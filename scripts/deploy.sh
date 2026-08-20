#!/usr/bin/env bash
# First-time deployment of portability-server.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_env_get() { sed -n "s/^$1=//p" "$REPO_DIR/.env" 2>/dev/null | tail -1 | tr -d "\"'"; }

APP_DIR="${APP_DIR:-$(_env_get APP_DIR)}"
APP_DIR="${APP_DIR:-$REPO_DIR}"
VENV="${VENV:-$(_env_get VENV_PATH)}"
VENV="${VENV:-$APP_DIR/venv}"
SERVICES="${SERVICES:-$(_env_get SERVICES)}"
SERVICES="${SERVICES:-portability-gunicorn portability-celery-worker portability-celery-beat}"
RUN_USER="${RUN_USER:-$(id -un)}"
SETUP_DB="${SETUP_DB:-auto}"        # auto | yes | no — database provisioning
DB_ADMIN_USER="${DB_ADMIN_USER:-postgres}"
INSTALL_CONFIGS="${INSTALL_CONFIGS:-yes}"  # first-time setup always installs the rendered configs

# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3 python3.12-venv postgresql nginx-extras redis-server clamav clamav-daemon

cd "$APP_DIR"

echo "==> Setting up Python virtual environment"
if [ ! -d "$VENV" ]; then
    python3 -m venv venv
fi
"$VENV/bin/pip" install -r requirements.txt

if [ ! -f .env ]; then
    echo "==> Creating .env from .env.example"
    cp .env.example .env
    cat <<'MSG'
Created .env from .env.example. Fill in the per-deployment values before continuing:
  - DATABASE_URL
  - SECRET_KEY
  - ALLOWED_HOSTS
  - CSRF_TRUSTED_ORIGINS
  - OAuth credentials (GOOGLE_OAUTH_CLIENT_ID/SECRET, TIKTOK_CLIENT_KEY/SECRET, etc.)
  - DEBUG=False
Then rerun this script.
MSG
    exit 1
fi

echo "==> Database provisioning (SETUP_DB=$SETUP_DB)"
if [ "$SETUP_DB" = "no" ]; then
    echo "Skipping database provisioning (SETUP_DB=no)"
else
    eval "$("$VENV/bin/python" - <<'PYEOF'
import environ
env = environ.Env(); environ.Env.read_env(".env")
db = env.db()
print(f"DB_NAME='{db['NAME']}'\nDB_USER='{db['USER']}'\nDB_PASSWORD='{db['PASSWORD']}'\nDB_HOST='{db['HOST']}'")
PYEOF
)"

    if { [ -z "$DB_HOST" ] || [ "$DB_HOST" = "localhost" ] || [ "$DB_HOST" = "127.0.0.1" ]; } \
        && sudo -u postgres psql -c 'select 1' >/dev/null 2>&1; then
        PSQL_ADMIN=(sudo -u postgres psql)
    else
        read -rs -p "PostgreSQL admin password for $DB_ADMIN_USER@${DB_HOST:-localhost}: " DB_ADMIN_PASSWORD
        echo
        PSQL_ADMIN=(env PGPASSWORD="$DB_ADMIN_PASSWORD" psql -h "${DB_HOST:-localhost}" -U "$DB_ADMIN_USER")
    fi

    if ! "${PSQL_ADMIN[@]}" -c 'select 1' >/dev/null 2>&1; then
        if [ "$SETUP_DB" = "yes" ]; then
            unset DB_ADMIN_PASSWORD
            echo "Cannot reach PostgreSQL as admin." >&2
            exit 1
        else
            echo "Warning: cannot reach PostgreSQL as admin; skipping provisioning (provision manually or rerun with correct credentials)" >&2
            unset DB_ADMIN_PASSWORD
        fi
    else
        # On a fresh install the admin role has no password (peer auth only);
        # set one so the instance is not left with a passwordless superuser.
        if "${PSQL_ADMIN[@]}" -tc "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='$DB_ADMIN_USER'" 2>/dev/null | grep -q t; then
            while true; do
                read -rs -p "Set a new PostgreSQL admin password for '$DB_ADMIN_USER': " NEW_ADMIN_PW; echo
                read -rs -p "Confirm password: " NEW_ADMIN_PW2; echo
                [ -n "$NEW_ADMIN_PW" ] && [ "$NEW_ADMIN_PW" = "$NEW_ADMIN_PW2" ] && break
                echo "Passwords empty or do not match; try again." >&2
            done
            "${PSQL_ADMIN[@]}" -c "ALTER ROLE \"$DB_ADMIN_USER\" PASSWORD '$NEW_ADMIN_PW'" >/dev/null
            unset NEW_ADMIN_PW NEW_ADMIN_PW2
            echo "Admin password set."
        fi
        "${PSQL_ADMIN[@]}" -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
            || "${PSQL_ADMIN[@]}" -c "CREATE ROLE \"$DB_USER\" LOGIN PASSWORD '$DB_PASSWORD'"
        "${PSQL_ADMIN[@]}" -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
            || "${PSQL_ADMIN[@]}" -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
        unset DB_ADMIN_PASSWORD
    fi
fi

validate_env

echo "==> Running migrations and collecting static files"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput

render_services
echo "==> Enabling and starting services"
sudo systemctl enable --now $SERVICES
verify_services_active

install_nginx

cat <<MSG
==> Deployment complete.
Next steps:
  - Create a researcher API token:
      $VENV/bin/python manage.py create_researcher_token
MSG
