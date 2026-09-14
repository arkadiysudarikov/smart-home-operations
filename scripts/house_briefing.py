"""On-demand Dr. House briefings. No device changes or scheduled announcements."""
import argparse
import json
import math
import fcntl
from datetime import datetime, timezone
from pathlib import Path

import generate_alerts as alerts

MODES = ("status", "energy", "complications", "discharge", "night", "changes", "explain", "hold")
BASELINE = alerts.DATA_DIR / "dr_house_baseline.json"


def observations(now):
    items = alerts.household_observations(alerts.load_alarm_com(), now)
    values = {key: {"name": value["name"], "state": value["state"]} for key, value in items.items() if not key.startswith(("cooling:", "temperature:"))}
    energy = alerts.load_json_file(alerts.ENERGY_HIGH_CONTEXT_PATH) or {}
    if fresh(energy, "generatedAt", now) and fresh(energy, "sampleAt", now) and isinstance(energy.get("active"), bool):
        values["energy"] = {"name": "Energy High", "state": "active" if energy["active"] else "clear"}
    laundry = alerts.load_json_file(alerts.DATA_DIR / "latest_smarthq_laundry_state.json") or {}
    if laundry.get("ok") is True and fresh(laundry, "capturedAt", now):
        for key, device in (laundry.get("devices") or {}).items():
            if fresh(device, "apiLastSuccessAt", now, 300) and isinstance(device.get("cycleActive"), bool):
                values["laundry:" + key] = {"name": key, "state": "running" if device["cycleActive"] else "idle"}
    return values


def changes_message(previous, current, now):
    if not fresh(previous, "at", now, 86400):
        return "No recent rounds to compare against. This check establishes the baseline."
    old = previous.get("observations") or {}
    changes = [f"{v['name']} is now {v['state']}" for k, v in current.items() if k in old and old[k].get("state") != v["state"]]
    missing = set(old) - set(current)
    text = "Interval changes: " + "; ".join(changes[:6]) + "." if changes else "No significant changes in the comparable readings."
    if missing:
        text += " Some previous readings are unavailable; they are not assumed normal."
    return text


def explanation(now, energy):
    path = alerts.DATA_DIR / "homepod_announcement_events.jsonl"
    try:
        # Bounded tail; don't replay old or failed announcements.
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 262144))
            rows = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(rows):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            identifier = str(event.get("announcementId", ""))
            if event.get("ok") is not True or event.get("skipped") or identifier.startswith("dr_house_"):
                continue
            if not fresh(event, "at", now, 7200):
                break
            if identifier.startswith("calendar-"):
                return "The last alert was an appointment reminder. " + (calendar_summary(now) or "I cannot repeat personal details without a current eligible appointment and verified home presence.")
            if identifier.startswith("energy_"):
                return "The last alert concerned energy. Current assessment: " + energy_message(energy, now, True)
            if identifier in ("washer", "dryer", "combo", "bubbler_on", "household_reminder"):
                return "The last accepted alert said: " + str(event.get("message", ""))[:600] + " That was the recorded alert, not a new sensor check. I do not have additional trigger evidence attached to that event."
            return "I cannot reliably explain the last alert from the recorded evidence."
    except OSError:
        pass
    return "There is no recent accepted announcement to explain."


def fresh(data, key, now, seconds=600):
    age = alerts.elapsed_minutes(data.get(key), now)
    return age is not None and 0 <= age * 60 <= seconds


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def energy_message(context, now, explain=False):
    if not fresh(context, "generatedAt", now) or not fresh(context, "sampleAt", now):
        return "Energy readings are unavailable."
    load, threshold = context.get("liveLoadKw"), context.get("thresholdKw")
    if not number(load) or not number(threshold) or threshold <= 0:
        return "Current energy readings are unavailable."
    high = load >= threshold
    if not explain:
        return "Energy use is high." if high else "Energy use is within the current normal range."
    if not high:
        return "Energy use is normal."
    loads = []
    for candidate in context.get("candidates") or []:
        if candidate.get("source") != "Sense" or not fresh(candidate, "capturedAt", now):
            continue
        for device in candidate.get("devices") or []:
            name, watts = str(device.get("name") or ""), device.get("watts")
            if name.lower() not in ("solar", "other", "unknown", "always on") and str(device.get("id", "")).lower() != "solar" and number(watts) and watts >= 200:
                loads.append((watts, name))
    if loads:
        watts, name = max(loads)
        name = "AC" if name.lower() == "central ac" else name
        return f"Energy is high; {name} is using about {watts / 1000:.1f} kilowatts."
    return "Energy is high; cause unknown."


def status_message(alarm, laundry, energy, now, calendar_text="", weather_text=""):
    parts = ["House rounds."]
    observations = alerts.household_observations(alarm, now)
    openings = list(dict.fromkeys(v["name"] for k, v in observations.items() if not k.startswith(("cooling:", "temperature:")) and v["state"] in ("open", "unlocked")))
    if openings:
        parts.append("Needs attention. Open or unlocked: " + ", ".join(openings[:4]) + (f", and {len(openings)-4} more" if len(openings)>4 else "") + ".")
    if laundry.get("ok") is True and fresh(laundry, "capturedAt", now):
        for name, device in (laundry.get("devices") or {}).items():
            if not fresh(device, "apiLastSuccessAt", now, 300):
                continue
            label = {"washer": "The washer", "dryer": "The dryer", "combo": "The washer dryer combo"}.get(name, "A laundry appliance")
            if device.get("cycleActive") is True:
                remaining = device.get("remainingSeconds")
                parts.append(f"{label} has about {math.ceil(remaining / 60)} minutes remaining." if number(remaining) and 0 < remaining < 86400 else f"{label} is running.")
    parts.append(energy_message(energy, now))
    if calendar_text:
        parts.append(calendar_text)
    if weather_text:
        parts.append(weather_text)
    if not observations:
        parts.append("Door and lock status is unavailable.")
    return " ".join(parts)


def calendar_summary(now):
    # Query the authorized helper and actual phones; never use a tablet as presence.
    import calendar_announcements as cal
    try:
        config = json.loads(cal.CONFIG.read_text())
        snapshot = cal.invoke()
        if not fresh(snapshot, "generatedAt", now, 90):
            return ""
        current = datetime.now(timezone.utc).timestamp()
        clients = cal.active_clients()
        events = []
        for event in snapshot.get("events", []):
            owner = event.get("calendar")
            mac = config.get("phones", {}).get(owner)
            if not mac or not cal.is_home(clients, mac, current) or event.get("allDay") or event.get("status") == 3:
                continue
            start = cal.timestamp(event["start"])
            if 0 < start - current <= 10800:
                events.append((start, event))
        if not events:
            return ""
        start, event = min(events, key=lambda item: item[0])
        owner = event["calendar"]
        departure_text = ""
        if cal.eligible(event, current) and config.get("homeAddress"):
            try:
                eta = cal.invoke("--eta", config["homeAddress"], str(event["latitude"]), str(event["longitude"]))
                seconds = eta.get("seconds")
                if fresh(eta, "generatedAt", datetime.now(timezone.utc).isoformat(), 90) and number(seconds) and 0 < seconds <= 10800:
                    leave_in = (start - datetime.now(timezone.utc).timestamp() - seconds) / 60 - config.get("arrivalBufferMinutes", 15)
                    departure_text = (" Leave now" if leave_in <= 0 else f" Leave in about {math.ceil(leave_in)} minutes") + f" for a {math.ceil(seconds / 60)} minute drive, including your arrival buffer."
            except Exception:
                pass
        if not cal.is_home(cal.active_clients(), config["phones"][owner], datetime.now(timezone.utc).timestamp()):
            return ""
        return f"{owner}, {str(event.get('title') or 'your appointment')[:100]} starts in about {max(1, math.ceil((start-current)/60))} minutes." + departure_text
    except Exception:
        return ""


def build(mode):
    now = datetime.now(timezone.utc).isoformat()
    energy = alerts.load_json_file(alerts.ENERGY_HIGH_CONTEXT_PATH) or {}
    if mode == "hold":
        return "Routine announcements are on hold for one hour. Detector alarms are unchanged."
    if mode == "changes":
        return changes_message(alerts.load_json_file(BASELINE) or {}, observations(now), now)
    if mode == "explain":
        return explanation(now, energy)
    if mode == "complications":
        current = observations(now)
        problems = [f"{v['name']} is {v['state']}" for k, v in current.items() if v["state"] in ("open", "unlocked") or (k == "energy" and v["state"] == "active")]
        if not any(k.startswith(("sensors:", "garages:", "locks:")) for k in current):
            problems.append("door and lock readings are unavailable")
        if "energy" not in current:
            problems.append("energy readings are unavailable")
        return "Needs attention: " + "; ".join(problems[:6]) + "." if problems else ""
    if mode in ("discharge", "night"):
        text = alerts.bedtime_message(alerts.load_alarm_com(), now).replace("Bedtime check.", "Discharge summary." if mode == "discharge" else "Night rounds.")
        running = [v["name"] for k, v in observations(now).items() if k.startswith("laundry:") and v["state"] == "running"]
        if running:
            text += " Laundry still running: " + ", ".join(running) + "."
        if mode == "discharge":
            text += " " + calendar_summary(now)
        return text
    if mode == "energy":
        return "" + energy_message(energy, now, explain=True)
    weather_text = ""
    try:
        from household_weather import check, rain_expected
        state, _ = check({}, {}, datetime.now(timezone.utc).timestamp())
        if rain_expected(state.get("forecast", {}), datetime.now(timezone.utc).timestamp()) is True:
            weather_text = "Rain is likely within the next hour."
    except Exception:
        pass
    return status_message(alerts.load_alarm_com(), alerts.load_json_file(alerts.DATA_DIR / "latest_smarthq_laundry_state.json") or {}, energy, now, calendar_summary(now), weather_text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, default="status")
    parser.add_argument("--speak", action="store_true")
    args = parser.parse_args()
    if args.speak and not alerts.running_from_runtime_root():
        raise SystemExit("Spoken briefings require deployed runtime")
    if args.speak:
        lock = (alerts.DATA_DIR / "dr_house.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "skipped", "reason": "A briefing is already running"}))
            return 0
        if args.mode == "hold":
            from announcement_pause import hold
            hold()
    message = build(args.mode)
    if args.speak:
        if not alerts.running_from_runtime_root():
            raise SystemExit("Spoken briefings require deployed runtime")
        if not message:
            print(json.dumps({"status": "skipped", "reason": "No complications in available readings"}))
            return 0
        delivery = alerts.run_indoor_homepod_announcement(message, "dr_house_" + args.mode)
        if delivery.get("status") == "accepted" and args.mode in ("status", "night", "discharge", "changes", "complications"):
            now = datetime.now(timezone.utc).isoformat()
            BASELINE.write_text(json.dumps({"at": now, "observations": observations(now)}))
        print(json.dumps(delivery))
        return 0 if delivery.get("status") == "accepted" else 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
