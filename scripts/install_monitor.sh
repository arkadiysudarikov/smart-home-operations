#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$HOME/Library/Application Support/SmartHomeMonitor"
PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-monitor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-monitor.plist"
ACTIONS_PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-actions.plist"
ACTIONS_PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-actions.plist"
DISPLAY_PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-display-awake.plist"
DISPLAY_PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-display-awake.plist"
DISPLAY_GUARD_PLIST_SRC="$ROOT/launchagents/com.arkadiy.smart-home-display-awake-guard.plist"
DISPLAY_GUARD_PLIST_DST="$HOME/Library/LaunchAgents/com.arkadiy.smart-home-display-awake-guard.plist"
DISPLAY_GUARD_RUNTIME="$RUNTIME/data/display_awake_policy_guard.py"

"$ROOT/scripts/check_install_source.sh" "$ROOT"

python3 "$ROOT/scripts/display_awake_policy_guard.py" --validate-source --source-root "$ROOT"

"$ROOT/scripts/check_install_source.sh" "$ROOT"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$RUNTIME"
mkdir -p "$RUNTIME/logs"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'data' \
  --exclude 'logs' \
  --exclude 'reports' \
  "$ROOT/" "$RUNTIME/"
mkdir -p "$RUNTIME/data"
cp "$ROOT/scripts/display_awake_policy_guard.py" "$DISPLAY_GUARD_RUNTIME"
chmod 700 "$DISPLAY_GUARD_RUNTIME"
python3 "$DISPLAY_GUARD_RUNTIME" --baseline --source-root "$ROOT" --runtime-root "$RUNTIME"
cp "$PLIST_SRC" "$PLIST_DST"
cp "$ACTIONS_PLIST_SRC" "$ACTIONS_PLIST_DST"
cp "$DISPLAY_PLIST_SRC" "$DISPLAY_PLIST_DST"
cp "$DISPLAY_GUARD_PLIST_SRC" "$DISPLAY_GUARD_PLIST_DST"
launchctl bootout "gui/$(id -u)" "$PLIST_DST" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$ACTIONS_PLIST_DST" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$DISPLAY_PLIST_DST" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$DISPLAY_GUARD_PLIST_DST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl bootstrap "gui/$(id -u)" "$ACTIONS_PLIST_DST"
launchctl bootstrap "gui/$(id -u)" "$DISPLAY_PLIST_DST"
launchctl bootstrap "gui/$(id -u)" "$DISPLAY_GUARD_PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-monitor"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-actions"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-display-awake"
launchctl kickstart -k "gui/$(id -u)/com.arkadiy.smart-home-display-awake-guard"
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-monitor" | sed -n '1,120p'
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-actions" | sed -n '1,120p'
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-display-awake" | sed -n '1,120p'
launchctl print "gui/$(id -u)/com.arkadiy.smart-home-display-awake-guard" | sed -n '1,120p'
