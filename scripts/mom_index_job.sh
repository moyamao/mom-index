#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="logs/mom_index_job_${TIMESTAMP}.log"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] mom-index job start"
  echo "root_dir=$ROOT_DIR"

  if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
    PYTHON_BIN="python"
  else
    PYTHON_BIN="python3"
  fi

  export PYTHONDONTWRITEBYTECODE=1

  # 管道交给 tee 后，显式关闭 Python 输出缓冲，终端可实时看到采集进度。
  "$PYTHON_BIN" -u pipeline.py
} 2>&1 | tee "$LOG_FILE"
