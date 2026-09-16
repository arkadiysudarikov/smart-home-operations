"""Repeat/snooze only recorded routine announcements; never read a mailbox."""
import argparse
import fcntl
import json
import re
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import generate_alerts as alerts
from announcement_envelope import encode
from announcement_pause import ROUTINE, active

LOCAL = ZoneInfo("America/Los_Angeles")


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def approved(event, now, max_age=600):
    try:
        identifier = event["announcementId"]
        if identifier not in ROUTINE and not re.fullmatch(r"calendar-[0-9a-f]{64}", identifier):
            return False
        encode(event["message"])
        if len(event["message"]) > 1400:
            return False
        return event.get("ok") is True and not event.get("skipped") and 0 <= now - stamp(event["at"]) <= max_age
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def recent(now):
    try:
        with (alerts.DATA_DIR / "homepod_announcement_events.jsonl").open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 262144))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and approved(event, now):
                return event
    except OSError:
        pass
    return None


def can_disclose(event, now):
    """A calendar replay requires the exact event and its owner's phone at home."""
    if not event["announcementId"].startswith("calendar-"):
        return True
    import calendar_announcements as cal
    try:
        config = json.loads(cal.CONFIG.read_text())
        snapshot = cal.invoke()
        checked = datetime.now(timezone.utc).timestamp()
        if not 0 <= checked - stamp(snapshot["generatedAt"]) <= 90:
            return False
        matching = [e for e in snapshot["events"] if "calendar-" + cal.occurrence(e) == event["announcementId"]]
        if len(matching) != 1:
            return False
        appointment = matching[0]
        owner = appointment["calendar"]
        mac = config.get("phones", {}).get(owner)
        return bool(owner in ("Arkadiy", "Maxim", "Jeanne") and mac
                    and appointment.get("status") != 3 and not appointment.get("allDay")
                    and stamp(appointment["start"]) > checked
                    and cal.is_home(cal.active_clients(), mac, datetime.now(timezone.utc).timestamp()))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        return False


def explain(event):
    identifier = event["announcementId"]
    reasons = {
        "washer": "That was a washer-cycle or venting alert from the laundry monitor.",
        "dryer": "That was a dryer-cycle alert from the laundry monitor.",
        "combo": "That was a combination-laundry-cycle alert from the laundry monitor.",
        "energy_high_on": "The verified Energy High indicator switched on.",
        "energy_high_clear": "Energy High cleared and Energy OK became active.",
        "energy_ok_off": "The verified Energy OK indicator switched off.",
        "bubbler_on": "The bubbler switched back on.",
        "household_reminder": "The household monitor issued that reminder. Its exact sensor evidence was not saved with the spoken message.",
    }
    if identifier.startswith("calendar-"):
        return "The calendar scheduler found an appointment reminder due and verified its owner's iPhone was home."
    return reasons[identifier]


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def request(mode, now=None, mutate=False):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    event = recent(now)
    if event is None:
        return "There is no recent approved announcement to " + ("explain." if mode == "why" else "repeat.")
    if mode == "why":
        return explain(event)
    if not can_disclose(event, now):
        return "I cannot repeat that appointment without current event details and its owner's phone at home."
    if mode == "repeat":
        return "Earlier announcement: " + event["message"]
    if mode != "snooze":
        raise ValueError("Unknown follow-up")
    if mutate:
        path = alerts.DATA_DIR / "announcement_followup.json"
        with (alerts.DATA_DIR / "announcement_followup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            save(path, {"requestedAt": now, "dueAt": now + 600, "event": event})
    return "I’ll repeat that announcement in ten minutes, if quiet hours and privacy checks allow it."


def tick(now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    path = alerts.DATA_DIR / "announcement_followup.json"
    with (alerts.DATA_DIR / "announcement_followup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            queued = json.loads(path.read_text())
            due = float(queued["dueAt"])
            requested = float(queued["requestedAt"])
            event = queued["event"]
            if due != requested + 600 or not approved(event, requested):
                save(path, {})
                return "invalid"
        except (OSError, ValueError, KeyError, TypeError):
            return "empty"
        if now < due:
            return "waiting"
        # Consume before the relay, including uncertain results. Never catch up later.
        save(path, {})
        if now - due > 120 or not 8 <= datetime.fromtimestamp(now, LOCAL).hour < 21:
            return "expired_or_quiet"
        if active(event["announcementId"]) or not can_disclose(event, now):
            return "suppressed"
        alerts.run_indoor_homepod_announcement("Earlier announcement: " + event["message"], "dr_house_snoozed")
        return "attempted"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tick", action="store_true", required=True)
    parser.parse_args()
    if not alerts.running_from_runtime_root():
        raise SystemExit("Scheduled follow-ups require deployed runtime")
    print(json.dumps({"status": tick()}))
