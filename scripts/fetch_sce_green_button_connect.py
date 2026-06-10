#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.error
import urllib.request
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sce_green_button_connect.json"
DATA_DIR = ROOT / "data"
DOWNLOAD_DIR = DATA_DIR / "sce-downloads"
STATUS_PATH = DATA_DIR / "latest_sce_api.json"
UTILITYAPI_BASE_URL = "https://utilityapi.com/api/v2"


class NoUtilityApiIntervals(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def configured_request() -> tuple[str | None, str | None]:
    config = load_config()
    resource_url = os.environ.get("SCE_GBC_RESOURCE_URL") or config.get("resource_url")
    access_token = os.environ.get("SCE_GBC_ACCESS_TOKEN") or config.get("access_token")
    return resource_url, access_token


def split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def configured_utilityapi() -> dict[str, Any]:
    config = load_config()
    return {
        "api_token": os.environ.get("UTILITYAPI_API_TOKEN")
        or os.environ.get("UTILITYAPI_TOKEN")
        or config.get("utilityapi_api_token")
        or config.get("api_token"),
        "meter_uids": split_csv(os.environ.get("UTILITYAPI_METER_UIDS") or config.get("utilityapi_meter_uids")),
        "authorization_uids": split_csv(
            os.environ.get("UTILITYAPI_AUTHORIZATION_UIDS") or config.get("utilityapi_authorization_uids")
        ),
        "start": os.environ.get("UTILITYAPI_INTERVAL_START") or config.get("utilityapi_interval_start"),
        "end": os.environ.get("UTILITYAPI_INTERVAL_END") or config.get("utilityapi_interval_end"),
        "base_url": os.environ.get("UTILITYAPI_BASE_URL") or config.get("utilityapi_base_url") or UTILITYAPI_BASE_URL,
    }


def api_get_json(url: str, api_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "User-Agent": "SmartHomeMonitor/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def paged_get(url: str, api_token: str, collection_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    urls: list[str] = []
    next_url: str | None = url
    while next_url:
        urls.append(next_url)
        payload = api_get_json(next_url, api_token)
        rows.extend(payload.get(collection_key) or [])
        next_url = payload.get("next")
    return rows, urls


def discover_utilityapi_meters(config: dict[str, Any]) -> dict[str, Any]:
    api_token = config["api_token"]
    base_url = config["base_url"].rstrip("/")
    payload = api_get_json(f"{base_url}/authorizations?include=meters", api_token)
    authorizations = payload.get("authorizations") or []
    meter_uids: list[str] = []
    authorization_uids: list[str] = []
    for authorization in authorizations:
        if authorization.get("utility") and authorization.get("utility") != "SCE":
            continue
        auth_uid = authorization.get("uid")
        if auth_uid:
            authorization_uids.append(str(auth_uid))
        meters = ((authorization.get("meters") or {}).get("meters") or [])
        for meter in meters:
            if meter.get("utility") and meter.get("utility") != "SCE":
                continue
            uid = meter.get("uid")
            if uid:
                meter_uids.append(str(uid))
    return {
        "authorizations": authorizations,
        "authorization_uids": sorted(set(authorization_uids)),
        "meter_uids": sorted(set(meter_uids)),
    }


def datapoint_value(reading: dict[str, Any], kind: str) -> float | None:
    for point in reading.get("datapoints") or []:
        if point.get("type") == kind and point.get("unit") == "kwh" and point.get("value") is not None:
            return float(point["value"])
    return None


def fetch_utilityapi_intervals(config: dict[str, Any]) -> Path:
    api_token = config["api_token"]
    base_url = config["base_url"].rstrip("/")
    meter_uids = list(config["meter_uids"])
    authorization_uids = list(config["authorization_uids"])
    discovery: dict[str, Any] | None = None
    if not meter_uids and not authorization_uids:
        discovery = discover_utilityapi_meters(config)
        meter_uids = discovery["meter_uids"]
        authorization_uids = discovery["authorization_uids"]
    if not meter_uids and not authorization_uids:
        raise RuntimeError("UtilityAPI token worked, but no SCE meter or authorization uid was found.")

    query: dict[str, str] = {"order": "earliest_first", "allow_mixed": "true"}
    if meter_uids:
        query["meters"] = ",".join(meter_uids)
    else:
        query["authorizations"] = ",".join(authorization_uids)
    if config["start"]:
        query["start"] = str(config["start"])
    if config["end"]:
        query["end"] = str(config["end"])
    url = f"{base_url}/intervals?{urllib.parse.urlencode(query)}"
    intervals, requested_urls = paged_get(url, api_token, "intervals")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S%z")
    raw_path = DOWNLOAD_DIR / f"UtilityAPI_intervals_{stamp}.json"
    raw_path.write_text(
        json.dumps(
            {
                "meters": meter_uids,
                "authorizations": authorization_uids,
                "requestedUrls": requested_urls,
                "discovery": discovery,
                "intervals": intervals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    csv_path = DOWNLOAD_DIR / f"SCE_Usage_UtilityAPI_{stamp}.csv"
    row_count = 0
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Energy Consumption Time Period Start",
                "Energy Consumption Time Period End",
                "Delivered",
                "Received",
                "Net",
                "Source",
                "Meter UID",
                "Authorization UID",
            ]
        )
        for interval in intervals:
            for reading in interval.get("readings") or []:
                start = reading.get("start")
                end = reading.get("end")
                if not start or not end:
                    continue
                delivered = datapoint_value(reading, "fwd")
                received = datapoint_value(reading, "rev")
                net = datapoint_value(reading, "net")
                if delivered is None and received is None and net is not None:
                    delivered = net if net >= 0 else 0.0
                    received = abs(net) if net < 0 else 0.0
                writer.writerow(
                    [
                        start,
                        end,
                        0.0 if delivered is None else delivered,
                        0.0 if received is None else received,
                        "" if net is None else net,
                        "UtilityAPI",
                        interval.get("meter_uid") or "",
                        interval.get("authorization_uid") or "",
                    ]
                )
                row_count += 1
    if row_count == 0:
        raise NoUtilityApiIntervals(f"UtilityAPI returned {len(intervals)} interval objects but no kWh readings.")
    return csv_path


def suffix_for(content_type: str, url: str) -> str:
    lowered = f"{content_type} {url}".lower()
    if "csv" in lowered:
        return ".csv"
    return ".xml"


def fetch(resource_url: str, access_token: str) -> Path:
    request = urllib.request.Request(
        resource_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/atom+xml, application/xml, text/xml, text/csv, */*",
            "User-Agent": "SmartHomeMonitor/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S%z")
    path = DOWNLOAD_DIR / f"SCE_Usage_GBC_{stamp}{suffix_for(content_type, resource_url)}"
    path.write_bytes(body)
    return path


def main() -> int:
    started_at = now()
    resource_url, access_token = configured_request()
    utilityapi_config = configured_utilityapi()
    if utilityapi_config["api_token"]:
        try:
            path = fetch_utilityapi_intervals(utilityapi_config)
        except urllib.error.HTTPError as exc:
            write_status(
                {
                    "ok": False,
                    "status": "utilityapi_http_error",
                    "startedAt": started_at,
                    "finishedAt": now(),
                    "statusCode": exc.code,
                    "reason": exc.reason,
                }
            )
            return 1
        except NoUtilityApiIntervals as exc:
            write_status(
                {
                    "ok": None,
                    "status": "utilityapi_no_intervals",
                    "startedAt": started_at,
                    "finishedAt": now(),
                    "detail": str(exc),
                    "requiredAction": "Trigger or wait for UtilityAPI historical collection for the configured SCE meter, then rerun Refresh SCE.",
                }
            )
            return 0
        except Exception as exc:
            write_status(
                {
                    "ok": False,
                    "status": "utilityapi_failed",
                    "startedAt": started_at,
                    "finishedAt": now(),
                    "error": str(exc),
                    "requiredConfig": [
                        "utilityapi_api_token",
                        "utilityapi_meter_uids or utilityapi_authorization_uids",
                    ],
                }
            )
            return 1
        write_status(
            {
                "ok": True,
                "status": "utilityapi_downloaded",
                "startedAt": started_at,
                "finishedAt": now(),
                "file": str(path),
                "bytes": path.stat().st_size,
            }
        )
        print(path)
        return 0
    if not resource_url or not access_token:
        write_status(
            {
                "ok": None,
                "status": "registration_required",
                "startedAt": started_at,
                "finishedAt": now(),
                "detail": "Configure UtilityAPI API credentials, or direct SCE Green Button Connect resource URL and OAuth token, before API pulls can run.",
                "configPath": str(CONFIG_PATH),
                "requiredConfig": [
                    "utilityapi_api_token with utilityapi_meter_uids or utilityapi_authorization_uids",
                    "or resource_url with access_token",
                ],
                "sceInfo": "https://www.sce.com/partners/3rd-party-energy-providers/access-energy-usage-data",
                "sceThirdPartyRegistration": "https://www.sce.com/partners/partnerships/thirdpartylandingpage",
                "utilityapiSettings": "https://utilityapi.com/settings",
            }
        )
        return 0
    try:
        path = fetch(resource_url, access_token)
    except urllib.error.HTTPError as exc:
        write_status(
            {
                "ok": False,
                "status": "http_error",
                "startedAt": started_at,
                "finishedAt": now(),
                "resourceUrl": resource_url,
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
                "resourceUrl": resource_url,
                "error": str(exc),
            }
        )
        return 1
    write_status(
        {
            "ok": True,
            "status": "downloaded",
            "startedAt": started_at,
            "finishedAt": now(),
            "resourceUrl": resource_url,
            "file": str(path),
            "bytes": path.stat().st_size,
        }
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
