#!/usr/bin/env bash
# Bootstraps the EC2 box: installs nginx + python, creates a service user,
# sets up the venv, installs the systemd service and nginx config, and
# starts everything. Run with sudo from a checkout of this repo:
#   sudo bash deploy/ec2_setup.sh
#
# Still need to create the .env file by hand after this runs - see the
# message printed at the end.
set -euo pipefail

APP_ROOT="/opt/pr-reviewer"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"  # repo root (this script lives in deploy/)
SVC_USER="prreviewer"

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3-venv python3-pip nginx

echo "==> Creating service user '$SVC_USER'"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

echo "==> Copying application to $APP_ROOT"
mkdir -p "$APP_ROOT"
cp -r "$REPO_DIR/fastapi-app" "$APP_ROOT/"

echo "==> Building virtualenv"
python3 -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/pip" install --upgrade pip
"$APP_ROOT/venv/bin/pip" install -r "$APP_ROOT/fastapi-app/requirements.txt"

echo "==> Setting ownership"
chown -R "$SVC_USER:$SVC_USER" "$APP_ROOT"

echo "==> Installing systemd unit"
cp "$REPO_DIR/deploy/pr-reviewer.service" /etc/systemd/system/pr-reviewer.service
systemctl daemon-reload
systemctl enable pr-reviewer

echo "==> Installing nginx site"
cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/sites-available/pr-reviewer
ln -sf /etc/nginx/sites-available/pr-reviewer /etc/nginx/sites-enabled/pr-reviewer
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

cat <<EOF

============================================================
Base setup complete.

NEXT (required before the service will run):
  1. Create the env file:
       sudo cp $REPO_DIR/.env.example $APP_ROOT/fastapi-app/.env
       sudo nano $APP_ROOT/fastapi-app/.env      # fill in real values
       sudo chown $SVC_USER:$SVC_USER $APP_ROOT/fastapi-app/.env
       sudo chmod 600 $APP_ROOT/fastapi-app/.env
  2. Start the service:
       sudo systemctl start pr-reviewer
       sudo systemctl status pr-reviewer
       journalctl -u pr-reviewer -f
  3. Verify locally:
       curl http://127.0.0.1:8000/health
  4. Open inbound :80 (and :443) in the EC2 security group.
  5. (Recommended) Add TLS:  sudo certbot --nginx -d your.domain.com
============================================================
EOF
