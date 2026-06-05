#!/usr/bin/env bash
# Install MINT systemd units. Run on EC2 after ./scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${APP_DIR:-$ROOT}"
SVC_USER="${SVC_USER:-ubuntu}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

if [[ ! -x "$APP_DIR/.venv/bin/uvicorn" ]]; then
  echo "ERROR: venv missing at $APP_DIR/.venv"
  echo "Run: cd $APP_DIR && PYTHON=python3.12 ./scripts/setup.sh"
  exit 1
fi

if ! "$APP_DIR/.venv/bin/python" -c "import fastapi" 2>/dev/null; then
  echo "ERROR: fastapi not installed in $APP_DIR/.venv"
  exit 1
fi

install_unit() {
  local name="$1"
  sed \
    -e "s|/home/ubuntu/MINT_Backend|$APP_DIR|g" \
    -e "s|User=ubuntu|User=$SVC_USER|g" \
    -e "s|Group=ubuntu|Group=$SVC_USER|g" \
    "$ROOT/deploy/${name}.service" | sudo tee "$SYSTEMD_DIR/${name}.service" >/dev/null
  echo "  installed $SYSTEMD_DIR/${name}.service"
}

echo "APP_DIR=$APP_DIR  SVC_USER=$SVC_USER"
install_unit mint-api
install_unit mint-celery-worker
install_unit mint-celery-beat

sudo systemctl daemon-reload
sudo systemctl enable mint-api mint-celery-worker mint-celery-beat

echo ""
echo "Start services:"
echo "  sudo systemctl start mint-api"
echo "  sudo systemctl start mint-celery-worker mint-celery-beat   # needs REDIS_URL in .env"
echo ""
echo "Check:"
echo "  sudo systemctl status mint-api"
echo "  journalctl -u mint-api -f"
echo "  curl http://localhost:8100/api/v1/health"
