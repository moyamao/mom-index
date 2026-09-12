#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
mkdir -p "$ROOT_DIR/logs"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

exec caffeinate -dimsu "$PYTHON" -u "$ROOT_DIR/scripts/run_27b_analysis.py" \
  --python "$PYTHON" \
  --days 14 \
  --limit 5000
