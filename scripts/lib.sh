# Shared helpers for deploy.sh, update.sh and verify.sh.
# Expects APP_DIR, VENV, RUN_USER, SERVICES and INSTALL_CONFIGS to already be set by the caller.
# install_credentials() and check_credentials() also expect the caller to
# define _env_get() (deploy.sh, update.sh and verify.sh all do).

# Number of installed configs found to differ from the version-controlled
# source. Only meaningful when INSTALL_CONFIGS=no; verify.sh reads it.
DRIFT_COUNT=${DRIFT_COUNT:-0}

# Certificates referenced by the rendered nginx config that are missing or
# unreadable, and whether the nginx comparison actually ran. verify.sh reads
# both: a check that could not be performed must never be reported as a pass.
CERT_PROBLEMS=${CERT_PROBLEMS:-0}

# Scanner settings that differ from what this service requires.
CLAMD_PROBLEMS=${CLAMD_PROBLEMS:-0}
NGINX_CHECKED=${NGINX_CHECKED:-0}

# Root-delivered credentials (OpenBao AppRole ids, or the local key) that are
# missing, wrongly owned or wrongly permissioned. Only meaningful after
# check_credentials has run; verify.sh reads it.
CRED_PROBLEMS=${CRED_PROBLEMS:-0}

CREDSTORE=/etc/credstore

_openbao_local_key_warning() {
    cat >&2 <<'WARN'
####################################################################
# WARNING: no OPENBAO_ADDR is configured. The encryption key is
# held locally on this host (root-only file under /etc/credstore),
# not isolated in a separate vault. This does not meet the
# key-isolation requirement.
#
# Configure OPENBAO_ADDR and the OpenBao AppRole credentials; see
# README.md, "Key management with OpenBao".
####################################################################
WARN
}

validate_env() {
    echo "==> Validating environment configuration"
    "$VENV/bin/python" - <<'PYEOF'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portability_server.settings")
import django
django.setup()
from django.conf import settings
problems = []
if settings.DEBUG:
    problems.append("DEBUG must be False in production")
if not settings.ALLOWED_HOSTS:
    problems.append("ALLOWED_HOSTS is empty")
if settings.SECRET_KEY == "change-me-to-a-random-secret-key":
    problems.append("SECRET_KEY is still the .env.example placeholder")
if not getattr(settings, "CSRF_TRUSTED_ORIGINS", None):
    problems.append("CSRF_TRUSTED_ORIGINS is empty")
if problems:
    raise SystemExit("Environment validation failed:\n  - " + "\n  - ".join(problems))
print("Environment validation OK")
PYEOF

    local openbao_addr
    openbao_addr="$(_env_get OPENBAO_ADDR)"
    if [ -n "$openbao_addr" ]; then
        local openbao_cacert curl_opts=() code
        openbao_cacert="$(_env_get OPENBAO_CACERT)"
        [ -n "$openbao_cacert" ] && curl_opts+=(--cacert "$openbao_cacert")
        code="$(curl -sS --max-time 10 "${curl_opts[@]}" -o /dev/null -w '%{http_code}' "$openbao_addr/v1/sys/health" || true)"
        case "$code" in
            200|429|472|473)
                echo "OpenBao at $openbao_addr is reachable (HTTP $code)"
                ;;
            503)
                echo "OpenBao at $openbao_addr is sealed (HTTP 503)" >&2
                return 1
                ;;
            *)
                echo "OpenBao at $openbao_addr did not respond as expected (HTTP ${code:-none})" >&2
                return 1
                ;;
        esac
    fi
}

# Ensures the root-only credential files the systemd units import
# (ImportCredential=portability.*) are in place: the OpenBao AppRole ids when
# OPENBAO_ADDR is configured, otherwise a locally held Fernet key. Prompts
# interactively for anything missing; never prints a secret value.
install_credentials() {
    echo "==> Installing credentials"
    sudo install -d -m 0700 -o root -g root "$CREDSTORE"

    local openbao_addr
    openbao_addr="$(_env_get OPENBAO_ADDR)"

    if [ -n "$openbao_addr" ]; then
        local name prompt value tmp
        for name in openbao_role_id openbao_secret_id; do
            if sudo test -f "$CREDSTORE/portability.$name"; then
                continue
            fi
            case "$name" in
                openbao_role_id) prompt="OpenBao role id: " ;;
                openbao_secret_id) prompt="OpenBao secret id: " ;;
            esac
            read -rs -p "$prompt" value
            echo
            tmp="$(mktemp)"
            chmod 0600 "$tmp"
            printf '%s' "$value" > "$tmp"
            sudo install -m 0400 -o root -g root "$tmp" "$CREDSTORE/portability.$name"
            rm -f "$tmp"
            unset value
            echo "$name stored in $CREDSTORE/portability.$name (from the OpenBao AppRole; see README, \"Key management with OpenBao\")"
        done
    else
        if ! sudo test -f "$CREDSTORE/portability.encryption_key"; then
            local env_key tmp
            env_key="$(_env_get ENCRYPTION_KEY)"
            tmp="$(mktemp)"
            chmod 0600 "$tmp"
            if [ -n "$env_key" ]; then
                printf '%s' "$env_key" > "$tmp"
                sudo install -m 0400 -o root -g root "$tmp" "$CREDSTORE/portability.encryption_key"
                sed -i '/^ENCRYPTION_KEY=/d' "$REPO_DIR/.env"
                echo "moved ENCRYPTION_KEY out of .env into $CREDSTORE/portability.encryption_key"
            else
                "$VENV/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$tmp"
                sudo install -m 0400 -o root -g root "$tmp" "$CREDSTORE/portability.encryption_key"
                echo "generated a new key at $CREDSTORE/portability.encryption_key"
            fi
            rm -f "$tmp"
            unset env_key
        elif grep -q '^ENCRYPTION_KEY=' "$REPO_DIR/.env" 2>/dev/null; then
            sed -i '/^ENCRYPTION_KEY=/d' "$REPO_DIR/.env"
            echo "removed leftover ENCRYPTION_KEY line from .env ($CREDSTORE/portability.encryption_key is already in place)"
        fi
        _openbao_local_key_warning
    fi
}

# Read-only counterpart of install_credentials, used by verify.sh. Reports
# findings via CRED_PROBLEMS; prints nothing secret.
check_credentials() {
    local owner_mode

    if sudo -n test -d "$CREDSTORE" 2>/dev/null; then
        owner_mode="$(sudo -n stat -c '%U %a' "$CREDSTORE" 2>/dev/null)"
        if [ "$owner_mode" = "root 700" ]; then
            echo "$CREDSTORE is present, root-owned, mode 700"
        else
            echo "Warning: $CREDSTORE is ${owner_mode:-in an unexpected state}, expected 'root 700'" >&2
            CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
        fi
    elif sudo -n true 2>/dev/null; then
        echo "Warning: $CREDSTORE does not exist" >&2
        CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
    else
        echo "Warning: cannot verify $CREDSTORE (needs root)" >&2
        CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
    fi

    local openbao_addr expected name path
    openbao_addr="$(_env_get OPENBAO_ADDR)"
    if [ -n "$openbao_addr" ]; then
        expected="portability.openbao_role_id portability.openbao_secret_id"
    else
        expected="portability.encryption_key"
    fi
    for name in $expected; do
        path="$CREDSTORE/$name"
        if sudo -n test -f "$path" 2>/dev/null; then
            owner_mode="$(sudo -n stat -c '%U %a' "$path" 2>/dev/null)"
            if [ "$owner_mode" = "root 400" ]; then
                echo "$name is present, root-owned, mode 400"
            else
                echo "Warning: $name is ${owner_mode:-in an unexpected state}, expected 'root 400'" >&2
                CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
            fi
        elif sudo -n true 2>/dev/null; then
            echo "Warning: $name is missing from $CREDSTORE" >&2
            CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
        else
            echo "Warning: cannot verify $name (needs root)" >&2
            CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
        fi
    done

    if grep -qE '^ENCRYPTION_KEY=.+' "$REPO_DIR/.env" 2>/dev/null; then
        echo "Warning: .env still contains an ENCRYPTION_KEY value; it must live only in $CREDSTORE" >&2
        CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
    fi

    if [ -z "$openbao_addr" ]; then
        _openbao_local_key_warning
        CRED_PROBLEMS=$((CRED_PROBLEMS + 1))
    fi
}

# Compares a rendered config against the installed one; applies it when
# INSTALL_CONFIGS=yes, otherwise reports the diff. Returns 0 if it changed.
_apply_or_report() {
    local src="$1" target="$2" desc="$3"
    if [ -f "$target" ] && diff -q "$src" "$target" >/dev/null 2>&1; then
        echo "$desc: config up to date"
        return 1
    fi
    if [ "$INSTALL_CONFIGS" = "yes" ]; then
        sudo install -m 0644 "$src" "$target"
        echo "$desc: installed"
        return 0
    fi
    # Indentation and blank lines are not significant to nginx or systemd, so a
    # file that differs only in those was reformatted by an editor rather than
    # changed. Report it, but do not count it as drift.
    if [ -f "$target" ] && diff -q -w -B "$src" "$target" >/dev/null 2>&1; then
        echo "$desc: differs from $target in whitespace only (reformatted in place; no change in meaning)"
        return 1
    fi
    echo "Warning: $desc differs from the installed config at $target (or is not installed there)." >&2
    echo "Diff (installed vs rendered, ignoring whitespace and blank lines):" >&2
    diff -u -w -B "$target" "$src" 2>&1 >&2 || true
    echo "Set INSTALL_CONFIGS=yes to apply this change." >&2
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
    return 1
}

render_services() {
    echo "==> Rendering systemd unit files"
    local changed=0
    local unit name tmp
    for unit in "$APP_DIR"/deploy/*.service; do
        name="$(basename "$unit")"
        tmp="$(mktemp)"
        sed -e "s|@RUN_USER@|$RUN_USER|g" -e "s|@APP_DIR@|$APP_DIR|g" "$unit" > "$tmp"
        if _apply_or_report "$tmp" "/etc/systemd/system/$name" "$name"; then
            changed=1
        fi
        rm -f "$tmp"
    done
    if [ "$changed" -eq 1 ]; then
        sudo systemctl daemon-reload
    fi
}

verify_services_active() {
    echo "==> Verifying services are active"
    local failed=()
    for service in $SERVICES; do
        if ! systemctl is-active --quiet "$service"; then
            failed+=("$service")
        fi
    done
    if [ "${#failed[@]}" -ne 0 ]; then
        echo "The following services are not active: ${failed[*]}" >&2
        exit 1
    fi
}

# Certificates normally live under /etc/letsencrypt, which is readable only by
# root, so an unprivileged test cannot tell "absent" from "unreadable". Retry
# with non-interactive sudo before calling one missing.
# Returns 0 present, 1 definitely absent, 2 undetermined.
_cert_present() {
    local path="$1"
    [ -f "$path" ] && return 0
    sudo -n test -f "$path" 2>/dev/null && return 0
    sudo -n true 2>/dev/null || return 2
    return 1
}

install_nginx() {
    echo "==> Rendering nginx configuration"
    local domains domain ssl_cert ssl_key
    # DOMAINS lists the names to serve, comma-separated, one pair of server
    # blocks each. DOMAIN remains accepted for a single name, and both fall
    # back to the first entry of ALLOWED_HOSTS.
    domains="${DOMAINS:-${DOMAIN:-$("$VENV/bin/python" - <<'PYEOF'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portability_server.settings")
import django
django.setup()
from django.conf import settings
print(settings.ALLOWED_HOSTS[0])
PYEOF
)}}"
    domains="${domains//,/ }"

    local changed=0 missing_certs=0 cert_state tmp_site
    tmp_site="$(mktemp)"
    # Certificate paths are substituted into the template as text, so a missing
    # certificate does not stop the config being rendered and compared. It only
    # stops it being installed: nginx would refuse to load it.
    for domain in $domains; do
        ssl_cert="${SSL_CERT:-/etc/letsencrypt/live/$domain/fullchain.pem}"
        ssl_key="${SSL_KEY:-/etc/letsencrypt/live/$domain/privkey.pem}"
        cert_state=0
        _cert_present "$ssl_cert" || cert_state=$?
        if [ "$cert_state" -eq 1 ]; then
            echo "Warning: TLS certificate for $domain is missing at $ssl_cert." >&2
            missing_certs=$((missing_certs + 1))
            CERT_PROBLEMS=$((CERT_PROBLEMS + 1))
        elif [ "$cert_state" -eq 2 ]; then
            # Unverifiable is not the same as absent: report it, but do not
            # block a deployment over a file this account cannot read.
            echo "Warning: cannot verify the TLS certificate for $domain at $ssl_cert (needs root, and passwordless sudo is unavailable)." >&2
            CERT_PROBLEMS=$((CERT_PROBLEMS + 1))
        fi
        sed -e "s|@DOMAIN@|$domain|g" -e "s|@SSL_CERT@|$ssl_cert|g" -e "s|@SSL_KEY@|$ssl_key|g" -e "s|@APP_DIR@|$APP_DIR|g" \
            "$APP_DIR/deploy/nginx-site.conf" >> "$tmp_site"
        echo >> "$tmp_site"
    done

    if [ "$missing_certs" -gt 0 ] && [ "$INSTALL_CONFIGS" = "yes" ]; then
        echo "Refusing to install an nginx config that references $missing_certs missing certificate(s). Provision them (e.g. via certbot) and rerun." >&2
        rm -f "$tmp_site"
        return
    fi

    NGINX_CHECKED=1
    if _apply_or_report "$tmp_site" /etc/nginx/sites-available/portability-server "nginx site config"; then
        changed=1
    fi
    rm -f "$tmp_site"

    if _apply_or_report "$APP_DIR/deploy/nginx-ratelimit.conf" /etc/nginx/conf.d/portability-ratelimit.conf "nginx ratelimit config"; then
        changed=1
    fi

    if [ "$INSTALL_CONFIGS" = "yes" ]; then
        sudo ln -sf /etc/nginx/sites-available/portability-server /etc/nginx/sites-enabled/portability-server
    fi

    if [ "$changed" -eq 1 ]; then
        sudo nginx -t
        sudo systemctl reload nginx
    fi
}

CLAMD_CONF="${CLAMD_CONF:-/etc/clamav/clamd.conf}"

# Required scanner settings, as "key value" pairs.
_clamd_required() {
    grep -vE '^\s*(#|$)' "$APP_DIR/deploy/clamd-settings.conf"
}

# What clamd reports as being in effect. Sizes come back in bytes and unset
# options as "disabled", which is normalised to 0/no for comparison.
_clamd_effective() {
    local key="$1" line
    line="$(clamconf 2>/dev/null | grep -E "^${key} " | head -1)"
    case "$line" in
        "") echo "" ;;
        *disabled*) echo "disabled" ;;
        *) echo "$line" | sed 's/.*= "//; s/"$//' ;;
    esac
}

_clamd_matches() {
    local want="$1" have="$2"
    [ "$want" = "$have" ] && return 0
    # unlimited and off are both reported as "disabled"
    { [ "$want" = "0" ] || [ "$want" = "no" ]; } && [ "$have" = "disabled" ] && return 0
    return 1
}

# Compares clamd's effective settings against the required ones. Applies them
# when INSTALL_CONFIGS=yes, otherwise reports. Sets CLAMD_PROBLEMS.
check_clamd_settings() {
    echo "==> Virus scanner settings"
    if ! command -v clamconf >/dev/null 2>&1; then
        echo "Warning: clamconf not available; scanner settings not verified." >&2
        CLAMD_PROBLEMS=$((CLAMD_PROBLEMS + 1))
        return
    fi

    local changed=0 key want have
    while read -r key want; do
        [ -n "$key" ] || continue
        have="$(_clamd_effective "$key")"
        if _clamd_matches "$want" "$have"; then
            continue
        fi
        if [ "$INSTALL_CONFIGS" = "yes" ]; then
            sudo sed -i "/^${key}[[:space:]]/d" "$CLAMD_CONF"
            echo "$key $want" | sudo tee -a "$CLAMD_CONF" >/dev/null
            echo "$key: set to $want (was ${have:-unset})"
            changed=1
        else
            echo "Warning: $key is ${have:-unset}, expected $want." >&2
            CLAMD_PROBLEMS=$((CLAMD_PROBLEMS + 1))
        fi
    done < <(_clamd_required)

    if [ "$changed" -eq 1 ]; then
        sudo systemctl restart clamav-daemon
    elif [ "$CLAMD_PROBLEMS" -eq 0 ]; then
        echo "scanner settings as required"
    fi
}
