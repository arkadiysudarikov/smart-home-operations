#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$HOME/Library/Application Support/SmartHomeMonitor"
PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-monitor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-monitor.plist"
ACTIONS_PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-actions.plist"
ACTIONS_PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-actions.plist"

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
cp "$ACTIONS_PLIST_SRC" "$ACTIONS_PLIST_DST"
launchctl bootout "gui/$(id -u)" "$PLIST_DST" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$ACTIONS_PLIST_DST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl bootstrap "gui/$(id -u)" "$ACTIONS_PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-monitor"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-actions"
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-monitor" | sed -n '1,120p'
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-actions" | sed -n '1,120p'
