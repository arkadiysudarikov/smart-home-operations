#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
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


class ChargePointConfigMissing(RuntimeError):
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


def iso_dt(value: str | None) -> str | None:
    dt = parse_dt(value)
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


def soap_envelope(query: dict[str, Any]) -> bytes:
    fields = "\n".join(f"          <{key}>{escape_xml(str(value))}</{key}>" for key, value in query.items() if value)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:dictionary:com.chargepoint.webservices">
  <soapenv:Header/>
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
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        endpoint,
        data=soap_envelope(query),
        headers={
            "Authorization": f"Basic {auth}",
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
        "source": f"ChargePoint {fetch_result['mode']} API",
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
                ],
                "existingSource": str(SOURCE_PATH) if SOURCE_PATH.exists() else None,
            }
        )
        return 0
    except urllib.error.HTTPError as exc:
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
