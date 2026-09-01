#!/usr/bin/env bash
# Read-only integrity check of a deployed portability-server.
#
# Compares the running deployment against the version-controlled sources and
# reports anything that differs. Writes nothing: no config is installed and no
# service is touched. Exits 0 when everything matches and 1 when a check fails,
# so it can also be run from cron.
#
# Run it as the account used for deployment: the systemd units are rendered
# with that account name, and the installed configs under /etc must be
# readable.

# Deliberately no -e: every check runs and the findings are collected.
set -uo pipefail

# Keep a copy of everything reported, so the run can be mailed afterwards.
REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT
exec > >(tee "$REPORT") 2>&1

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_env_get() { sed -n "s/^$1=//p" "$REPO_DIR/.env" 2>/dev/null | tail -1 | tr -d "\"'"; }

APP_DIR="${APP_DIR:-$(_env_get APP_DIR)}"
APP_DIR="${APP_DIR:-$REPO_DIR}"
VENV="${VENV:-$(_env_get VENV_PATH)}"
VENV="${VENV:-$APP_DIR/venv}"
SERVICES="portability-gunicorn portability-celery-worker portability-celery-beat"
RUN_USER="${RUN_USER:-$(id -un)}"
DOMAINS="${DOMAINS:-$(_env_get DOMAINS)}"   # comma-separated; see .env.example
INSTALL_CONFIGS=no   # lib.sh only reports differences in this mode

# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

cd "$APP_DIR" || exit 1

FINDINGS=0
fail() { echo "FAIL: $*" >&2; FINDINGS=$((FINDINGS + 1)); }
pass() { echo "ok:   $*"; }
note() { echo "note: $*"; }
_keys() { grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | tr -d '=' | sort -u; }

echo "==> Application tree"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail "$APP_DIR is not a git checkout, so the application tree has nothing to compare against"
else
    note "HEAD $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
    modified="$(git status --porcelain --untracked-files=no)"
    if [ -n "$modified" ]; then
        fail "tracked files differ from HEAD:"
        echo "$modified" >&2
        git diff --stat >&2
    else
        pass "tracked files match HEAD"
    fi
fi

echo "==> Environment file"
if [ ! -f "$APP_DIR/.env" ]; then
    fail ".env is missing"
else
    mode="$(stat -c %a "$APP_DIR/.env")"
    case "$mode" in
        600|400) pass ".env is owner-only (mode $mode)" ;;
        *) fail ".env is readable beyond its owner (mode $mode); it holds the OAuth secrets and the encryption key" ;;
    esac
    # .env is not version-controlled, so only its set of keys can be compared
    # against the template; the values themselves have no baseline.
    missing="$(comm -23 <(_keys "$APP_DIR/.env.example") <(_keys "$APP_DIR/.env") | tr '\n' ' ')"
    extra="$(comm -13 <(_keys "$APP_DIR/.env.example") <(_keys "$APP_DIR/.env") | tr '\n' ' ')"
    if [ -n "${missing// /}" ]; then
        note "keys in .env.example but not in .env: $missing"
    fi
    if [ -n "${extra// /}" ]; then
        note "keys in .env but not in .env.example: $extra"
    fi
fi

echo "==> Database"
db_info="$("$VENV/bin/python" - <<'PYEOF' 2>/dev/null
import environ
env = environ.Env()
environ.Env.read_env(".env")
db = env.db()
print("DB_HOST='%s'" % (db.get("HOST") or ""))
print("DB_NAME='%s'" % (db.get("NAME") or ""))
PYEOF
)"
if [ -z "$db_info" ]; then
    fail "could not read DATABASE_URL from .env"
else
    eval "$db_info"
    case "$DB_HOST" in
        ""|localhost|127.0.0.1|::1|/*)
            # A local database is part of this machine, so it can be checked.
            if timeout 15 "$VENV/bin/python" - <<'PYEOF' >/dev/null 2>&1
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portability_server.settings")
import django
django.setup()
from django.db import connection
connection.ensure_connection()
PYEOF
            then
                pass "local database '$DB_NAME' is present and reachable"
            else
                fail "local database '$DB_NAME' could not be reached"
            fi
            ;;
        *)
            note "DATABASE_URL points at '$DB_HOST'; a remote database is entered by hand and has no version-controlled reference, so it is not checked here"
            ;;
    esac
fi

if validate_env; then
    pass "environment validation"
else
    fail "environment validation"
fi

echo "==> Django deployment checks"
# --fail-level WARNING: check --deploy exits 0 on warnings by default, which
# would report a pass while printing security warnings.
if "$VENV/bin/python" manage.py check --deploy --fail-level WARNING; then
    pass "manage.py check --deploy reported no issues"
else
    fail "manage.py check --deploy reported issues (listed above)"
fi

echo "==> Installed configuration"
render_services
install_nginx
if [ "$NGINX_CHECKED" -eq 0 ]; then
    fail "the nginx configuration could not be compared and is unverified"
fi
if [ "$CERT_PROBLEMS" -gt 0 ]; then
    fail "$CERT_PROBLEMS TLS certificate(s) referenced by the nginx config are missing or unreadable"
fi
if [ "$DRIFT_COUNT" -gt 0 ]; then
    fail "$DRIFT_COUNT installed config file(s) differ from the version-controlled source (diffs above)"
elif [ "$NGINX_CHECKED" -eq 1 ]; then
    pass "installed systemd and nginx configs match the version-controlled source"
fi

check_clamd_settings
if [ "$CLAMD_PROBLEMS" -gt 0 ]; then
    fail "$CLAMD_PROBLEMS virus scanner setting(s) differ from what the service requires"
else
    pass "virus scanner settings"
fi

echo "==> Disk"
df -h / "$APP_DIR" 2>/dev/null | awk 'NR==1 || !seen[$0]++'
for filesystem in / "$APP_DIR"; do
    use="$(df --output=pcent "$filesystem" 2>/dev/null | tail -1 | tr -dc '0-9')"
    if [ -n "$use" ] && [ "$use" -ge 90 ]; then
        fail "$filesystem is ${use}% full"
    fi
done

echo "==> Services"
for service in $SERVICES; do
    if systemctl is-active --quiet "$service"; then
        pass "$service is active"
    else
        fail "$service is not active"
    fi
done

# Mails the report when VERIFY_EMAIL is set. Cron then only needs to run the
# script; a quiet run still confirms the check happened.
_mail_report() {
    local status="$1"
    [ -n "${VERIFY_EMAIL:-}" ] || return 0
    command -v mail >/dev/null 2>&1 || return 0
    local subject="portability-server verify: OK"
    [ "$status" -eq 0 ] || subject="portability-server verify: $FINDINGS problem(s)"
    mail -s "$subject on $(hostname -s)" "$VERIFY_EMAIL" < "$REPORT"
}

echo
if [ "$FINDINGS" -eq 0 ]; then
    echo "Integrity check passed: the deployment matches version control."
    _mail_report 0
    exit 0
fi
echo "Integrity check found $FINDINGS problem(s); see the output above." >&2
_mail_report 1
exit 1
