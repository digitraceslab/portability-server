# Shared helpers for deploy.sh and update.sh.
# Expects APP_DIR, VENV, RUN_USER, SERVICES and INSTALL_CONFIGS to already be set by the caller.

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
    echo "Warning: $desc differs from the installed config at $target (or is not installed there)." >&2
    echo "Diff (installed vs rendered):" >&2
    diff -u "$target" "$src" 2>&1 >&2 || true
    echo "Set INSTALL_CONFIGS=yes to apply this change." >&2
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

install_nginx() {
    echo "==> Rendering nginx configuration"
    local domain ssl_cert ssl_key
    domain="${DOMAIN:-$("$VENV/bin/python" - <<'PYEOF'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portability_server.settings")
import django
django.setup()
from django.conf import settings
print(settings.ALLOWED_HOSTS[0])
PYEOF
)}"
    ssl_cert="${SSL_CERT:-/etc/letsencrypt/live/$domain/fullchain.pem}"
    ssl_key="${SSL_KEY:-/etc/letsencrypt/live/$domain/privkey.pem}"

    if [ ! -f "$ssl_cert" ]; then
        echo "Warning: TLS certificate not found at $ssl_cert. Provision certificates (e.g. via certbot) and rerun. Skipping nginx installation." >&2
        return
    fi

    local changed=0 tmp_site
    tmp_site="$(mktemp)"
    sed -e "s|@DOMAIN@|$domain|g" -e "s|@SSL_CERT@|$ssl_cert|g" -e "s|@SSL_KEY@|$ssl_key|g" -e "s|@APP_DIR@|$APP_DIR|g" \
        "$APP_DIR/deploy/nginx-site.conf" > "$tmp_site"
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
