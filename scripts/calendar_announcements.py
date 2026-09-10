"""Local calendar departure reminders. Dry-run unless explicitly passed --deliver."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import display_awake_manager as presence
from generate_alerts import run_indoor_homepod_announcement

RUNTIME = Path.home() / "Library/Application Support/SmartHomeMonitor"
READER = RUNTIME / "Smart Home Calendar Reader.app/Contents/MacOS/calendar-reader"
CONFIG = RUNTIME / "data/calendar_announcement_config.json"
STATE = RUNTIME / "data/calendar_announcement_state.json"
LOCAL = ZoneInfo("America/Los_Angeles")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def eligible(event, now):
    try:
        lead = timestamp(event["start"]) - now
        location = event.get("location", "").lower()
        lat, lon = float(event["latitude"]), float(event["longitude"])
        return (event.get("calendar") in ("Arkadiy", "Maxim", "Jeanne")
                and not event.get("allDay") and event.get("status") != 3
                and 0 < lead <= 10800 and -90 <= lat <= 90 and -180 <= lon <= 180
                and not any(word in location for word in ("http:", "https:", "zoom", "teams.microsoft", "online", "virtual")))
    except (KeyError, TypeError, ValueError):
        return False


def video_event(event, now):
    try:
        lead = timestamp(event["start"]) - now
        text = (event.get("location", "") + " " + event.get("url", "")).lower()
        # A generic URL can be a venue page, not a video appointment.
        markers = ("zoom.us/", "meet.google.com/", "teams.microsoft.com/", "teams.live.com/", "webex.com/", "facetime.apple.com/", "video call", "video appointment", "online meeting", "virtual meeting")
        return (event.get("calendar") in ("Arkadiy", "Maxim", "Jeanne")
                and not event.get("allDay") and event.get("status") != 3
                and 0 < lead <= 10800 and any(marker in text for marker in markers))
    except (KeyError, TypeError, ValueError):
        return False


def is_home(clients, mac, now):
    for client in clients:
        if client.get("mac", "").lower() == mac.lower():
            try:
                age = now - float(client["last_seen"])
                return 0 <= age <= 90 and not client.get("is_wired", False)
            except (KeyError, TypeError, ValueError):
                return False
    return False


def occurrence(event):
    return hashlib.sha256((event["calendar"] + event["id"] + event["start"]).encode()).hexdigest()


def due(event, seconds, now, buffer_minutes):
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 10800:
        return False
    departure = timestamp(event["start"]) - seconds - buffer_minutes * 60
    # Short catch-up window; never announce an old reminder at startup/arrival.
    return 0 <= now - departure <= 120


def invoke(*args):
    result = subprocess.run([str(READER), *args], capture_output=True, text=True, timeout=45, check=True)
    data = json.loads(result.stdout)
    if data.get("ok") is not True:
        raise RuntimeError(data.get("error", "Reader unavailable"))
    return data


def active_clients():
    session = presence.UnifiSession()
    presence.query_unifi_clients(session=session)
    config = presence.load_config()
    unifi = presence.unifi_platform(presence.read_json(presence.HOMEBRIDGE_CONFIG))["unifi"]
    headers = {"X-API-KEY": presence.keychain_secret(config["unifi_api_keychain_service"], config["unifi_api_keychain_account"])}
    payload = presence.request_json(session.opener, unifi["controller"].rstrip("/") + "/proxy/network/api/s/" + unifi.get("site", "default") + "/stat/sta", extra_headers=headers, timeout=12)
    if payload.get("meta", {}).get("rc") != "ok" or not isinstance(payload.get("data"), list):
        raise RuntimeError("Presence unavailable")
    return payload["data"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    snapshot = invoke()
    now = datetime.now(timezone.utc).timestamp()
    if not 0 <= now - timestamp(snapshot["generatedAt"]) <= 90:
        raise RuntimeError("Stale calendar snapshot")
    clients = active_clients()
    home = {person: is_home(clients, mac, now) for person, mac in config["phones"].items() if person in ("Arkadiy", "Maxim", "Jeanne")}
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "home": home, "eventCount": len(snapshot["events"]), "due": 0, "attempts": []}
    if not 8 <= datetime.now(LOCAL).hour < 21:
        report["suppressed"] = "quiet_hours"
        print(json.dumps(report))
        return
    with (STATE.parent / "calendar_announcement.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        state = {key: value for key, value in state.items() if now - value < 604800}
        for event in snapshot["events"]:
            video = video_event(event, now)
            if not (video or eligible(event, now)) or not home.get(event["calendar"], False):
                continue
            key = occurrence(event)
            if key in state:
                continue
            eta = None if video else invoke("--eta", config["homeAddress"], str(event["latitude"]), str(event["longitude"]))
            current = datetime.now(timezone.utc).timestamp()
            if eta is not None and not 0 <= current - timestamp(eta["generatedAt"]) <= 90:
                continue
            ready = (0 <= current - (timestamp(event["start"]) - 300) <= 120) if video else due(event, float(eta["seconds"]), current, config.get("arrivalBufferMinutes", 15))
            if not ready:
                continue
            report["due"] += 1
            if args.deliver and config.get("enabled") is True:
                if Path(__file__).resolve().parents[1] != RUNTIME.resolve():
                    raise RuntimeError("Delivery requires deployed runtime")
                # Recheck the actual phone immediately before speaking.
                if not is_home(active_clients(), config["phones"][event["calendar"]], datetime.now(timezone.utc).timestamp()):
                    continue
                state[key] = current
                temporary = STATE.with_suffix(".tmp")
                temporary.write_text(json.dumps(state))
                temporary.chmod(0o600)
                temporary.replace(STATE)
                # Persist before delivery: uncertain relay results must not repeat.
                minutes = max(1, math.ceil((timestamp(event['start']) - current) / 60))
                message = (f"{event['calendar']}, {event['title']} starts in about {minutes} minutes. This is a video appointment."
                           if video else f"{event['calendar']}, it is time to leave for {event['title']}. The drive is about {math.ceil(eta['seconds'] / 60)} minutes.")
                result = run_indoor_homepod_announcement(message, "calendar-" + key)
                report["attempts"].append({"id": key, "status": result.get("status")})
        print(json.dumps(report))


if __name__ == "__main__":
    main()
