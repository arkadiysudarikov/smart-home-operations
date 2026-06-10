#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$HOME/Library/Application Support/SmartHomeMonitor"
PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-monitor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-monitor.plist"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$RUNTIME"
mkdir -p "$RUNTIME/logs"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'data' \
  --exclude 'logs' \
  --exclude 'reports' \
  "$ROOT/" "$RUNTIME/"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootout "gui/$(id -u)" "$PLIST_DST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-monitor"
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-monitor" | sed -n '1,120p'
