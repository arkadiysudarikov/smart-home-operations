#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.error
import urllib.request
import csv
import time
from datetime import datetime, timedelta, timezone
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
    gbc_config = config.get("green_button_connect") or config
    resource_url = (
        os.environ.get("SCE_GBC_RESOURCE_URL")
        or gbc_config.get("resource_url")
        or config.get("resource_url")
    )
    access_token = (
        os.environ.get("SCE_GBC_ACCESS_TOKEN")
        or gbc_config.get("access_token")
        or config.get("access_token")
    )
    return resource_url, access_token


def green_button_registration_plan(config: dict[str, Any]) -> dict[str, Any]:
    registration = config.get("third_party_registration") or {}
    oauth_client = config.get("green_button_connect") or {}
    return {
        "sceRegistrationUrl": registration.get(
            "sce_registration_url",
            "https://www.sce.com/user-registration?userType=4",
        ),
        "sceThirdPartyInfoUrl": "https://www.sce.com/partners/partnerships/thirdpartylandingpage",
        "sceDataAccessInfoUrl": "https://www.sce.com/partners/3rd-party-energy-providers/access-energy-usage-data",
        "requiredManualRegistrationFields": [
            "third-party vendor first name",
            "third-party vendor last name",
            "shared vendor email not already registered as an SCE.com User ID",
            "password entered directly on SCE.com",
            "organization legal name",
            "organization TIN",
            "SCE terms acceptance by an authorized person",
            "connectivity-test endpoint details",
        ],
        "localCallbackUrl": oauth_client.get("redirect_uri"),
        "clientConfigured": bool(oauth_client.get("client_id")),
        "tokenConfigured": bool(oauth_client.get("access_token") or config.get("access_token")),
        "resourceConfigured": bool(oauth_client.get("resource_url") or config.get("resource_url")),
        "notes": (
            "This project can store issued OAuth/resource values and fetch SCE Green Button data, "
            "but SCE account creation, TIN entry, terms acceptance, and connectivity testing must "
            "be completed manually on SCE.com."
        ),
    }


def split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def configured_utilityapi() -> dict[str, Any]:
    config = load_config()
    end = os.environ.get("UTILITYAPI_INTERVAL_END") or config.get("utilityapi_interval_end")
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
        "end": resolve_interval_end(end),
        "base_url": os.environ.get("UTILITYAPI_BASE_URL") or config.get("utilityapi_base_url") or UTILITYAPI_BASE_URL,
        "auto_historical_collection": parse_bool(
            os.environ.get("UTILITYAPI_AUTO_HISTORICAL_COLLECTION")
            if os.environ.get("UTILITYAPI_AUTO_HISTORICAL_COLLECTION") is not None
            else config.get("utilityapi_auto_historical_collection", False)
        ),
        "stale_hours": float(
            os.environ.get("UTILITYAPI_AUTO_COLLECTION_STALE_HOURS")
            or config.get("utilityapi_auto_collection_stale_hours", 36)
        ),
        "collection_timeout_seconds": int(
            os.environ.get("UTILITYAPI_HISTORICAL_COLLECTION_TIMEOUT_SECONDS")
            or config.get("utilityapi_historical_collection_timeout_seconds", 600)
        ),
        "collection_poll_seconds": int(
            os.environ.get("UTILITYAPI_HISTORICAL_COLLECTION_POLL_SECONDS")
            or config.get("utilityapi_historical_collection_poll_seconds", 30)
        ),
    }


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def resolve_interval_end(value: Any) -> str:
    if value is None or str(value).strip().lower() in {"", "auto", "current", "today"}:
        return (datetime.now(timezone.utc).astimezone().date() + timedelta(days=1)).isoformat()
    if str(value).strip().lower() == "tomorrow":
        return (datetime.now(timezone.utc).astimezone().date() + timedelta(days=1)).isoformat()
    return str(value).strip()


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


def api_post_json(url: str, api_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartHomeMonitor/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def coverage_age_hours(coverage_end: str | None) -> float | None:
    parsed = parse_datetime(coverage_end)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc).astimezone() - parsed.astimezone()).total_seconds() / 3600


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


def fetch_utilityapi_intervals(config: dict[str, Any]) -> dict[str, Any]:
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
    coverage_start: str | None = None
    coverage_end: str | None = None
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
                coverage_start = start if coverage_start is None or start < coverage_start else coverage_start
                coverage_end = end if coverage_end is None or end > coverage_end else coverage_end
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
    return {
        "path": csv_path,
        "rowCount": row_count,
        "coverageStart": coverage_start,
        "coverageEnd": coverage_end,
        "requestedEnd": config.get("end"),
        "meters": meter_uids,
        "authorizations": authorization_uids,
    }


def utilityapi_meter_status(config: dict[str, Any], meter_uids: list[str]) -> list[dict[str, Any]]:
    if not meter_uids:
        return []
    api_token = config["api_token"]
    base_url = config["base_url"].rstrip("/")
    url = f"{base_url}/meters?{urllib.parse.urlencode({'uids': ','.join(meter_uids)})}"
    return api_get_json(url, api_token).get("meters") or []


def trigger_historical_collection(config: dict[str, Any], meter_uids: list[str]) -> dict[str, Any]:
    if not meter_uids:
        return {"ok": False, "error": "no meter uids available for historical collection"}
    api_token = config["api_token"]
    base_url = config["base_url"].rstrip("/")
    try:
        payload = api_post_json(f"{base_url}/meters/historical-collection", api_token, {"meters": meter_uids})
    except urllib.error.HTTPError as exc:
        if exc.code == 402:
            return {
                "ok": False,
                "status": "payment_required",
                "statusCode": exc.code,
                "reason": exc.reason,
                "meters": meter_uids,
                "requiredAction": "Check UtilityAPI billing or collection entitlement, then rerun Refresh SCE.",
            }
        raise
    return {
        "ok": bool(payload.get("success", True)),
        "meters": meter_uids,
        "collectionDuration": payload.get("collection_duration"),
        "createdFreeCollection": payload.get("created_free_collection"),
    }


def wait_for_historical_collection(config: dict[str, Any], meter_uids: list[str]) -> dict[str, Any]:
    timeout = max(0, int(config.get("collection_timeout_seconds") or 0))
    poll_seconds = max(5, int(config.get("collection_poll_seconds") or 30))
    deadline = time.monotonic() + timeout
    polls: list[dict[str, Any]] = []
    while True:
        meters = utilityapi_meter_status(config, meter_uids)
        statuses = [
            {
                "uid": str(meter.get("uid")),
                "status": meter.get("status"),
                "statusMessage": meter.get("status_message"),
                "statusTs": meter.get("status_ts"),
                "intervalCount": meter.get("interval_count"),
                "intervalCoverage": meter.get("interval_coverage"),
            }
            for meter in meters
        ]
        polls.append({"at": now(), "meters": statuses})
        if statuses and all(item.get("status") != "pending" for item in statuses):
            return {"ok": True, "polls": polls[-8:]}
        if time.monotonic() >= deadline:
            return {"ok": False, "timedOut": True, "polls": polls[-8:]}
        time.sleep(poll_seconds)


def fetch_with_auto_historical_collection(config: dict[str, Any]) -> dict[str, Any]:
    try:
        result = fetch_utilityapi_intervals(config)
    except NoUtilityApiIntervals:
        if not config.get("auto_historical_collection") or not config.get("meter_uids"):
            raise
        collection = trigger_historical_collection(config, config["meter_uids"])
        if not collection.get("ok"):
            raise
        wait_result = wait_for_historical_collection(config, config["meter_uids"])
        if not wait_result.get("ok"):
            raise
        result = fetch_utilityapi_intervals(config)
        result["historicalCollection"] = {
            "triggered": True,
            "triggeredAt": now(),
            **collection,
            "wait": wait_result,
            "refetchedAt": now(),
            "reason": "no_intervals",
        }
    age = coverage_age_hours(result.get("coverageEnd"))
    result["coverageAgeHours"] = age
    if not config.get("auto_historical_collection"):
        return result
    if age is not None and age < float(config.get("stale_hours") or 36):
        return result
    collection = trigger_historical_collection(config, result.get("meters") or [])
    result["historicalCollection"] = {
        "triggered": True,
        "triggeredAt": now(),
        **collection,
    }
    if not collection.get("ok"):
        return result
    wait_result = wait_for_historical_collection(config, result.get("meters") or [])
    result["historicalCollection"]["wait"] = wait_result
    if not wait_result.get("ok"):
        return result
    refreshed = fetch_utilityapi_intervals(config)
    refreshed["coverageAgeHours"] = coverage_age_hours(refreshed.get("coverageEnd"))
    refreshed["historicalCollection"] = result["historicalCollection"] | {
        "refetchedAt": now(),
        "previousCoverageEnd": result.get("coverageEnd"),
        "previousIntervalRows": result.get("rowCount"),
    }
    return refreshed


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
            result = fetch_with_auto_historical_collection(utilityapi_config)
        except urllib.error.HTTPError as exc:
            if exc.code == 402:
                write_status(
                    {
                        "ok": None,
                        "status": "utilityapi_payment_required",
                        "startedAt": started_at,
                        "finishedAt": now(),
                        "statusCode": exc.code,
                        "reason": exc.reason,
                        "detail": (
                            "UtilityAPI returned 402 Payment Required. Treating SCE as source-side degraded "
                            "so the monitor can complete while stale-interval alerts remain active."
                        ),
                        "requiredAction": "Do not use paid UtilityAPI collection. Import a fresh SCE Green Button CSV/XML export, then rerun Refresh SCE.",
                    }
                )
                return 0
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
                    "requiredAction": "UtilityAPI returned no intervals. Import a fresh SCE Green Button CSV/XML export, then rerun Refresh SCE; paid UtilityAPI collection remains disabled unless explicitly approved.",
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
        path = result["path"]
        write_status(
            {
                "ok": True,
                "status": "utilityapi_downloaded",
                "startedAt": started_at,
                "finishedAt": now(),
                "file": str(path),
                "bytes": path.stat().st_size,
                "intervalRows": result.get("rowCount"),
                "coverageStart": result.get("coverageStart"),
                "coverageEnd": result.get("coverageEnd"),
                "requestedEnd": result.get("requestedEnd"),
                "coverageAgeHours": result.get("coverageAgeHours"),
                "autoHistoricalCollection": result.get("historicalCollection"),
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
                "registrationPlan": green_button_registration_plan(load_config()),
                "requiredConfig": [
                    "utilityapi_api_token with utilityapi_meter_uids or utilityapi_authorization_uids",
                    "or green_button_connect.resource_url with green_button_connect.access_token",
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
