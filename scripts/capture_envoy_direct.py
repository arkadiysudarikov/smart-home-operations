#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
import xml.etree.ElementTree as ET
import base64
import plistlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "latest_envoy_direct.json"
TOKEN_CACHE_PATH = DATA_DIR / "envoy_token.json"
HOMEBRIDGE_CONFIG_PATH = Path.home() / ".homebridge" / "config.json"
ENLIGHTEN_BASE = "https://enlighten.enphaseenergy.com"
ENLIGHTEN_PREFS_GLOB = "*/Data/Library/Preferences/com.enphaseenergy.MyEnlighten.plist"
ENLIGHTEN_PREFS_KNOWN_PATHS = (
    Path.home()
    / "Library"
    / "Containers"
    / "3816A446-32F5-4CA2-BDA7-50D50D942869"
    / "Data"
    / "Library"
    / "Preferences"
    / "com.enphaseenergy.MyEnlighten.plist",
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def envoy_hosts(config: dict[str, Any]) -> list[str]:
    hosts = (config.get("network") or {}).get("envoy_candidates") or []
    clean = [str(host).strip() for host in hosts if str(host).strip()]
    return clean or ["envoy.local", "envoy.localdomain"]


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def epoch_to_local(value: Any) -> str | None:
    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(timespec="seconds")


def cached_token() -> tuple[str | None, dict[str, Any]]:
    payload = load_json(TOKEN_CACHE_PATH)
    token_value = payload.get("token")
    expires_at = payload.get("expires_at")
    if not isinstance(token_value, str) or not token_value:
        return None, {}
    try:
        expires_epoch = int(expires_at)
    except (TypeError, ValueError):
        return None, {}
    if expires_epoch <= int(datetime.now(timezone.utc).timestamp()) + 300:
        return None, payload
    return token_value, payload


def configured_token(config: dict[str, Any]) -> tuple[str | None, str, dict[str, Any]]:
    value = os.environ.get("ENPHASE_ENVOY_TOKEN")
    if value:
        return value, "environment", {}
    envoy = config.get("envoy") or {}
    if isinstance(envoy, dict) and envoy.get("token"):
        return str(envoy["token"]), "sources_config", {}
    value, metadata = cached_token()
    if value:
        return value, "cache", metadata
    if os.environ.get("ENPHASE_READ_APP_CONTAINER") != "1":
        return None, "none", {"skippedAppContainer": True}
    value, metadata = enlighten_site_data_token()
    if value:
        return value, "enlighten_site_data", metadata
    return None, "none", {}


def jwt_expiry(token_value: str) -> int | None:
    parts = token_value.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode()).decode("utf-8"))
    except Exception:
        return None
    exp = decoded.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def enlighten_site_data_token() -> tuple[str | None, dict[str, Any]]:
    containers = Path.home() / "Library" / "Containers"
    prefs_paths = [path for path in ENLIGHTEN_PREFS_KNOWN_PATHS if path.exists()]
    if os.environ.get("ENPHASE_SCAN_CONTAINERS") == "1":
        prefs_paths.extend(containers.glob(ENLIGHTEN_PREFS_GLOB))
    for prefs_path in prefs_paths:
        try:
            payload = plistlib.load(prefs_path.open("rb"))
            encoded = payload.get("site_data")
            if isinstance(encoded, bytes):
                encoded = encoded.decode("utf-8")
            if not isinstance(encoded, str):
                continue
            encoded = json.loads(encoded)
            site_data = json.loads(urllib.parse.unquote(base64.b64decode(encoded).decode("utf-8")))
        except Exception:
            continue
        for envoy in site_data.get("envoy") or []:
            token_value = envoy.get("entrez_token")
            if token_value:
                expires_at = jwt_expiry(str(token_value))
                metadata = {
                    "ok": True,
                    "source": "enlighten_site_data",
                    "serialNumber": envoy.get("serialNumber"),
                    "partNumber": envoy.get("part_num"),
                    "siteId": site_data.get("site_id"),
                    "userId": site_data.get("user_id"),
                    "expires_at": expires_at,
                    "expiresAt": epoch_to_local(expires_at),
                }
                if expires_at:
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    TOKEN_CACHE_PATH.write_text(
                        json.dumps({**metadata, "token": str(token_value)}, indent=2, sort_keys=True) + "\n"
                    )
                return str(token_value), metadata
    return None, {}


def homebridge_enphase_device() -> dict[str, Any]:
    config = load_json(HOMEBRIDGE_CONFIG_PATH)
    for platform in config.get("platforms") or []:
        if platform.get("platform") != "enphaseEnvoy":
            continue
        for device in platform.get("devices") or []:
            if device.get("enlightenUser") and device.get("enlightenPasswd"):
                return device
    return {}


def post_form(url: str, form: dict[str, str]) -> tuple[int | None, str, dict[str, Any], str | None]:
    body = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "SmartHomeMonitor/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            text = response.read().decode("utf-8", errors="replace")
            cookies = response.headers.get_all("Set-Cookie") or []
            return response.status, "; ".join(cookie.split(";", 1)[0] for cookie in cookies), json.loads(text), None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return exc.code, "", {}, text.splitlines()[0][:160] if text else exc.reason
    except Exception as exc:
        return None, "", {}, str(exc)


def get_json(url: str, headers: dict[str, str]) -> tuple[int | None, dict[str, Any], str | None]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.status, json.loads(response.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return exc.code, {}, text.splitlines()[0][:160] if text else exc.reason
    except Exception as exc:
        return None, {}, str(exc)


def generate_token(serial_number: str | None) -> tuple[str | None, dict[str, Any]]:
    device = homebridge_enphase_device()
    if not serial_number or not device:
        return None, {"ok": False, "source": "homebridge_config", "error": "missing serial number or Homebridge Enphase credentials"}

    login_status, cookie, _, login_error = post_form(
        f"{ENLIGHTEN_BASE}/login/login.json",
        {
            "user[email]": str(device["enlightenUser"]),
            "user[password]": str(device["enlightenPasswd"]),
        },
    )
    if login_status != 200 or not cookie:
        return None, {"ok": False, "source": "enlighten_login", "status": login_status, "error": login_error or "missing login cookie"}

    token_status, token_payload, token_error = get_json(
        f"{ENLIGHTEN_BASE}/entrez-auth-token?{urllib.parse.urlencode({'serial_num': serial_number})}",
        {"Accept": "application/json", "Cookie": cookie, "User-Agent": "SmartHomeMonitor/1.0"},
    )
    token_value = token_payload.get("token")
    if token_status != 200 or not token_value:
        return None, {"ok": False, "source": "entrez_auth_token", "status": token_status, "error": token_error or "missing token"}

    metadata = {
        "ok": True,
        "source": "generated",
        "generatedAt": now(),
        "serialNumber": serial_number,
        "token": token_value,
        "expires_at": token_payload.get("expires_at"),
        "generation_time": token_payload.get("generation_time"),
        "expiresAt": epoch_to_local(token_payload.get("expires_at")),
        "installer": (token_payload.get("expires_at") or 0) - (token_payload.get("generation_time") or 0) == 43200,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return str(token_value), metadata


def request_json(url: str, bearer: str | None = None) -> tuple[int | None, Any, str | None]:
    headers = {"Accept": "application/json", "User-Agent": "SmartHomeMonitor/1.0"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, headers=headers)
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=4, context=context) as response:
            body = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type or body.lstrip().startswith(("{", "[")):
                return response.status, json.loads(body), None
            try:
                return response.status, json.loads(body), None
            except json.JSONDecodeError:
                pass
            if body.lstrip().startswith("<?xml") or body.lstrip().startswith("<envoy_info"):
                return response.status, parse_envoy_info_xml(body), None
            return response.status, body[:500], None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, None, body.splitlines()[0][:160] if body else exc.reason
    except Exception as exc:
        return None, None, str(exc)


def parse_envoy_info_xml(body: str) -> dict[str, Any]:
    root = ET.fromstring(body)
    device = root.find("device")

    def text(parent: ET.Element | None, name: str) -> str | None:
        child = parent.find(name) if parent is not None else None
        return child.text.strip() if child is not None and child.text else None

    return {
        "serial_num": text(device, "sn"),
        "software": text(device, "software"),
        "imeter": text(device, "imeter") == "true",
        "web-tokens": text(root, "web-tokens") == "true",
    }


def probe_host(host: str, bearer: str | None) -> dict[str, Any]:
    base = f"https://{host}"
    info_code, info, info_error = request_json(f"{base}/info")
    production_code, production, production_error = request_json(f"{base}/production.json", bearer)
    meters_code, meters, meters_error = request_json(f"{base}/ivp/meters/readings", bearer)
    auth_required = production_code in {401, 403} or meters_code in {401, 403}
    return {
        "host": host,
        "reachable": info_code == 200,
        "infoStatus": info_code,
        "productionStatus": production_code,
        "metersStatus": meters_code,
        "authRequired": auth_required,
        "info": info if isinstance(info, dict) else None,
        "production": production if isinstance(production, dict) else None,
        "meters": meters if isinstance(meters, list) else None,
        "errors": {
            "info": info_error,
            "production": production_error,
            "meters": meters_error,
        },
    }


def main() -> int:
    started_at = now()
    config = load_json(CONFIG_PATH)
    bearer, token_source, token_metadata = configured_token(config)
    probes = [probe_host(host, bearer) for host in envoy_hosts(config)]
    selected = next((probe for probe in probes if probe.get("reachable")), probes[0] if probes else {})
    info = selected.get("info") if isinstance(selected.get("info"), dict) else {}

    if not bearer and selected.get("reachable") and info.get("web-tokens"):
        bearer, token_metadata = generate_token(info.get("serial_num"))
        token_source = token_metadata.get("source") or "generated"
        if bearer:
            probes = [probe_host(host, bearer) for host in envoy_hosts(config)]
            selected = next((probe for probe in probes if probe.get("reachable")), probes[0] if probes else {})
            info = selected.get("info") if isinstance(selected.get("info"), dict) else {}

    has_live_data = bool(selected.get("production") or selected.get("meters"))
    auth_required = bool(selected.get("authRequired"))
    ok = bool(selected.get("reachable") and (has_live_data or auth_required))
    status = "live" if has_live_data else ("auth_required" if auth_required else ("reachable" if selected.get("reachable") else "unreachable"))
    payload = {
        "ok": ok,
        "status": status,
        "startedAt": started_at,
        "finishedAt": now(),
        "host": selected.get("host"),
        "serialNumber": info.get("serial_num"),
        "software": info.get("software"),
        "webTokens": info.get("web-tokens"),
        "imeter": info.get("imeter"),
        "hasToken": bool(bearer),
        "tokenSource": token_source,
        "tokenExpiresAt": token_metadata.get("expiresAt") if isinstance(token_metadata, dict) else None,
        "tokenGenerationOk": token_metadata.get("ok") if isinstance(token_metadata, dict) and token_source not in {"cache", "environment", "sources_config"} else None,
        "tokenGenerationError": token_metadata.get("error") if isinstance(token_metadata, dict) else None,
        "probes": probes,
    }
    write_status(payload)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
