#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "chargepoint.json"
DATA_DIR = ROOT / "data"
SOURCE_PATH = DATA_DIR / "chargepoint_sessions.json"
STATUS_PATH = DATA_DIR / "latest_chargepoint_refresh.json"
WEBSERVICES_URL = "https://webservices.chargepoint.com/webservices/chargepoint/services/5.0"
SOAP_ACTION = "urn:provider/interface/chargepointservices/getChargingSessionData"
DISCOVERY_URL = "https://discovery.chargepoint.com/discovery/v3/globalconfig"
DEFAULT_DRIVER_REFRESH_MINUTES = 180
DEFAULT_DRIVER_RETRY_MINUTES = 360


class ChargePointConfigMissing(RuntimeError):
    pass


class ChargePointDriverLoginBlocked(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def env_or_config(config: dict[str, Any], env_name: str, config_name: str, default: Any = None) -> Any:
    return os.environ.get(env_name) or config.get(config_name, default)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_status(payload: dict[str, Any]) -> None:
    write_json(STATUS_PATH, payload)


def text_at(row: ET.Element, name: str) -> str | None:
    for child in row.iter():
        if child.tag.split("}")[-1] == name and child.text:
            return child.text.strip()
    return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def parse_chargepoint_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
    return parse_dt(str(value))


def iso_dt(value: str | None) -> str | None:
    dt = parse_dt(value)
    return dt.isoformat(timespec="seconds") if dt else None


def iso_chargepoint_time(value: Any) -> str | None:
    dt = parse_chargepoint_time(value)
    return dt.isoformat(timespec="seconds") if dt else None


def duration_label(start_at: str | None, end_at: str | None) -> str | None:
    start = parse_dt(start_at)
    end = parse_dt(end_at)
    if not start or not end:
        return None
    total = max(0, int((end - start).total_seconds()))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}h {minutes}m {seconds}s"


def duration_label_from_ms(value: Any) -> str | None:
    seconds_float = parse_float(value)
    if seconds_float is None:
        return None
    total = max(0, int(seconds_float / 1000))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}h {minutes}m {seconds}s"


def keychain_password(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.rstrip("\n")


def read_secret(config: dict[str, Any], value_name: str, env_name: str, service_name: str, account_name: str) -> str | None:
    if os.environ.get(env_name):
        return os.environ[env_name]
    if config.get(value_name):
        return str(config[value_name])
    service = config.get(service_name)
    account = config.get(account_name) or config.get("username")
    if service and account:
        return keychain_password(str(service), str(account))
    return None


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartHomeMonitor/1.0",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def last_source_age_minutes() -> float | None:
    previous = load_previous()
    captured_at = parse_dt(previous.get("capturedAt"))
    if not captured_at:
        return None
    return (datetime.now(timezone.utc).astimezone() - captured_at).total_seconds() / 60


def last_status_age_minutes(status: str) -> float | None:
    if not STATUS_PATH.exists():
        return None
    try:
        payload = json.loads(STATUS_PATH.read_text())
    except json.JSONDecodeError:
        return None
    if payload.get("status") != status:
        return None
    finished_at = parse_dt(payload.get("finishedAt"))
    if not finished_at:
        return None
    return (datetime.now(timezone.utc).astimezone() - finished_at).total_seconds() / 60


def should_skip_driver_refresh(config: dict[str, Any]) -> tuple[bool, str | None]:
    if os.environ.get("CHARGEPOINT_FORCE"):
        return False, None
    min_refresh_minutes = int(env_or_config(config, "CHARGEPOINT_DRIVER_MIN_REFRESH_MINUTES", "driver_min_refresh_minutes", DEFAULT_DRIVER_REFRESH_MINUTES))
    current_age = last_source_age_minutes()
    if current_age is not None and current_age < min_refresh_minutes:
        return True, f"Last ChargePoint source refresh is {current_age:.1f} minutes old; minimum driver portal interval is {min_refresh_minutes} minutes."
    retry_minutes = int(env_or_config(config, "CHARGEPOINT_DRIVER_RETRY_MINUTES", "driver_retry_minutes", DEFAULT_DRIVER_RETRY_MINUTES))
    blocked_age = last_status_age_minutes("driver_login_blocked")
    if blocked_age is not None and blocked_age < retry_minutes:
        return True, f"Previous ChargePoint driver login was blocked {blocked_age:.1f} minutes ago; retry interval is {retry_minutes} minutes."
    return False, None


def address_from_parts(row: ET.Element) -> str | None:
    parts = [
        text_at(row, "Address"),
        text_at(row, "City"),
        text_at(row, "State"),
        text_at(row, "postalCode"),
    ]
    clean = [part for part in parts if part]
    return ", ".join(clean) if clean else None


def session_from_soap(row: ET.Element, config: dict[str, Any]) -> dict[str, Any] | None:
    start_at = iso_dt(text_at(row, "startTime"))
    end_at = iso_dt(text_at(row, "endTime"))
    energy_kwh = parse_float(text_at(row, "Energy"))
    if not start_at or energy_kwh is None:
        return None
    station_name = text_at(row, "stationName")
    station_id = text_at(row, "stationID")
    station = " / ".join(part for part in [station_name, station_id] if part)
    session = {
        "startAt": start_at,
        "endAt": end_at,
        "duration": duration_label(start_at, end_at),
        "stationType": config.get("station_type") or "Home",
        "station": station or station_id or station_name,
        "address": address_from_parts(row),
        "energyKwh": energy_kwh,
        "costUsd": None,
        "sourcePrecision": "api",
    }
    for source_name, target_name in [
        ("sessionID", "sessionId"),
        ("portNumber", "portNumber"),
        ("userID", "userId"),
        ("recordNumber", "recordNumber"),
        ("credentialID", "credentialId"),
    ]:
        value = text_at(row, source_name)
        if value:
            session[target_name] = value
    return {key: value for key, value in session.items() if value is not None}


def soap_envelope(query: dict[str, Any], username: str, password: str) -> bytes:
    fields = "\n".join(f"          <{key}>{escape_xml(str(value))}</{key}>" for key, value in query.items() if value)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:dictionary:com.chargepoint.webservices" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <soapenv:Header>
    <wsse:Security soapenv:mustUnderstand="1">
      <wsse:UsernameToken>
        <wsse:Username>{escape_xml(username)}</wsse:Username>
        <wsse:Password>{escape_xml(password)}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
  <soapenv:Body>
    <urn:getChargingSessionData>
      <searchQuery>
{fields}
      </searchQuery>
    </urn:getChargingSessionData>
  </soapenv:Body>
</soapenv:Envelope>
"""
    return body.encode("utf-8")


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def configured_webservices(config: dict[str, Any]) -> dict[str, Any]:
    username = env_or_config(config, "CHARGEPOINT_WS_USERNAME", "username")
    password = env_or_config(config, "CHARGEPOINT_WS_PASSWORD", "password")
    station_id = env_or_config(config, "CHARGEPOINT_STATION_ID", "station_id")
    if not username or not password:
        raise ChargePointConfigMissing("Configure ChargePoint Web Services username/password before API pulls can run.")
    lookback_days = int(env_or_config(config, "CHARGEPOINT_LOOKBACK_DAYS", "lookback_days", 120))
    end_at = env_or_config(config, "CHARGEPOINT_TO", "to")
    start_at = env_or_config(config, "CHARGEPOINT_FROM", "from")
    end_dt = parse_dt(end_at) if end_at else datetime.now(timezone.utc).astimezone()
    start_dt = parse_dt(start_at) if start_at else end_dt - timedelta(days=lookback_days)
    query: dict[str, Any] = {
        "fromTimeStamp": start_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "toTimeStamp": end_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "stationID": station_id,
    }
    return {
        "username": username,
        "password": password,
        "endpoint": env_or_config(config, "CHARGEPOINT_WS_ENDPOINT", "endpoint", WEBSERVICES_URL),
        "query": query,
        "max_pages": int(env_or_config(config, "CHARGEPOINT_MAX_PAGES", "max_pages", 20)),
    }


def request_soap(endpoint: str, username: str, password: str, query: dict[str, Any]) -> ET.Element:
    request = urllib.request.Request(
        endpoint,
        data=soap_envelope(query, username, password),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": SOAP_ACTION,
            "User-Agent": "SmartHomeMonitor/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return ET.fromstring(response.read())


def fetch_webservices(config: dict[str, Any]) -> dict[str, Any]:
    settings = configured_webservices(config)
    sessions: list[dict[str, Any]] = []
    query = dict(settings["query"])
    requested_pages = 0
    response_text = None
    response_code = None

    for page in range(settings["max_pages"]):
        query["startRecord"] = page * 100 + 1
        root = request_soap(settings["endpoint"], settings["username"], settings["password"], query)
        requested_pages += 1
        response_code = next((child.text for child in root.iter() if child.tag.split("}")[-1] == "responseCode"), None)
        response_text = next((child.text for child in root.iter() if child.tag.split("}")[-1] == "responseText"), None)
        for row in root.iter():
            if row.tag.split("}")[-1] != "ChargingSessionData":
                continue
            session = session_from_soap(row, config)
            if session:
                sessions.append(session)
        more_flag = next((child.text for child in root.iter() if child.tag.split("}")[-1] == "MoreFlag"), None)
        if str(more_flag or "0").strip() != "1":
            break

    if response_code and response_code != "100":
        raise RuntimeError(f"ChargePoint API returned responseCode={response_code}: {response_text or 'no responseText'}")
    return {
        "mode": "webservices",
        "requestedPages": requested_pages,
        "responseCode": response_code,
        "responseText": response_text,
        "sessions": sessions,
    }


def get_path(payload: Any, path: list[str | int]) -> Any:
    current = payload
    for key in path:
        if isinstance(current, list) and isinstance(key, int):
            current = current[key]
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def session_from_json(row: dict[str, Any], field_map: dict[str, str], config: dict[str, Any]) -> dict[str, Any] | None:
    def value(name: str) -> Any:
        source = field_map.get(name, name)
        return get_path(row, source.split(".")) if "." in source else row.get(source)

    start_at = iso_dt(value("startAt"))
    end_at = iso_dt(value("endAt"))
    energy_kwh = parse_float(value("energyKwh"))
    if not start_at or energy_kwh is None:
        return None
    return {
        "startAt": start_at,
        "endAt": end_at,
        "duration": value("duration") or duration_label(start_at, end_at),
        "stationType": value("stationType") or config.get("station_type") or "Home",
        "station": value("station"),
        "address": value("address"),
        "energyKwh": energy_kwh,
        "costUsd": parse_float(value("costUsd")),
        "sourcePrecision": value("sourcePrecision") or "api",
    }


def fetch_json_api(config: dict[str, Any]) -> dict[str, Any]:
    url = env_or_config(config, "CHARGEPOINT_JSON_URL", "json_url")
    if not url:
        raise ChargePointConfigMissing("Configure json_url or Web Services credentials before ChargePoint pulls can run.")
    method = str(env_or_config(config, "CHARGEPOINT_JSON_METHOD", "json_method", "GET")).upper()
    headers = dict(config.get("json_headers") or {})
    if os.environ.get("CHARGEPOINT_JSON_AUTHORIZATION"):
        headers["Authorization"] = os.environ["CHARGEPOINT_JSON_AUTHORIZATION"]
    if os.environ.get("CHARGEPOINT_COULOMB_SESS"):
        headers["Cookie"] = f"coulomb_sess={os.environ['CHARGEPOINT_COULOMB_SESS']}"
    body = config.get("json_body")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers.setdefault("Accept", "application/json")
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    sessions_path = config.get("json_sessions_path") or ["sessions"]
    rows = get_path(payload, sessions_path)
    if not isinstance(rows, list):
        raise RuntimeError(f"JSON response did not contain a session list at {sessions_path}")
    field_map = config.get("json_field_map") or {}
    sessions = [session for row in rows if isinstance(row, dict) for session in [session_from_json(row, field_map, config)] if session]
    return {"mode": "json", "sessions": sessions, "responseRows": len(rows)}


def endpoint_value(discovery: dict[str, Any], key: str, default: str) -> str:
    endpoints = discovery.get("endPoints") or discovery.get("endpoints") or {}
    value = endpoints.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    if not value:
        return default
    return str(value)


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def driver_device_data(username: str) -> dict[str, Any]:
    return {
        "app": "web-driver",
        "device_id": f"smart-home-monitor-{username}",
        "device_type": "browser",
        "locale": "en-US",
        "os": "macOS",
    }


def driver_discovery(username: str) -> dict[str, Any]:
    return post_json(
        DISCOVERY_URL,
        {
            "deviceData": driver_device_data(username),
            "username": username,
        },
    )


def driver_login(accounts_endpoint: str, username: str, password: str) -> dict[str, Any]:
    login_url = join_url(accounts_endpoint, "v2/driver/profile/account/login")
    try:
        return post_json(
            login_url,
            {
                "deviceData": driver_device_data(username),
                "password": password,
                "username": username,
            },
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and ("datadome" in body.lower() or "captcha" in body.lower()):
            raise ChargePointDriverLoginBlocked("ChargePoint driver login was blocked by DataDome/CAPTCHA; keeping the previous local session file.") from exc
        raise


def extract_session_token(login_payload: dict[str, Any]) -> str | None:
    candidates = [
        login_payload.get("sessionId"),
        login_payload.get("session_id"),
        get_path(login_payload, ["user", "sessionId"]),
        get_path(login_payload, ["driver", "sessionId"]),
        get_path(login_payload, ["account", "sessionId"]),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return None


def region_from_token(token: str, discovery: dict[str, Any]) -> str:
    if "#R" in token:
        return token.rsplit("#R", 1)[-1]
    region = discovery.get("region") or discovery.get("locale")
    return str(region or "NA-US")


def session_from_driver(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    start_at = iso_chargepoint_time(row.get("start_time"))
    end_at = iso_chargepoint_time(row.get("end_time"))
    energy_kwh = parse_float(row.get("energy_kwh"))
    if not start_at or energy_kwh is None:
        return None
    address = ", ".join(str(part) for part in [row.get("address1"), row.get("city")] if part)
    currency = row.get("currency_iso_code")
    cost = parse_float(row.get("total_amount"))
    session = {
        "startAt": start_at,
        "endAt": end_at,
        "duration": duration_label_from_ms(row.get("session_time")) or duration_label(start_at, end_at),
        "stationType": "Home" if row.get("is_home_charger") else "Public",
        "station": row.get("device_name"),
        "address": address or None,
        "energyKwh": energy_kwh,
        "costUsd": cost if currency in (None, "USD") else None,
        "sourcePrecision": "api",
    }
    for source_name, target_name in [
        ("session_id_string", "sessionId"),
        ("session_id", "sessionId"),
        ("device_id", "deviceId"),
        ("company_name", "companyName"),
        ("payment_type", "paymentType"),
    ]:
        value = row.get(source_name)
        if value and target_name not in session:
            session[target_name] = value
    if currency and currency != "USD":
        session["currency"] = currency
        session["cost"] = cost
    if config.get("station_type") and row.get("is_home_charger"):
        session["stationType"] = config["station_type"]
    return {key: value for key, value in session.items() if value is not None}


def driver_charging_activity(mapcache_endpoint: str, token: str, region: str, max_pages: int) -> tuple[list[dict[str, Any]], int]:
    sessions: list[dict[str, Any]] = []
    page_offset: int | str | None = 0
    pages = 0
    headers = {
        "Accept-Language": "en-US",
        "Cookie": f"coulomb_sess={token}",
        "CP-Region": region,
        "CP-Session-Token": token,
        "CP-Session-Type": "CP_SESSION_TOKEN",
        "X-Requested-With": "XMLHttpRequest",
    }
    while page_offset != "last_page" and pages < max_pages:
        payload = post_json(
            join_url(mapcache_endpoint, "v2"),
            {
                "charging_activity_monthly": {
                    "page_offset": page_offset,
                    "page_size": 20,
                    "show_address_for_home_sessions": True,
                }
            },
            headers=headers,
        )
        activity = payload.get("charging_activity_monthly") or {}
        error = activity.get("error_message") or activity.get("error")
        if error:
            raise RuntimeError(f"ChargePoint charging activity returned an error: {error}")
        for month in activity.get("month_info") or []:
            for row in month.get("sessions") or []:
                if isinstance(row, dict):
                    sessions.append(row)
        pages += 1
        page_offset = activity.get("page_offset", "last_page")
    return sessions, pages


def fetch_driver_portal(config: dict[str, Any]) -> dict[str, Any]:
    skip, skip_detail = should_skip_driver_refresh(config)
    if skip:
        return {
            "mode": "driver_portal",
            "sessions": [],
            "skipped": True,
            "status": "fresh_enough",
            "detail": skip_detail,
        }
    username = env_or_config(config, "CHARGEPOINT_USERNAME", "username")
    password = read_secret(
        config,
        "password",
        "CHARGEPOINT_PASSWORD",
        "password_keychain_service",
        "password_keychain_account",
    )
    if not username or not password:
        raise ChargePointConfigMissing("Configure ChargePoint driver portal username and Keychain password reference before portal pulls can run.")
    discovery = driver_discovery(str(username))
    accounts_endpoint = endpoint_value(discovery, "accounts_endpoint", "https://account.chargepoint.com/account/")
    mapcache_endpoint = endpoint_value(discovery, "mapcache_endpoint", "https://mc.chargepoint.com/map-prod/")
    login_payload = driver_login(accounts_endpoint, str(username), password)
    token = extract_session_token(login_payload)
    if not token:
        raise RuntimeError("ChargePoint driver login did not return a session token.")
    raw_sessions, pages = driver_charging_activity(
        mapcache_endpoint,
        token,
        region_from_token(token, discovery),
        int(env_or_config(config, "CHARGEPOINT_MAX_PAGES", "max_pages", 10)),
    )
    sessions = [session for row in raw_sessions for session in [session_from_driver(row, config)] if session]
    return {
        "mode": "driver_portal",
        "sessions": sessions,
        "responseRows": len(raw_sessions),
        "requestedPages": pages,
    }


def load_previous() -> dict[str, Any]:
    if not SOURCE_PATH.exists():
        return {}
    try:
        return json.loads(SOURCE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def merge_payload(fetch_result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    previous = load_previous()
    sessions = sorted(fetch_result["sessions"], key=lambda item: item["startAt"], reverse=True)
    energy_total = round(sum(float(session.get("energyKwh") or 0) for session in sessions), 3)
    costs = [float(session["costUsd"]) for session in sessions if session.get("costUsd") is not None]
    payload = {
        "capturedAt": now(),
        "source": "ChargePoint driver portal charging-activity API" if fetch_result["mode"] == "driver_portal" else f"ChargePoint {fetch_result['mode']} API",
        "sessions": sessions,
        "senseEvEvents": previous.get("senseEvEvents", []),
        "visibleTotals": {
            "sessionCount": len(sessions),
            "energyKwh": energy_total,
            "costUsd": round(sum(costs), 2) if costs else None,
        },
    }
    if config.get("source_note"):
        payload["sourceNote"] = config["source_note"]
    return payload


def main() -> int:
    started_at = now()
    config = load_config()
    mode = str(env_or_config(config, "CHARGEPOINT_MODE", "mode", "webservices")).lower()
    try:
        if mode in {"webservices", "soap"}:
            result = fetch_webservices(config)
        elif mode == "json":
            result = fetch_json_api(config)
        elif mode in {"driver_portal", "portal", "driver"}:
            result = fetch_driver_portal(config)
        else:
            raise RuntimeError(f"Unsupported ChargePoint mode: {mode}")
    except ChargePointConfigMissing as exc:
        write_status(
            {
                "ok": None,
                "status": "registration_required",
                "startedAt": started_at,
                "finishedAt": now(),
                "detail": str(exc),
                "configPath": str(CONFIG_PATH),
                "requiredConfig": [
                    "mode=webservices with username/password and optional station_id",
                    "or mode=json with json_url/json_headers/json_sessions_path/json_field_map",
                    "or mode=driver_portal with username and password_keychain_service/password_keychain_account",
                ],
                "existingSource": str(SOURCE_PATH) if SOURCE_PATH.exists() else None,
            }
        )
        return 0
    except ChargePointDriverLoginBlocked as exc:
        write_status(
            {
                "ok": None,
                "status": "driver_login_blocked",
                "startedAt": started_at,
                "finishedAt": now(),
                "detail": str(exc),
                "existingSource": str(SOURCE_PATH) if SOURCE_PATH.exists() else None,
            }
        )
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and ("datadome" in body.lower() or "captcha" in body.lower()):
            write_status(
                {
                    "ok": None,
                    "status": "driver_login_blocked",
                    "startedAt": started_at,
                    "finishedAt": now(),
                    "detail": "ChargePoint returned DataDome/CAPTCHA protection; keeping the previous local session file.",
                    "existingSource": str(SOURCE_PATH) if SOURCE_PATH.exists() else None,
                }
            )
            return 0
        write_status(
            {
                "ok": False,
                "status": "http_error",
                "startedAt": started_at,
                "finishedAt": now(),
                "statusCode": exc.code,
                "reason": exc.reason,
            }
        )
        return 1
    except Exception as exc:
        write_status(
            {
                "ok": False,
                "status": "failed",
                "startedAt": started_at,
                "finishedAt": now(),
                "error": str(exc),
            }
        )
        return 1

    if result.get("skipped"):
        write_status(
            {
                "ok": None,
                "status": result.get("status") or "skipped",
                "startedAt": started_at,
                "finishedAt": now(),
                "detail": result.get("detail"),
                "mode": result["mode"],
                "existingSource": str(SOURCE_PATH) if SOURCE_PATH.exists() else None,
            }
        )
        return 0

    if not result["sessions"]:
        write_status(
            {
                "ok": None,
                "status": "no_sessions",
                "startedAt": started_at,
                "finishedAt": now(),
                "detail": "ChargePoint API returned no usable sessions; keeping the previous local session file.",
                "mode": result["mode"],
            }
        )
        return 0

    payload = merge_payload(result, config)
    write_json(SOURCE_PATH, payload)
    write_status(
        {
            "ok": True,
            "status": "downloaded",
            "startedAt": started_at,
            "finishedAt": now(),
            "mode": result["mode"],
            "sessions": len(payload["sessions"]),
            "energyKwh": payload["visibleTotals"]["energyKwh"],
            "sourceFile": str(SOURCE_PATH),
        }
    )
    print(SOURCE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
