#!/usr/bin/env bash
# One-time setup: create venv and install dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON" ]]; then
  echo "Python not found. Install 3.11 or 3.12, e.g.: sudo apt install python3.12 python3.12-venv"
  exit 1
fi

VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
MAJOR="${VER%%.*}"
MINOR="${VER#*.}"
if [[ "$MAJOR" -lt 3 ]] || [[ "$MAJOR" -eq 3 && "$MINOR" -lt 11 ]]; then
  echo "Python $VER is too old. Use 3.11 or 3.12."
  exit 1
fi
if [[ "$MAJOR" -eq 3 && "$MINOR" -ge 14 ]]; then
  echo "WARNING: Python $VER is unsupported. Prefer 3.11 or 3.12 (google/grpc deps may break)."
fi

echo "Using $PYTHON ($VER)"
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps .

echo ""
echo "Done. Activate with: source .venv/bin/activate"
echo "Run API:           ./scripts/start-api.sh"
