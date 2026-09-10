"""NWS hourly forecast for the home's Ventura forecast grid; no precise address sent."""
import json
import urllib.request
from datetime import datetime


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def rain_expected(properties, now):
    try:
        if not 0 <= now - stamp(properties["updateTime"]) <= 21600:
            return None
        relevant = [p for p in properties["periods"] if stamp(p["endTime"]) > now and stamp(p["startTime"]) <= now + 3600]
        if not relevant:
            return None
        return any((p.get("probabilityOfPrecipitation", {}).get("value") or 0) >= 60
                   and any(w in p.get("shortForecast", "").lower() for w in ("rain", "showers", "thunderstorm")) for p in relevant)
    except (KeyError, ValueError, TypeError):
        return None


def check(previous, observations, now):
    state = dict(previous)
    if not 0 <= now - state.get("fetchedAt", -1e12) < 900:
        try:
            request = urllib.request.Request("https://api.weather.gov/gridpoints/LOX/119,62/forecast/hourly", headers={"User-Agent": "SmartHomeMonitor household forecast"})
            with urllib.request.urlopen(request, timeout=8) as response:
                state["forecast"] = json.load(response)["properties"]
            state["fetchedAt"] = now
        except Exception:
            return state, None
    wet = rain_expected(state.get("forecast", {}), now)
    if wet is False:
        state["attempted"] = False
    opened = [item["name"] for item in observations.values() if item["state"] == "open"
              and (item["group"] == "garages" or (item["group"] == "sensors" and any(w in item["name"].lower() for w in ("window", "slider"))))]
    if wet and opened and not state.get("attempted"):
        state["attempted"] = True
        return state, "Rain is likely within the next hour while these are open: " + ", ".join(dict.fromkeys(opened)) + "."
    return state, None
