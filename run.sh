#!/usr/bin/env bash
# Always use project venv — never /usr/bin/uvicorn
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "ERROR: .venv not ready. Run first:"
  echo "  ./scripts/setup.sh"
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8100}"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
