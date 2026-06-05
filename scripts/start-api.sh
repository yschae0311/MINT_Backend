#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .venv/bin/uvicorn ]]; then
  echo "venv not found. Run: ./scripts/setup.sh"
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8100}"

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
