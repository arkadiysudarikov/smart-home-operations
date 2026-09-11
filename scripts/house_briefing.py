"""On-demand Dr. House briefings. No device changes or scheduled announcements."""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import generate_alerts as alerts


def fresh(data, key, now, seconds=600):
    age = alerts.elapsed_minutes(data.get(key), now)
    return age is not None and 0 <= age * 60 <= seconds


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def energy_message(context, now, explain=False):
    if not fresh(context, "generatedAt", now) or not fresh(context, "sampleAt", now):
        return "Energy assessment deferred. Current readings are unavailable."
    load, threshold = context.get("liveLoadKw"), context.get("thresholdKw")
    if not number(load) or not number(threshold) or threshold <= 0:
        return "Current energy readings are unavailable."
    high = load >= threshold
    if not explain:
        return "Energy use is high." if high else "Energy use is within the current normal range."
    text = f"Energy assessment: {'elevated demand' if high else 'stable'}. House load is {load:.1f} kilowatts, {'above' if high else 'below'} the current {threshold:.1f} kilowatt alert threshold."
    if not high:
        return text + " Energy High is not currently active."
    loads = []
    for candidate in context.get("candidates") or []:
        if candidate.get("source") != "Sense" or not fresh(candidate, "capturedAt", now):
            continue
        for device in candidate.get("devices") or []:
            name, watts = str(device.get("name") or ""), device.get("watts")
            if name.lower() not in ("solar", "other", "unknown", "always on") and str(device.get("id", "")).lower() != "solar" and number(watts) and watts >= 200:
                loads.append((watts, name))
    if loads:
        text += " Suspected contributors, based on Sense estimates: " + "; ".join(f"{name} at {watts / 1000:.1f} kilowatts" for watts, name in sorted(loads, reverse=True)[:2]) + ". That does not establish the full cause."
    else:
        text += " I do not have reliable appliance-level evidence to name the main cause."
    adjustments = context.get("adjustments") or []
    if adjustments:
        text += " The alert threshold also accounts for " + "; ".join(str(a["reason"]) for a in adjustments[:2] if a.get("reason")) + "."
    return text


def status_message(alarm, laundry, energy, now, calendar_text="", weather_text=""):
    parts = ["Dr. House. House rounds."]
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
    if mode == "energy":
        return "Dr. House. " + energy_message(energy, now, explain=True)
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
    parser.add_argument("--mode", choices=("status", "energy"), default="status")
    parser.add_argument("--speak", action="store_true")
    args = parser.parse_args()
    message = build(args.mode)
    if args.speak:
        if not alerts.running_from_runtime_root():
            raise SystemExit("Spoken briefings require deployed runtime")
        delivery = alerts.run_indoor_homepod_announcement(message, "dr_house_" + args.mode)
        print(json.dumps(delivery))
        return 0 if delivery.get("status") == "accepted" else 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
