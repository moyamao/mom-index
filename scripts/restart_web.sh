#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/web_server.log"
PID_FILE="$LOG_DIR/web_server.pid"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "错误: 未找到 $PYTHON_BIN，请先在 Mac mini 创建 .venv 并安装依赖。" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

PORT="$($PYTHON_BIN -c 'from runtime_config import ini_get_int; print(ini_get_int("web", "port", 8081))')"

stop_pid() {
  local pid="$1"
  [[ "$pid" == <-> ]] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  local command
  local process_cwd
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  if [[ "$command" != *"scripts/web_server.py"* || "$process_cwd" != "$ROOT_DIR" ]]; then
    echo "跳过非本项目进程 PID $pid: $command" >&2
    return 0
  fi
  echo "停止旧 Web 服务 PID $pid"
  kill "$pid"
  for _ in {1..20}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.25
  done
  echo "旧进程未及时退出，发送 TERM 后仍存活: PID $pid" >&2
  return 1
}

if [[ -f "$PID_FILE" ]]; then
  stop_pid "$(cat "$PID_FILE")"
fi

for pid in $(pgrep -f "scripts/web_server.py" 2>/dev/null || true); do
  stop_pid "$pid"
done
rm -f "$PID_FILE"

listener="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$listener" ]]; then
  echo "错误: 端口 $PORT 仍被其他程序占用，未启动 Web 服务:" >&2
  echo "$listener" >&2
  exit 1
fi

echo "启动 mom-index Web 服务，端口 $PORT"
nohup "$PYTHON_BIN" -u scripts/web_server.py >> "$LOG_FILE" 2>&1 &
pid=$!
echo "$pid" > "$PID_FILE"

for _ in {1..20}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "错误: Web 服务启动后立即退出。最近日志:" >&2
    tail -n 30 "$LOG_FILE" >&2
    exit 1
  fi
  if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/dashboard-data" >/dev/null; then
    echo "Web 服务已启动: http://0.0.0.0:$PORT/dashboard.html"
    echo "PID: $pid"
    echo "日志: $LOG_FILE"
    exit 0
  fi
  sleep 0.5
done

echo "错误: Web 服务未在预期时间内通过健康检查。最近日志:" >&2
tail -n 30 "$LOG_FILE" >&2
exit 1
