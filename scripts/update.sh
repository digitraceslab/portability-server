#!/usr/bin/env bash
# Routine update of an already-deployed portability-server.
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="$APP_DIR/venv"
RUN_USER="${RUN_USER:-$(id -un)}"
SERVICES="${SERVICES:-portability-gunicorn portability-celery-worker portability-celery-beat}"
INSTALL_CONFIGS="${INSTALL_CONFIGS:-no}"  # yes = apply rendered systemd/nginx configs; no = report drift only

# shellcheck source=lib.sh
source "$(dirname "$(readlink -f "$0")")/lib.sh"

cd "$APP_DIR"

echo "==> Checking for local changes"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Local changes detected in $APP_DIR. Commit, stash or discard them before updating." >&2
    exit 1
fi

echo "==> Pulling latest changes"
git pull --ff-only

echo "==> Installing dependencies"
"$VENV/bin/pip" install -r requirements.txt

validate_env

echo "==> Running deployment checks"
"$VENV/bin/python" manage.py check --deploy || true

echo "==> Running migrations and collecting static files"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput

render_services
install_nginx

echo "==> Restarting services"
sudo systemctl restart $SERVICES
verify_services_active

echo "==> Update complete."
