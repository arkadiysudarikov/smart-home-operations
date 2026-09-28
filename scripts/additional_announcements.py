"""Conservative optional announcements; dry-run by default, no mailbox access."""
import argparse
import copy
import fcntl
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from announcement_health import load

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / "Library/Application Support/SmartHomeMonitor"


def stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo else float("nan")
    except (TypeError, ValueError, AttributeError):
        return float("nan")


def daytime(now):
    return 8 <= datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).hour < 21


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def episode(state, name, bad, now, seconds, fresh=True):
    """Require continuous observations; startup and gaps cannot imply recovery."""
    prior = state.get(name, {})
    continuous = 0 <= now - prior.get("at", 0) <= 600
    if not fresh:
        state[name] = {"at": now, "valid": False}
        return None
    if not continuous or not prior.get("valid"):
        prior = {}
    current = {"at": now, "valid": True, "bad": bad,
               "since": prior.get("since", now) if bad and prior.get("bad") else now,
               "warned": prior.get("warned", False) if bad else False,
               "notified": prior.get("notified", False) if bad else False}
    state[name] = current
    if bad and now-current["since"] >= seconds and not current["warned"]:
        current["warned"] = True  # consumed even during quiet hours; no stale catch-up
        return "failure"
    if not bad and prior.get("bad") and prior.get("notified"):
        return "recovery"
    return None


def evaluate(config, original, health, rings, freezer, now):
    state = copy.deepcopy(original)
    notices = []
    enabled = config.get("enabled") is True
    if enabled and config.get("laundryHealth") is True:
        sources = health.get("laundrySources", {})
        fresh = 0 <= now-stamp(health.get("generatedAt")) <= 180
        bad = (health.get("smartHQPlugin", {}).get("ok") is not True
               or len(sources) != 3
               or "Laundry capture stale or missing" in health.get("faults", [])
               or any(s.get("active") and s.get("status") != "fresh" for s in sources.values()))
        change = episode(state, "laundry", bad, now, 600, fresh)
        if change:
            state["laundry"]["notified"] = change == "failure" and daytime(now)
            notices.append(("laundry_health", "Laundry alerts may be unavailable. The laundry connection needs attention."
                            if change == "failure" else "The laundry connection is healthy again."))
    door = config.get("doorbell", {})
    if enabled and door.get("enabled") is True and door.get("sourceVerified") is True and door.get("cameraId"):
        old = state.get("doorbell", {})
        seen = {key: value for key, value in old.get("seen", {}).items() if 0 <= now-value < 86400}
        latest = old.get("lastAttempt", 0)
        # First run establishes a baseline, never announces historical rings.
        baseline = "doorbell" not in state
        expected = f"Camera ring detected for {door.get('name', 'Doorbell')} ({door['cameraId']})"
        for ring in sorted(rings, key=lambda r: str(r.get("capturedAt", ""))):
            key = ring.get("eventKey")
            if not key or ring.get("message") != expected or ring.get("component") != door.get("component"):
                continue
            if key in seen or not 0 <= now-stamp(ring.get("capturedAt")) <= 90:
                continue
            seen[key] = now
            if not baseline and now-latest >= 30:
                latest = now
                notices.append(("doorbell_press", "Someone rang the doorbell."))
        state["doorbell"] = {"seen": seen, "lastAttempt": latest}
    cfg = config.get("freezer", {})
    if enabled and cfg.get("enabled") is True and cfg.get("sourceVerified") is True and cfg.get("sensorId"):
        value = freezer.get("fahrenheit")
        valid = (freezer.get("sensorId") == cfg["sensorId"] and freezer.get("ok") is True
                 and isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and -80 <= value <= 150
                 and 0 <= now-stamp(freezer.get("observedAt")) <= 180)
        threshold = float(cfg.get("warnAboveF", 15))
        clear = float(cfg.get("clearBelowF", 10))
        if not math.isfinite(threshold) or not math.isfinite(clear) or clear >= threshold:
            raise ValueError("Invalid freezer hysteresis")
        was_bad = state.get("freezer", {}).get("bad", False)
        bad = valid and (value > clear if was_bad else value > threshold)
        change = episode(state, "freezer", bad, now, 1800, valid)
        if change:
            state["freezer"]["notified"] = change == "failure" and daytime(now)
            notices.append(("freezer_temperature", "The freezer has stayed unusually warm for thirty minutes. Please check it."
                            if change == "failure" else "The freezer temperature is back below the alert threshold."))
    return state, notices if daytime(now) else []


def calendar_changes(events, original, home, now):
    """Explicit cancellation only. No inference from deletions or window expiry."""
    state = {}
    candidates = []
    grouped = {}
    for event in events:
        if event.get("calendar") not in ("Arkadiy", "Maxim", "Jeanne") or event.get("allDay"):
            continue
        # Reader metadata is mandatory; old readers fail closed for reschedules.
        if event.get("recurring") is not False or not event.get("id"):
            continue
        key = hashlib.sha256((event["calendar"] + "\0" + event["id"]).encode()).hexdigest()
        grouped.setdefault(key, []).append(event)
    for key, group in grouped.items():
        if len(group) != 1:
            continue
        event = group[0]
        start = stamp(event.get("start"))
        if not 0 < start-now <= 86400 or event.get("status") not in (0, 1, 2, 3):
            continue
        version = hashlib.sha256(json.dumps({k: event.get(k) for k in ("start", "location", "status")}, sort_keys=True).encode()).hexdigest()
        state[key] = {"version": version, "at": now, "cancelled": event.get("status") == 3,
                      "lastAttempt": original.get(key, {}).get("lastAttempt", 0)}
        old = original.get(key)
        if not old or not 0 <= now-old.get("at", 0) <= 600 or old.get("version") == version:
            continue
        if not home.get(event["calendar"]) or not daytime(now) or now-old.get("lastAttempt", 0) < 900:
            continue
        state[key]["lastAttempt"] = now
        word = "was cancelled" if event.get("status") == 3 else "was updated"
        message = f"{event['calendar']}, an upcoming appointment {word}. Check Calendar for details."
        candidates.append((event["calendar"], "calendar-change-" + key, message))
    return state, candidates


def read_rings(data):
    path = data / "smart_home.sqlite"
    if not path.exists():
        return []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT event_key,captured_at,component,message FROM home_events WHERE message LIKE 'Camera ring detected for %' ORDER BY captured_at DESC LIMIT 100").fetchall()
    return [dict(zip(("eventKey", "capturedAt", "component", "message"), row)) for row in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    if args.deliver and ROOT != RUNTIME.resolve():
        raise RuntimeError("Delivery requires deployed runtime")
    data = ROOT / "data"
    config = load(ROOT / "config/additional_announcements.json")
    with (data / "additional_announcements.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = data / "additional_announcements_state.json"
        state, notices = evaluate(config, load(path), load(data / "latest_announcement_health.json"),
                                  read_rings(data) if config.get("doorbell", {}).get("enabled") else [],
                                  load(data / "verified_freezer_temperature.json"), datetime.now(timezone.utc).timestamp())
        results = []
        if args.deliver:
            # At-most-once attempt, including uncertain relay results.
            pending = {"laundry_health": "laundry", "freezer_temperature": "freezer"}
            for identifier, _ in notices:
                name = pending.get(identifier)
                if name and state.get(name, {}).get("bad"):
                    state[name]["notified"] = False
            save(path, state)
            from generate_alerts import run_indoor_homepod_announcement
            for identifier, message in notices:
                status = run_indoor_homepod_announcement(message, identifier).get("status")
                results.append({"id": identifier, "status": status})
                name = pending.get(identifier)
                if name and state.get(name, {}).get("bad") and status == "accepted":
                    state[name]["notified"] = True
                    save(path, state)
        print(json.dumps({"dryRun": not args.deliver, "due": len(notices), "attempts": results}))


if __name__ == "__main__":
    main()
