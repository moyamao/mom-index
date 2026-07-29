#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$ROOT_DIR/deploy/com.mhy.mom_index.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.mhy.mom_index.plist"

chmod +x "$ROOT_DIR/scripts/mom_index_job.sh"
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$ROOT_DIR/logs"
cp "$PLIST_SRC" "$PLIST_DST"

launchctl unload "$PLIST_DST" >/dev/null 2>&1 || true
launchctl load "$PLIST_DST"

echo "Installed: $PLIST_DST"
echo "Check: launchctl list | grep mom_index"
