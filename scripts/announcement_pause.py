"""Bounded pause for known routine announcements, never unknown safety alerts."""
import json
from datetime import datetime, timezone
from pathlib import Path

PATH = Path.home() / "Library/Application Support/SmartHomeMonitor/data/announcement_pause.json"
ROUTINE = {"washer", "dryer", "combo", "energy_high_on", "energy_high_clear", "energy_ok_off", "bubbler_on", "household_reminder"}


def paused(identifier, state, now):
    if identifier not in ROUTINE and not identifier.startswith("calendar-"):
        return False
    try:
        start = float(state["startedAt"])
        end = float(state["until"])
        return start <= now < end <= start + 3600
    except (KeyError, ValueError, TypeError):
        return False


def active(identifier):
    try:
        return paused(identifier, json.loads(PATH.read_text()), datetime.now(timezone.utc).timestamp())
    except (OSError, ValueError):
        return False


def hold():
    now = datetime.now(timezone.utc).timestamp()
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"startedAt": now, "until": now + 3600}))
