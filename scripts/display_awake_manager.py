#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shlex
import signal
import ssl
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
CONFIG_PATH = ROOT / "config" / "sources.json"
HOMEBRIDGE_CONFIG = Path.home() / ".homebridge" / "config.json"
HOMEBRIDGE_ACCESSORIES = Path.home() / ".homebridge" / "accessories"
DATA_DIR = ROOT / "data"
STATUS_PATH = DATA_DIR / "latest_display_awake.json"
SUMMARY_PATH = DATA_DIR / "latest_display_awake_summary.json"
EVENTS_PATH = DATA_DIR / "display_awake_events.jsonl"
ENROLLMENT_PATH = DATA_DIR / "display_awake_enrollment.json"
HOME_MAPPING_PATH = DATA_DIR / "display_awake_home_mapping.json"
MODE_PATH = DATA_DIR / "display_awake_mode.json"
OVERRIDE_PATH = DATA_DIR / "display_awake_override.json"
UNIFI_OBSERVATIONS_CACHE_PATH = DATA_DIR / "display_awake_unifi_observations.json"

SENSITIVE_KEYS = {
    "ap_mac",
    "bssid",
    "client_id",
    "clientid",
    "id",
    "mac",
    "macaddress",
    "uplink_mac",
    "wifi_mac",
    "wifimac",
}

PROBE_SCRIPT = r'''
console_user=$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)
idle_ns=$(/usr/sbin/ioreg -r -c IOHIDSystem -l 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ {gsub(/[^0-9]/, "", $2); print $2; exit}')
locked=$(/usr/sbin/ioreg -n Root -d1 2>/dev/null | /usr/bin/awk -F'= ' '/"CGSSessionScreenIsLocked"/ {gsub(/[[:space:]]/, "", $2); print tolower($2); exit}')
power=$(/usr/bin/pmset -g batt 2>/dev/null | /usr/bin/head -1)
lid=$(/usr/sbin/ioreg -r -k AppleClamshellState -d1 2>/dev/null | /usr/bin/awk -F'= ' '/"AppleClamshellState"/ {gsub(/[[:space:]]/, "", $2); print tolower($2); exit}')
wifi_device=$(/usr/sbin/networksetup -listallhardwareports 2>/dev/null | /usr/bin/awk '/Hardware Port: (Wi-Fi|AirPort)/ {wifi=1; next} wifi && /Device:/ {print $2; exit}')
wifi_mac=$(/sbin/ifconfig "$wifi_device" 2>/dev/null | /usr/bin/awk '/ether / {print tolower($2); exit}')
if [ -z "$wifi_mac" ]; then
  wifi_mac=$(/usr/sbin/networksetup -listallhardwareports 2>/dev/null | /usr/bin/awk '/Hardware Port: (Wi-Fi|AirPort)/ {wifi=1; next} wifi && /Ethernet Address:/ {print tolower($3); exit}')
fi
native_assertion=$(/usr/bin/pmset -g assertions 2>/dev/null | /usr/bin/awk '/PreventUserIdleDisplaySleep/ {print $2; exit}')
/usr/bin/printf 'consoleUser=%s\nidleNs=%s\nlocked=%s\npower=%s\nlidClosed=%s\nwifiMac=%s\nnativeDisplayAssertion=%s\n' "$console_user" "$idle_ns" "$locked" "$power" "$lid" "$wifi_mac" "$native_assertion"
'''.strip()


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def iso_now() -> str:
    return local_now().isoformat(timespec="seconds")


def running_from_runtime_root() -> bool:
    return ROOT.resolve() == RUNTIME_ROOT.resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(sanitize(payload), sort_keys=True) + "\n")


def parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def build_observability_summary(
    previous: dict[str, Any],
    status: dict[str, Any],
    *,
    now: float,
    max_sample_gap_seconds: int,
) -> dict[str, Any]:
    previous_generated = parse_timestamp(previous.get("generatedAt"))
    raw_gap = max(0.0, now - previous_generated) if previous_generated is not None else 0.0
    accepted_gap = raw_gap if raw_gap <= max_sample_gap_seconds else 0.0
    previous_targets = previous.get("targets") if isinstance(previous.get("targets"), dict) else {}
    target_totals: dict[str, dict[str, Any]] = {}
    current_targets = status.get("targets") if isinstance(status.get("targets"), dict) else {}
    for target_id, current in current_targets.items():
        if not isinstance(current, dict):
            continue
        prior = previous_targets.get(str(target_id)) if isinstance(previous_targets.get(str(target_id)), dict) else {}
        totals = default_target_summary()
        totals.update({key: value for key, value in prior.items() if key in totals})
        totals["reasonEventCounts"] = dict(prior.get("reasonEventCounts") or {})
        totals["ineligibleEventCounts"] = dict(prior.get("ineligibleEventCounts") or {})
        if prior.get("currentWouldHold"):
            totals["predictedHoldSeconds"] = float(totals.get("predictedHoldSeconds") or 0) + accepted_gap
        if prior.get("currentLeaseActive"):
            totals["leaseActiveSeconds"] = float(totals.get("leaseActiveSeconds") or 0) + accepted_gap
        if prior and bool(prior.get("currentWouldHold")) != bool(current.get("wouldHold")):
            totals["wouldHoldTransitions"] = int(totals.get("wouldHoldTransitions") or 0) + 1
        if prior and bool(prior.get("currentLeaseActive")) != bool(current.get("leaseActive")):
            totals["leaseTransitions"] = int(totals.get("leaseTransitions") or 0) + 1
        current_reasons = list(current.get("reasons") or [])
        current_ineligible = list(current.get("ineligibleReasons") or [])
        if current_reasons != list(prior.get("currentReasons") or []):
            for reason in current_reasons:
                totals["reasonEventCounts"][str(reason)] = totals["reasonEventCounts"].get(str(reason), 0) + 1
        if current_ineligible != list(prior.get("currentIneligibleReasons") or []):
            for reason in current_ineligible:
                totals["ineligibleEventCounts"][str(reason)] = totals["ineligibleEventCounts"].get(str(reason), 0) + 1
        totals.update(
            {
                "currentWouldHold": bool(current.get("wouldHold")),
                "currentLeaseActive": bool(current.get("leaseActive")),
                "currentReasons": current_reasons,
                "currentIneligibleReasons": current_ineligible,
            }
        )
        target_totals[str(target_id)] = totals
    for totals in target_totals.values():
        totals["predictedHoldSeconds"] = round(float(totals["predictedHoldSeconds"]), 1)
        totals["leaseActiveSeconds"] = round(float(totals["leaseActiveSeconds"]), 1)
    current_presence = status.get("presence") if isinstance(status.get("presence"), dict) else {}
    previous_presence = previous.get("presence") if isinstance(previous.get("presence"), dict) else {}
    room = current_presence.get("confirmedRoom")
    source = current_presence.get("source")
    room_transitions = int(previous_presence.get("roomTransitions") or 0)
    source_transitions = int(previous_presence.get("sourceTransitions") or 0)
    if previous and previous_presence.get("currentRoom") != room:
        room_transitions += 1
    if previous and previous_presence.get("currentSource") != source:
        source_transitions += 1
    return {
        "ok": True,
        "generatedAt": datetime.fromtimestamp(now, timezone.utc).astimezone().isoformat(timespec="seconds"),
        "windowStartedAt": previous.get("windowStartedAt") or datetime.fromtimestamp(now, timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sampleCount": int(previous.get("sampleCount") or 0) + 1,
        "observationSeconds": round(float(previous.get("observationSeconds") or 0) + accepted_gap, 1),
        "droppedGapSeconds": round(float(previous.get("droppedGapSeconds") or 0) + (raw_gap if raw_gap > max_sample_gap_seconds else 0), 1),
        "presence": {
            "roomTransitions": room_transitions,
            "sourceTransitions": source_transitions,
            "currentRoom": room,
            "currentSource": source,
        },
        "targets": target_totals,
    }


def default_target_summary() -> dict[str, Any]:
    return {
        "predictedHoldSeconds": 0.0,
        "leaseActiveSeconds": 0.0,
        "wouldHoldTransitions": 0,
        "leaseTransitions": 0,
        "reasonEventCounts": {},
        "ineligibleEventCounts": {},
        "currentWouldHold": False,
        "currentLeaseActive": False,
        "currentReasons": [],
        "currentIneligibleReasons": [],
    }


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize(item)
            for key, item in value.items()
            if str(key).lower().replace("_", "") not in {key.replace("_", "") for key in SENSITIVE_KEYS}
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def load_config() -> dict[str, Any]:
    payload = read_json(CONFIG_PATH)
    config = payload.get("display_awake")
    return config if isinstance(config, dict) else {}


def unifi_platform(homebridge: dict[str, Any]) -> dict[str, Any]:
    for platform in homebridge.get("platforms", []):
        if isinstance(platform, dict) and platform.get("platform") == "UnifiOccupancy":
            return platform
    return {}


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "smart-home-display-awake"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def query_unifi_clients(homebridge_path: Path = HOMEBRIDGE_CONFIG) -> tuple[list[dict[str, Any]], dict[str, str]]:
    homebridge = read_json(homebridge_path)
    platform = unifi_platform(homebridge)
    unifi = platform.get("unifi") if isinstance(platform.get("unifi"), dict) else {}
    controller = str(unifi.get("controller") or "").rstrip("/")
    username = unifi.get("username")
    password = unifi.get("password")
    if not controller or not username or not password:
        raise RuntimeError("UniFi controller credentials are incomplete")

    context = ssl._create_unverified_context() if unifi.get("secure") is False else None
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(context=context),
    )
    unifios = unifi.get("unifios") is not False
    auth_path = "/api/auth/login" if unifios else "/api/login"
    request_json(
        opener,
        f"{controller}{auth_path}",
        method="POST",
        body={"username": username, "password": password},
        timeout=12,
    )
    prefix = "/proxy/network" if unifios else ""
    site = str(unifi.get("site") or "default")
    payload = request_json(opener, f"{controller}{prefix}/v2/api/site/{site}/clients/active")
    clients = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
    aliases = {
        str(item.get("accessPoint") or "").lower(): str(item.get("alias") or "")
        for item in platform.get("accessPointAliases", [])
        if isinstance(item, dict) and item.get("accessPoint") and item.get("alias")
    }
    return clients, aliases


def normalize_mac(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    parts = raw.split(":")
    if len(parts) != 6 or any(len(part) != 2 or any(char not in "0123456789abcdef" for char in part) for part in parts):
        return None
    return raw


def client_mac(client: dict[str, Any]) -> str | None:
    return normalize_mac(client.get("mac") or client.get("id") or client.get("client_mac"))


def client_model(client: dict[str, Any]) -> str:
    return str(client.get("model_name") or client.get("model") or "Unknown Apple device")


def client_kind(client: dict[str, Any]) -> str | None:
    blob = " ".join(
        str(client.get(key) or "")
        for key in ("display_name", "hostname", "name", "model_name", "model")
    ).lower()
    fingerprint = client.get("fingerprint") if isinstance(client.get("fingerprint"), dict) else {}
    category = fingerprint.get("dev_cat")
    if "watch" in blob or category == 45:
        return "watch"
    if "iphone" in blob or category == 44:
        return "iphone"
    return None


def candidate_token(mac: str) -> str:
    return hashlib.sha256(mac.encode()).hexdigest()[:12]


def sanitized_candidates(clients: list[dict[str, Any]], aliases: dict[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for client in clients:
        mac = client_mac(client)
        kind = client_kind(client)
        if not mac or not kind:
            continue
        ap_mac = normalize_mac(client.get("ap_mac") or client.get("uplink_mac"))
        candidates.append(
            {
                "candidate": candidate_token(mac),
                "kind": kind,
                "model": client_model(client),
                "accessPoint": aliases.get(ap_mac or "", "unmapped"),
                "active": str(client.get("status") or "online").lower() == "online",
            }
        )
    return sorted(candidates, key=lambda item: (item["kind"], item["model"], item["candidate"]))


def enroll_devices(
    clients: list[dict[str, Any]],
    *,
    watch_token: str,
    iphone_token: str,
    path: Path = ENROLLMENT_PATH,
) -> dict[str, Any]:
    selected: dict[str, str] = {}
    expected = {"watch": watch_token, "iphone": iphone_token}
    for client in clients:
        mac = client_mac(client)
        kind = client_kind(client)
        if not mac or not kind or candidate_token(mac) != expected.get(kind):
            continue
        selected[kind] = mac
    missing = sorted(set(expected) - set(selected))
    if missing:
        raise ValueError(f"candidate selection did not resolve: {', '.join(missing)}")
    write_private_json(path, {"version": 1, "enrolledAt": iso_now(), "devices": selected})
    return {"ok": True, "enrolled": sorted(selected), "path": str(path)}


def load_enrollment(path: Path = ENROLLMENT_PATH) -> dict[str, str]:
    payload = read_json(path)
    devices = payload.get("devices") if isinstance(payload.get("devices"), dict) else {}
    return {
        kind: mac
        for kind in ("watch", "iphone")
        if (mac := normalize_mac(devices.get(kind))) is not None
    }


def load_room_mapping(config: dict[str, Any], path: Path = HOME_MAPPING_PATH) -> dict[str, str]:
    mapping: dict[str, str] = {}
    configured = config.get("access_point_rooms")
    if isinstance(configured, dict):
        mapping.update({str(key): str(value) for key, value in configured.items() if value})
    local = read_json(path).get("accessPointRooms")
    if isinstance(local, dict):
        mapping.update({str(key): str(value) for key, value in local.items() if value})
    return mapping


def write_room_mapping(entries: list[str], path: Path = HOME_MAPPING_PATH) -> dict[str, Any]:
    mapping: dict[str, str] = {}
    for entry in entries:
        alias, separator, room = entry.partition("=")
        if not separator or not alias.strip() or not room.strip():
            raise ValueError(f"invalid mapping {entry!r}; expected ACCESS_POINT=ROOM")
        mapping[alias.strip()] = room.strip().lower()
    write_private_json(path, {"version": 1, "updatedAt": iso_now(), "accessPointRooms": mapping})
    return {"ok": True, "accessPointRooms": mapping, "path": str(path)}


def client_ap_alias(client: dict[str, Any], aliases: dict[str, str]) -> str | None:
    ap_mac = normalize_mac(client.get("ap_mac") or client.get("uplink_mac") or client.get("last_uplink_mac"))
    return aliases.get(ap_mac or "")


def client_last_seen(client: dict[str, Any]) -> float | None:
    value = client.get("last_seen") or client.get("lastSeen")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def presence_observations(
    clients: list[dict[str, Any]],
    aliases: dict[str, str],
    enrollment: dict[str, str],
    room_mapping: dict[str, str],
    *,
    now: float,
    fresh_seconds: int,
) -> dict[str, dict[str, Any]]:
    by_mac = {client_mac(client): client for client in clients if client_mac(client)}
    observations: dict[str, dict[str, Any]] = {}
    for kind in ("watch", "iphone"):
        client = by_mac.get(enrollment.get(kind))
        seen = client_last_seen(client) if client else None
        fresh = bool(client and seen is not None and now - seen <= fresh_seconds)
        alias = client_ap_alias(client, aliases) if client else None
        observations[kind] = {
            "enrolled": kind in enrollment,
            "online": bool(client),
            "fresh": fresh,
            "ageSeconds": max(0, round(now - seen)) if seen is not None else None,
            "accessPoint": alias,
            "room": room_mapping.get(alias or ""),
        }
    return observations


def cached_presence_observations(
    path: Path,
    *,
    now: float,
    max_age_seconds: int,
    fresh_seconds: int,
) -> tuple[dict[str, dict[str, Any]], float | None]:
    payload = read_json(path)
    captured_at = payload.get("capturedAtEpoch")
    observations = payload.get("observations")
    if not isinstance(captured_at, (int, float)) or not isinstance(observations, dict):
        return {}, None
    cache_age = max(0.0, now - float(captured_at))
    if cache_age > max(0, max_age_seconds):
        return {}, cache_age
    cached: dict[str, dict[str, Any]] = {}
    for kind in ("watch", "iphone"):
        source = observations.get(kind) if isinstance(observations.get(kind), dict) else {}
        prior_age = source.get("ageSeconds")
        age = max(0.0, float(prior_age) + cache_age) if isinstance(prior_age, (int, float)) else None
        cached[kind] = {
            "enrolled": source.get("enrolled") is True,
            "online": source.get("online") is True,
            "fresh": bool(source.get("fresh") and age is not None and age <= fresh_seconds),
            "ageSeconds": round(age) if age is not None else None,
            "accessPoint": source.get("accessPoint"),
            "room": source.get("room"),
            "cached": True,
            "cacheAgeSeconds": round(cache_age),
        }
    return cached, cache_age


class PresenceTracker:
    def __init__(self, *, confirmation_polls: int, grace_seconds: int, state: dict[str, Any] | None = None) -> None:
        self.confirmation_polls = max(1, confirmation_polls)
        self.grace_seconds = max(0, grace_seconds)
        self.state = dict(state or {})

    def update(self, observations: dict[str, dict[str, Any]], now: float) -> dict[str, Any]:
        watch = observations.get("watch") or {}
        iphone = observations.get("iphone") or {}
        iphone_fresh = iphone.get("fresh") is True
        watch_fresh = watch.get("fresh") is True
        iphone_room = str(iphone.get("room")) if iphone_fresh and iphone.get("room") else None
        watch_room = str(watch.get("room")) if watch_fresh and watch.get("room") else None
        prior_rooms = self.state.get("deviceRooms") if isinstance(self.state.get("deviceRooms"), dict) else {}
        prior_iphone = prior_rooms.get("iphone")
        prior_watch = prior_rooms.get("watch")
        iphone_moved = bool(iphone_room and prior_iphone and iphone_room != prior_iphone)
        watch_moved = bool(watch_room and prior_watch and watch_room != prior_watch)
        prior_agreement = bool(prior_iphone and prior_iphone == prior_watch)
        carried_source = self.state.get("carriedSource")
        if carried_source not in {"iphone", "watch"}:
            carried_source = None

        # Charging devices are usually stationary. Once the devices have been
        # observed together, whichever one leaves that shared floor becomes
        # the carried signal. Agreement resets to the iPhone default.
        candidate_source: str | None = None
        candidate_room: str | None = None
        if iphone_room and watch_room:
            if iphone_room == watch_room:
                carried_source = None
                self.state["lastAgreementRoom"] = iphone_room
                candidate_source, candidate_room = "iphone", iphone_room
            else:
                if iphone_moved != watch_moved:
                    carried_source = "iphone" if iphone_moved else "watch"
                elif carried_source not in {"iphone", "watch"}:
                    carried_source = "iphone"
                candidate_source = carried_source
                candidate_room = iphone_room if carried_source == "iphone" else watch_room
        elif iphone_room:
            if carried_source == "watch":
                candidate_source = candidate_room = None
            else:
                if prior_agreement and iphone_moved:
                    carried_source = "iphone"
                candidate_source, candidate_room = "iphone", iphone_room
                carried_source = carried_source or "iphone"
        elif watch_room:
            if carried_source == "iphone":
                candidate_source = candidate_room = None
            elif carried_source == "watch" or (prior_agreement and watch_moved):
                carried_source = "watch"
                candidate_source, candidate_room = "watch", watch_room

        selected_kind = candidate_source
        if selected_kind is None and carried_source is None:
            selected_kind = "iphone" if iphone_fresh else "watch" if watch_fresh else None
        confirmed = self.state.get("confirmedRoom")

        if candidate_room:
            if candidate_room == confirmed:
                self.state.update(
                    {
                        "pendingRoom": None,
                        "pendingSource": None,
                        "pendingCount": 0,
                        "lastConfirmedAt": now,
                        "confirmedSource": candidate_source,
                    }
                )
            else:
                same_candidate = (
                    self.state.get("pendingRoom") == candidate_room
                    and self.state.get("pendingSource") == candidate_source
                )
                pending_count = int(self.state.get("pendingCount") or 0) + 1 if same_candidate else 1
                self.state.update(
                    {"pendingRoom": candidate_room, "pendingSource": candidate_source, "pendingCount": pending_count}
                )
                if pending_count >= self.confirmation_polls:
                    self.state.update(
                        {
                            "confirmedRoom": candidate_room,
                            "confirmedSource": candidate_source,
                            "pendingRoom": None,
                            "pendingSource": None,
                            "pendingCount": 0,
                            "lastConfirmedAt": now,
                        }
                    )
        else:
            last_confirmed = self.state.get("lastConfirmedAt")
            if not isinstance(last_confirmed, (int, float)) or now - float(last_confirmed) > self.grace_seconds:
                self.state.update(
                    {
                        "confirmedRoom": None,
                        "confirmedSource": None,
                        "pendingRoom": None,
                        "pendingSource": None,
                        "pendingCount": 0,
                    }
                )

        device_rooms = dict(prior_rooms)
        if iphone_room:
            device_rooms["iphone"] = iphone_room
        if watch_room:
            device_rooms["watch"] = watch_room
        self.state["deviceRooms"] = device_rooms
        self.state["carriedSource"] = carried_source

        last_confirmed = self.state.get("lastConfirmedAt")
        grace_active = bool(
            candidate_room is None
            and self.state.get("confirmedRoom")
            and isinstance(last_confirmed, (int, float))
            and now - float(last_confirmed) <= self.grace_seconds
        )
        confirmed_source = self.state.get("confirmedSource")
        if confirmed_source not in {"iphone", "watch"} and self.state.get("confirmedRoom"):
            confirmed_source = "iphone"
            self.state["confirmedSource"] = confirmed_source
        zone_source = (
            f"{confirmed_source}_grace"
            if grace_active and confirmed_source
            else "watch_carried"
            if confirmed_source == "watch"
            else "iphone"
            if confirmed_source == "iphone"
            else None
        )
        self.state["source"] = selected_kind
        self.state["zoneSource"] = zone_source
        self.state["homePresent"] = bool(selected_kind)
        return {
            "homePresent": bool(selected_kind),
            "source": selected_kind,
            "confirmedSource": confirmed_source,
            "carriedSource": carried_source,
            "zoneSource": zone_source,
            "confirmedRoom": self.state.get("confirmedRoom"),
            "pendingRoom": self.state.get("pendingRoom"),
            "pendingSource": self.state.get("pendingSource"),
            "pendingCount": int(self.state.get("pendingCount") or 0),
            "graceActive": grace_active,
        }


def parse_probe_output(output: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    try:
        idle_seconds = int(values.get("idleNs") or "") / 1_000_000_000
    except ValueError:
        idle_seconds = None
    locked = values.get("locked", "").lower() in {"yes", "true", "1"}
    lid_closed = values.get("lidClosed", "").lower() in {"yes", "true", "1"}
    try:
        native_assertion = int(values.get("nativeDisplayAssertion") or 0) > 0
    except ValueError:
        native_assertion = False
    return {
        "reachable": True,
        "consoleUser": values.get("consoleUser") or None,
        "idleSeconds": round(idle_seconds, 1) if idle_seconds is not None else None,
        "locked": locked,
        "onAcPower": "AC Power" in values.get("power", ""),
        "lidClosed": lid_closed,
        "wifiMac": normalize_mac(values.get("wifiMac")),
        "nativeDisplayAssertion": native_assertion,
    }


def normalized_hostname(value: Any) -> str:
    hostname = str(value or "").strip().lower().rstrip(".")
    for suffix in (".localdomain", ".local"):
        if hostname.endswith(suffix):
            hostname = hostname[: -len(suffix)]
            break
    return hostname


def target_with_live_unifi_host(target: dict[str, Any], clients: list[dict[str, Any]]) -> dict[str, Any]:
    if target.get("host_source") != "unifi" or target.get("local"):
        return target
    configured_host = str(target.get("host") or "")
    wanted = normalized_hostname(configured_host)
    if not wanted:
        return target
    for client in clients:
        names = {
            normalized_hostname(client.get(key))
            for key in ("display_name", "hostname", "name")
            if client.get(key)
        }
        if wanted not in names or str(client.get("status") or "online").lower() != "online":
            continue
        address = client.get("ip") or client.get("ip_address")
        try:
            ipaddress.ip_address(str(address))
        except ValueError:
            continue
        resolved = dict(target)
        resolved["host"] = str(address)
        resolved["host_key_alias"] = str(target.get("host_key_alias") or configured_host)
        resolved["configured_host"] = configured_host
        resolved["probe_host_source"] = "unifi"
        return resolved
    return target


def probe_target(target: dict[str, Any], *, connect_timeout: int = 5) -> dict[str, Any]:
    if target.get("local"):
        command = ["/bin/zsh", "-lc", PROBE_SCRIPT]
    else:
        command = [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={connect_timeout}",
        ]
        if target.get("host_key_alias"):
            command.extend(["-o", f"HostKeyAlias={target['host_key_alias']}"])
        command.extend([str(target["host"]), "/bin/zsh", "-lc", shlex.quote(PROBE_SCRIPT)])
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=connect_timeout + 8, check=False)
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"reachable": False, "error": proc.stderr.strip()[-300:] or f"probe exited {proc.returncode}"}
    return parse_probe_output(proc.stdout)


def probe_targets(targets: list[dict[str, Any]], *, connect_timeout: int) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        futures = {
            executor.submit(probe_target, target, connect_timeout=connect_timeout): str(target["id"])
            for target in targets
        }
        for future in as_completed(futures):
            target_id = futures[future]
            try:
                results[target_id] = future.result()
            except Exception as exc:
                results[target_id] = {"reachable": False, "error": str(exc)}
    return results


def read_light_states(
    lights: list[dict[str, Any]], accessories_dir: Path = HOMEBRIDGE_ACCESSORIES
) -> dict[str, bool | None]:
    wanted = {str(item.get("accessory")) for item in lights if item.get("accessory")}
    states: dict[str, bool | None] = {name: None for name in wanted}
    for path in sorted(accessories_dir.glob("cachedAccessories*")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, list):
            continue
        for accessory in payload:
            if not isinstance(accessory, dict) or accessory.get("displayName") not in wanted:
                continue
            name = str(accessory["displayName"])
            for service in accessory.get("services", []):
                if not isinstance(service, dict):
                    continue
                for characteristic in service.get("characteristics", []):
                    if isinstance(characteristic, dict) and characteristic.get("constructorName") == "On":
                        states[name] = bool(characteristic.get("value"))
    return states


def dynamic_room_for_probe(
    probe: dict[str, Any],
    clients: list[dict[str, Any]],
    aliases: dict[str, str],
    room_mapping: dict[str, str],
) -> str | None:
    wifi_mac = normalize_mac(probe.get("wifiMac"))
    if not wifi_mac:
        return None
    client = next((item for item in clients if client_mac(item) == wifi_mac), None)
    alias = client_ap_alias(client, aliases) if client else None
    return room_mapping.get(alias or "")


def zone_for_target(
    target: dict[str, Any],
    probe: dict[str, Any],
    clients: list[dict[str, Any]],
    aliases: dict[str, str],
    room_mapping: dict[str, str],
    presence_room: str | None,
) -> tuple[str | None, str, str | None]:
    if not target.get("dynamic_room"):
        configured = str(target.get("zone") or target.get("room") or "") or None
        return configured, "configured", None
    unifi_zone = dynamic_room_for_probe(probe, clients, aliases, room_mapping)
    if target.get("dynamic_zone_source") == "presence" and presence_room:
        return presence_room, "effective_presence", unifi_zone
    return unifi_zone, "unifi_client", unifi_zone


def light_on_for_target(
    target_id: str,
    lights: list[dict[str, Any]],
    states: dict[str, bool | None],
) -> bool:
    return any(
        target_id in item.get("targets", []) and states.get(str(item.get("accessory"))) is True
        for item in lights
        if isinstance(item, dict)
    )


def evaluate_policy(
    *,
    target: dict[str, Any],
    probe: dict[str, Any],
    target_room: str | None,
    presence_room: str | None,
    light_on: bool,
    manual_override: bool,
    activity_hold_seconds: int,
    light_activity_hold_seconds: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    ineligible: list[str] = []
    if not probe.get("reachable"):
        ineligible.append("unreachable")
    if not probe.get("consoleUser") or probe.get("consoleUser") in {"loginwindow", "root"}:
        ineligible.append("logged_out")
    if probe.get("locked"):
        ineligible.append("locked")
    if target.get("ac_only") and not probe.get("onAcPower"):
        ineligible.append("battery_power")
    if target.get("require_lid_open") and probe.get("lidClosed"):
        ineligible.append("lid_closed")

    idle = probe.get("idleSeconds")
    if manual_override:
        reasons.append("manual_override")
    if target_room and presence_room == target_room:
        reasons.append("presence_room")
    if isinstance(idle, (int, float)) and idle <= activity_hold_seconds:
        reasons.append("recent_activity")
    if light_on and isinstance(idle, (int, float)) and idle <= light_activity_hold_seconds:
        reasons.append("light_plus_activity")
    return {
        "eligible": not ineligible,
        "hold": bool(reasons) and not ineligible,
        "reasons": reasons,
        "ineligibleReasons": ineligible,
    }


def read_mode(config: dict[str, Any], path: Path = MODE_PATH) -> str:
    mode = read_json(path).get("mode")
    return str(mode) if mode in {"shadow", "enforce"} else str(config.get("default_mode") or "shadow")


def read_enforce_targets(path: Path = MODE_PATH) -> set[str] | None:
    targets = read_json(path).get("enforceTargets")
    if not isinstance(targets, list):
        return None
    return {str(target) for target in targets if target}


def enforcement_enabled_for_target(mode: str, target_id: str, enforce_targets: set[str] | None) -> bool:
    return mode == "enforce" and (enforce_targets is None or target_id in enforce_targets)


def write_mode(
    mode: str,
    path: Path = MODE_PATH,
    *,
    enforce_targets: list[str] | None = None,
) -> dict[str, Any]:
    if mode not in {"shadow", "enforce"}:
        raise ValueError("mode must be shadow or enforce")
    payload: dict[str, Any] = {"mode": mode, "updatedAt": iso_now()}
    if mode == "enforce" and enforce_targets is not None:
        payload["enforceTargets"] = sorted(set(enforce_targets))
    write_private_json(path, payload)
    return {"ok": True, **payload, "path": str(path)}


def read_override(path: Path = OVERRIDE_PATH) -> bool:
    return read_json(path).get("enabled") is True


class LeaseManager:
    def __init__(
        self,
        *,
        lease_seconds: int,
        refresh_seconds: int,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.lease_seconds = lease_seconds
        self.refresh_seconds = refresh_seconds
        self.popen = popen
        self.now = now
        self.processes: dict[str, list[subprocess.Popen[Any]]] = {}
        self.last_started: dict[str, float] = {}

    def _command(self, target: dict[str, Any]) -> list[str]:
        caffeinate = ["/usr/bin/caffeinate", "-d", "-t", str(self.lease_seconds)]
        if target.get("local"):
            return caffeinate
        command = ["/usr/bin/ssh", "-o", "BatchMode=yes"]
        if target.get("host_key_alias"):
            command.extend(["-o", f"HostKeyAlias={target['host_key_alias']}"])
        return [*command, str(target["host"]), *caffeinate]

    def _reap(self, target_id: str) -> None:
        self.processes[target_id] = [process for process in self.processes.get(target_id, []) if process.poll() is None]

    def tick(self, target: dict[str, Any], hold: bool) -> bool:
        target_id = str(target["id"])
        self._reap(target_id)
        if not hold:
            self.stop(target_id)
            return False
        now = self.now()
        if now - self.last_started.get(target_id, 0) < self.refresh_seconds and self.processes.get(target_id):
            return True
        process = self.popen(self._command(target), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.setdefault(target_id, []).append(process)
        self.last_started[target_id] = now
        return True

    def stop(self, target_id: str) -> None:
        for process in self.processes.pop(target_id, []):
            if process.poll() is None:
                process.terminate()
        self.last_started.pop(target_id, None)

    def stop_all(self) -> None:
        for target_id in list(self.processes):
            self.stop(target_id)

    def expires_at(self, target_id: str) -> str | None:
        self._reap(target_id)
        started = self.last_started.get(target_id)
        if started is None or not self.processes.get(target_id):
            return None
        return datetime.fromtimestamp(started + self.lease_seconds, timezone.utc).astimezone().isoformat(timespec="seconds")


def event_state(status: dict[str, Any]) -> dict[str, Any]:
    presence = status.get("presence") if isinstance(status.get("presence"), dict) else {}
    targets = status.get("targets") if isinstance(status.get("targets"), dict) else {}
    return {
        "mode": status.get("mode"),
        "mappingConfigured": status.get("mappingConfigured"),
        "enrollment": status.get("enrollment"),
        "unifiOk": (status.get("unifi") or {}).get("ok") if isinstance(status.get("unifi"), dict) else None,
        "presence": {
            "homePresent": presence.get("homePresent"),
            "source": presence.get("source"),
            "confirmedSource": presence.get("confirmedSource"),
            "carriedSource": presence.get("carriedSource"),
            "zoneSource": presence.get("zoneSource"),
            "confirmedRoom": presence.get("confirmedRoom"),
            "pendingRoom": presence.get("pendingRoom"),
            "graceActive": presence.get("graceActive"),
        },
        "manualOverride": status.get("manualOverride"),
        "lights": status.get("lights"),
        "targets": {
            key: {
                "room": value.get("room"),
                "reachable": (value.get("probe") or {}).get("reachable") if isinstance(value.get("probe"), dict) else None,
                "locked": (value.get("probe") or {}).get("locked") if isinstance(value.get("probe"), dict) else None,
                "onAcPower": (value.get("probe") or {}).get("onAcPower") if isinstance(value.get("probe"), dict) else None,
                "lidClosed": (value.get("probe") or {}).get("lidClosed") if isinstance(value.get("probe"), dict) else None,
                "wouldHold": value.get("wouldHold"),
                "leaseActive": value.get("leaseActive"),
                "reasons": value.get("reasons"),
                "ineligibleReasons": value.get("ineligibleReasons"),
            }
            for key, value in targets.items()
            if isinstance(value, dict)
        },
    }


class DisplayAwakeManager:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or load_config())
        state_payload = read_json(DATA_DIR / "display_awake_presence_state.json")
        self.tracker = PresenceTracker(
            confirmation_polls=int(self.config.get("zone_confirmation_polls") or 2),
            grace_seconds=int(self.config.get("presence_grace_seconds") or 600),
            state=state_payload,
        )
        self.leases = LeaseManager(
            lease_seconds=int(self.config.get("lease_seconds") or 150),
            refresh_seconds=int(self.config.get("lease_refresh_seconds") or 90),
        )
        self.stopping = threading.Event()

    def cycle(self, *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        mode = read_mode(self.config)
        enforce_targets = read_enforce_targets() if mode == "enforce" else None
        enrollment = load_enrollment()
        room_mapping = load_room_mapping(self.config)
        clients: list[dict[str, Any]] = []
        aliases: dict[str, str] = {}
        unifi_error: str | None = None
        unifi_cache_used = False
        unifi_cache_age: float | None = None
        try:
            clients, aliases = query_unifi_clients()
        except Exception as exc:
            unifi_error = type(exc).__name__

        fresh_seconds = int(self.config.get("presence_fresh_seconds") or 90)
        if unifi_error:
            observations, unifi_cache_age = cached_presence_observations(
                UNIFI_OBSERVATIONS_CACHE_PATH,
                now=timestamp,
                max_age_seconds=int(self.config.get("unifi_observation_cache_seconds") or 120),
                fresh_seconds=fresh_seconds,
            )
            unifi_cache_used = bool(observations)
        else:
            observations = presence_observations(
                clients,
                aliases,
                enrollment,
                room_mapping,
                now=timestamp,
                fresh_seconds=fresh_seconds,
            )
            write_private_json(
                UNIFI_OBSERVATIONS_CACHE_PATH,
                {"version": 1, "capturedAtEpoch": timestamp, "observations": observations},
            )
        if not observations:
            observations = presence_observations(
                [],
                {},
                enrollment,
                room_mapping,
                now=timestamp,
                fresh_seconds=fresh_seconds,
            )
        presence = self.tracker.update(observations, timestamp)
        write_private_json(DATA_DIR / "display_awake_presence_state.json", self.tracker.state)

        previous = read_json(STATUS_PATH)
        configured_targets = [
            item for item in self.config.get("targets", []) if isinstance(item, dict) and item.get("id")
        ]
        targets = [target_with_live_unifi_host(item, clients) for item in configured_targets]
        probes = probe_targets(targets, connect_timeout=int(self.config.get("ssh_connect_timeout_seconds") or 5))
        lights = [item for item in self.config.get("lights", []) if isinstance(item, dict)]
        light_states = read_light_states(lights)
        manual_override = read_override()
        target_status: dict[str, dict[str, Any]] = {}
        for target in targets:
            target_id = str(target["id"])
            probe = probes.get(target_id, {"reachable": False, "error": "probe missing"})
            zone, zone_source, observed_unifi_zone = zone_for_target(
                target,
                probe,
                clients,
                aliases,
                room_mapping,
                presence.get("confirmedRoom"),
            )
            zone_cached = False
            if target.get("dynamic_room") and zone is None and unifi_cache_used:
                prior_target = (previous.get("targets") or {}).get(target_id)
                if isinstance(prior_target, dict) and prior_target.get("zone"):
                    zone = str(prior_target["zone"])
                    zone_source = "cached"
                    observed_unifi_zone = prior_target.get("observedUniFiZone")
                    zone_cached = True
            light_on = light_on_for_target(target_id, lights, light_states)
            decision = evaluate_policy(
                target=target,
                probe=probe,
                target_room=zone,
                presence_room=presence.get("confirmedRoom"),
                light_on=light_on,
                manual_override=manual_override,
                activity_hold_seconds=int(self.config.get("activity_hold_seconds") or 1800),
                light_activity_hold_seconds=int(self.config.get("light_activity_hold_seconds") or 7200),
            )
            enforcement_enabled = enforcement_enabled_for_target(mode, target_id, enforce_targets)
            lease_active = self.leases.tick(target, decision["hold"]) if enforcement_enabled else False
            if not enforcement_enabled:
                self.leases.stop(target_id)
            safe_probe = {key: value for key, value in probe.items() if key != "wifiMac"}
            target_status[target_id] = {
                "host": target.get("configured_host") or target.get("host"),
                "probeHostSource": target.get("probe_host_source") or "configured",
                "room": target.get("room"),
                "zone": zone,
                "zoneSource": zone_source,
                "observedUniFiZone": observed_unifi_zone,
                "zoneCached": zone_cached,
                "optional": target.get("optional") is True,
                "lightOn": light_on,
                "probe": safe_probe,
                "wouldHold": decision["hold"],
                "enforcementEnabled": enforcement_enabled,
                "leaseActive": lease_active,
                "leaseExpiresAt": self.leases.expires_at(target_id) if lease_active else None,
                **decision,
            }

        mapping_configured = bool(room_mapping)
        enrolled = {kind: kind in enrollment for kind in ("watch", "iphone")}
        unreachable_targets = sorted(
            target_id
            for target_id, value in target_status.items()
            if not value.get("optional") and not (value.get("probe") or {}).get("reachable")
        )
        optional_unavailable = sorted(
            target_id
            for target_id, value in target_status.items()
            if value.get("optional") and not (value.get("probe") or {}).get("reachable")
        )
        setup_required = [kind for kind, configured in enrolled.items() if not configured]
        if not mapping_configured:
            setup_required.append("home_mapping")
        degraded = (["unifi"] if unifi_error and not unifi_cache_used else []) + [
            f"unreachable:{target_id}" for target_id in unreachable_targets
        ]
        warnings = (["unifi_cached"] if unifi_cache_used else []) + [
            f"optional_unavailable:{target_id}" for target_id in optional_unavailable
        ]
        health_status = "degraded" if degraded else "setup_required" if setup_required else "healthy"
        status = sanitize(
            {
                "ok": True,
                "status": "shadow" if mode == "shadow" else "enforcing",
                "generatedAt": datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds"),
                "mode": mode,
                "enforceTargets": sorted(enforce_targets) if enforce_targets is not None else None,
                "mappingConfigured": mapping_configured,
                "enrollment": enrolled,
                "health": {
                    "status": health_status,
                    "setupRequired": setup_required,
                    "degradedReasons": degraded,
                    "warnings": warnings,
                },
                "unifi": {
                    "ok": unifi_error is None,
                    "error": unifi_error,
                    "cached": unifi_cache_used,
                    "cacheAgeSeconds": round(unifi_cache_age) if unifi_cache_age is not None else None,
                },
                "presence": {**presence, "devices": observations},
                "manualOverride": manual_override,
                "lights": light_states,
                "targets": target_status,
            }
        )
        digest_source = event_state(status)
        digest = hashlib.sha256(json.dumps(digest_source, sort_keys=True).encode()).hexdigest()
        status["eventDigest"] = digest
        write_private_json(STATUS_PATH, status)
        if previous.get("eventDigest") != digest:
            append_event(
                EVENTS_PATH,
                {
                    "timestamp": status["generatedAt"],
                    "mode": mode,
                    "mappingConfigured": mapping_configured,
                    "enrollment": enrolled,
                    "presence": presence,
                    "manualOverride": manual_override,
                    "targets": {
                        key: {
                            "room": value.get("room"),
                            "wouldHold": value.get("wouldHold"),
                            "leaseActive": value.get("leaseActive"),
                            "reasons": value.get("reasons"),
                            "ineligibleReasons": value.get("ineligibleReasons"),
                        }
                        for key, value in target_status.items()
                    },
                },
            )
        summary = build_observability_summary(
            read_json(SUMMARY_PATH),
            status,
            now=timestamp,
            max_sample_gap_seconds=max(120, int(self.config.get("poll_seconds") or 30) * 4),
        )
        write_private_json(SUMMARY_PATH, summary)
        return status

    def stop(self, *_args: Any) -> None:
        self.stopping.set()
        self.leases.stop_all()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        poll_seconds = max(5, int(self.config.get("poll_seconds") or 30))
        while not self.stopping.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except Exception as exc:
                write_private_json(
                    STATUS_PATH,
                    {"ok": False, "status": "failed", "generatedAt": iso_now(), "mode": read_mode(self.config), "error": type(exc).__name__},
                )
                self.leases.stop_all()
            self.stopping.wait(max(1, poll_seconds - (time.monotonic() - started)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage presence-aware macOS display sleep assertions.")
    parser.add_argument("--force-outside-runtime", action="store_true")
    parser.add_argument("--once", action="store_true", help="run one policy cycle")
    parser.add_argument("--list-candidates", action="store_true", help="list sanitized active Watch/iPhone candidates")
    parser.add_argument("--enroll-watch")
    parser.add_argument("--enroll-iphone")
    parser.add_argument("--set-room-map", action="append", default=[])
    parser.add_argument("--set-mode", choices=("shadow", "enforce"))
    parser.add_argument("--enforce-target", action="append", default=[])
    args = parser.parse_args()
    if not args.force_outside_runtime and not running_from_runtime_root():
        print(json.dumps({"ok": False, "error": "refusing to manage displays outside the runtime root"}, sort_keys=True))
        return 1

    if args.set_mode:
        if args.enforce_target and args.set_mode != "enforce":
            parser.error("--enforce-target requires --set-mode enforce")
        print(
            json.dumps(
                write_mode(args.set_mode, enforce_targets=args.enforce_target or None),
                sort_keys=True,
            )
        )
        return 0
    if args.set_room_map:
        print(json.dumps(write_room_mapping(args.set_room_map), sort_keys=True))
        return 0
    if args.list_candidates or args.enroll_watch or args.enroll_iphone:
        clients, aliases = query_unifi_clients()
        if args.enroll_watch or args.enroll_iphone:
            if not args.enroll_watch or not args.enroll_iphone:
                parser.error("--enroll-watch and --enroll-iphone must be provided together")
            print(
                json.dumps(
                    enroll_devices(clients, watch_token=args.enroll_watch, iphone_token=args.enroll_iphone),
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps({"ok": True, "candidates": sanitized_candidates(clients, aliases)}, sort_keys=True))
        return 0

    manager = DisplayAwakeManager()
    if args.once:
        print(json.dumps(manager.cycle(), sort_keys=True))
        return 0
    manager.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
