#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.mhy.mom_index_27b"
SOURCE="$ROOT_DIR/deploy/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

chmod +x "$ROOT_DIR/scripts/run_27b_daily_job.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT_DIR/logs"
cp "$SOURCE" "$TARGET"
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$TARGET"

echo "Installed: $TARGET"
echo "Schedule: daily at 02:30"
echo "Check: launchctl print $DOMAIN/$LABEL"
