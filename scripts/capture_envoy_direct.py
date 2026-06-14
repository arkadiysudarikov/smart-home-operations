#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
import xml.etree.ElementTree as ET
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "latest_envoy_direct.json"


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


def token(config: dict[str, Any]) -> str | None:
    value = os.environ.get("ENPHASE_ENVOY_TOKEN")
    if value:
        return value
    envoy = config.get("envoy") or {}
    if isinstance(envoy, dict) and envoy.get("token"):
        return str(envoy["token"])
    return None


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
    bearer = token(config)
    probes = [probe_host(host, bearer) for host in envoy_hosts(config)]
    selected = next((probe for probe in probes if probe.get("reachable")), probes[0] if probes else {})
    has_live_data = bool(selected.get("production") or selected.get("meters"))
    auth_required = bool(selected.get("authRequired"))
    ok = bool(selected.get("reachable") and (has_live_data or auth_required))
    status = "live" if has_live_data else ("auth_required" if auth_required else ("reachable" if selected.get("reachable") else "unreachable"))
    info = selected.get("info") if isinstance(selected.get("info"), dict) else {}
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
        "probes": probes,
    }
    write_status(payload)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
