#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.mhy.mom_index_web"
PLIST_SRC="$ROOT_DIR/deploy/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT_DIR/logs"
cp "$PLIST_SRC" "$PLIST_DST"

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true

# Stop only an existing web server that belongs to this checkout.
for pid in $(pgrep -f "scripts/web_server.py" 2>/dev/null || true); do
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  if [[ "$command" == *"scripts/web_server.py"* && "$process_cwd" == "$ROOT_DIR" ]]; then
    kill "$pid"
  fi
done

for _ in {1..20}; do
  ! lsof -tiTCP:8081 -sTCP:LISTEN >/dev/null 2>&1 && break
  sleep 0.25
done

launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl kickstart -k "$DOMAIN/$LABEL"

for _ in {1..30}; do
  if curl -fsS --max-time 5 "http://127.0.0.1:8081/api/dashboard-data" >/dev/null 2>&1; then
    echo "Installed: $PLIST_DST"
    echo "Web service: http://127.0.0.1:8081/dashboard.html"
    echo "Log: $ROOT_DIR/logs/web_server_launchd.log"
    exit 0
  fi
  sleep 0.5
done

echo "Web service failed its health check. Recent log:" >&2
tail -n 30 "$ROOT_DIR/logs/web_server_launchd.log" >&2 || true
exit 1
