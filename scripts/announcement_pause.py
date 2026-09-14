"""Bounded pause for known routine announcements, never unknown safety alerts."""
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

PATH = Path.home() / "Library/Application Support/SmartHomeMonitor/data/announcement_pause.json"
ROUTINE = {"washer", "dryer", "combo", "energy_high_on", "energy_high_clear", "energy_ok_off", "bubbler_on", "household_reminder"}


def paused(identifier, state, now):
    if identifier not in ROUTINE and not identifier.startswith("calendar-"):
        return False
    try:
        start = float(state["startedAt"])
        end = float(state["until"])
        limit = 90000 if state.get("mode") == "morning" else 3600
        if state.get("mode") == "morning" and end != morning_end(start):
            return False
        return start <= now < end <= start + limit
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


def morning_end(now):
    local = datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles"))
    end = local.replace(hour=8, minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end.timestamp()


def quiet_until_morning():
    now = datetime.now(timezone.utc).timestamp()
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"startedAt": now, "until": morning_end(now), "mode": "morning"}))
