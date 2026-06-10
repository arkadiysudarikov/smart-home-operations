#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
SOURCE_PATH = DATA_DIR / "chargepoint_sessions.json"
ALARM_PATH = ROOT / "config" / "alarm_energy_readings.json"
ALL_ENERGY_PATH = DATA_DIR / "latest_all_energy_pairs.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

SENSE_RE = re.compile(r"Watts: ([\d.-]+), Current: ([\d.-]+), Voltage: ([\d.-]+)")
ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")


@dataclass
class Sample:
    source: str
    meter: str
    captured_at: datetime
    kw: float


def parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def session_key(session: dict[str, Any]) -> str:
    basis = "|".join(
        [
            str(session.get("station", "")),
            str(session.get("startAt", "")),
            str(session.get("endAt", "")),
            str(session.get("energyKwh", "")),
        ]
    )
    return hashlib.sha256(basis.encode()).hexdigest()


def load_source() -> dict[str, Any]:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"No ChargePoint session source found at {SOURCE_PATH}")
    data = json.loads(SOURCE_PATH.read_text())
    if not isinstance(data.get("sessions"), list):
        raise SystemExit(f"ChargePoint source has no sessions list: {SOURCE_PATH}")
    return data


def load_alarm() -> dict[str, Any]:
    if not ALARM_PATH.exists():
        return {"dailyKwh": [], "periodKwh": [], "dashboard": {}}
    return json.loads(ALARM_PATH.read_text())


def load_sce() -> dict[str, Any]:
    if not ALL_ENERGY_PATH.exists():
        return {}
    try:
        return json.loads(ALL_ENERGY_PATH.read_text()).get("sceGreenButton", {}).get("summary", {})
    except json.JSONDecodeError:
        return {}


def load_monitor_samples() -> list[Sample]:
    samples: list[Sample] = []
    if not DB_PATH.exists():
        return samples
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select captured_at, component, message
            from home_events
            where component in ('Sense Energy Meter', 'Enphase Envoy')
            order by captured_at asc
            """
        )
        for row in rows:
            captured_at = parse_dt(row["captured_at"])
            message = row["message"] or ""
            if row["component"] == "Sense Energy Meter":
                match = SENSE_RE.search(message)
                if match:
                    samples.append(Sample("Sense", "Whole Home", captured_at, float(match.group(1)) / 1000.0))
                continue
            match = ENVOY_POWER_RE.search(message)
            if match:
                samples.append(Sample("Envoy", match.group(1), captured_at, float(match.group(2))))
    return samples


def estimate_kwh(samples: list[Sample], source: str, meter: str, start_at: datetime, end_at: datetime) -> dict[str, Any]:
    rows = [item for item in samples if item.source == source and item.meter == meter and start_at <= item.captured_at < end_at]
    if not rows:
        return {"sampleCount": 0}
    duration_hours = (end_at - start_at).total_seconds() / 3600.0
    avg_kw = mean(item.kw for item in rows)
    return {
        "sampleCount": len(rows),
        "firstSampleAt": min(item.captured_at for item in rows).isoformat(timespec="seconds"),
        "lastSampleAt": max(item.captured_at for item in rows).isoformat(timespec="seconds"),
        "averageKw": avg_kw,
        "estimatedKwh": avg_kw * duration_hours,
    }


def group_chargepoint_by_day(sessions: list[dict[str, Any]]) -> dict[str, float]:
    by_day: dict[str, float] = defaultdict(float)
    for session in sessions:
        # Attribute each charge session to its local start day. That matches the
        # way the overnight charging habit is reviewed operationally.
        by_day[parse_dt(session["startAt"]).date().isoformat()] += float(session["energyKwh"])
    return dict(sorted(by_day.items()))


def build_alarm_context(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    alarm = load_alarm()
    cp_by_day = group_chargepoint_by_day(sessions)
    alarm_by_day = {
        item["date"]: float(item["kwh"])
        for item in alarm.get("dailyKwh", [])
        if item.get("meter") == "Energy Clamp"
    }
    daily = []
    for day, cp_kwh in cp_by_day.items():
        alarm_kwh = alarm_by_day.get(day)
        daily.append(
            {
                "date": day,
                "chargepointKwh": cp_kwh,
                "alarmEnergyClampKwh": alarm_kwh,
                "chargepointShareOfAlarm": cp_kwh / alarm_kwh if alarm_kwh else None,
            }
        )
    period = {
        item["period"]: float(item["kwh"])
        for item in alarm.get("periodKwh", [])
        if item.get("meter") == "Energy Clamp"
    }
    recent_cp = sum(
        float(session["energyKwh"])
        for session in sessions
        if parse_dt(session["startAt"]).date().isoformat() >= "2026-06-03"
    )
    return {
        "capturedAtLocal": alarm.get("capturedAtLocal"),
        "daily": daily,
        "recentChargepointKwhSince2026_06_03": recent_cp,
        "alarm7dEnergyClampKwh": period.get("7d"),
        "recentChargepointShareOfAlarm7d": recent_cp / period["7d"] if period.get("7d") else None,
        "dashboard": alarm.get("dashboard", {}),
    }


def build_sce_context(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    sce = load_sce()
    starts = [parse_dt(session["startAt"]) for session in sessions]
    ends = [parse_dt(session["endAt"]) for session in sessions]
    coverage_start = parse_dt(sce["coverageStart"]) if sce.get("coverageStart") else None
    coverage_end = parse_dt(sce["coverageEnd"]) if sce.get("coverageEnd") else None
    overlaps = bool(coverage_start and coverage_end and starts and max(starts) < coverage_end and min(ends) > coverage_start)
    return {
        "coverageStart": sce.get("coverageStart"),
        "coverageEnd": sce.get("coverageEnd"),
        "netImportKwh": sce.get("netImportKwh"),
        "overlapsChargepointWindow": overlaps,
        "message": (
            "SCE Green Button interval data overlaps the ChargePoint window."
            if overlaps
            else "SCE Green Button interval data does not overlap the May-June 2026 ChargePoint window; fresh SCE export is required."
        ),
    }


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            create table if not exists chargepoint_sessions (
              session_key text primary key,
              captured_at text not null,
              start_at text not null,
              end_at text not null,
              duration text,
              station_type text,
              station text,
              address text,
              energy_kwh real not null,
              cost_usd real,
              source text,
              raw_json text not null
            )
            """
        )
        db.execute(
            """
            create table if not exists energy_session_pairs (
              pair_key text primary key,
              captured_at text not null,
              chargepoint_session_key text not null,
              sense_start_at text,
              sense_end_at text,
              start_gap_seconds real,
              end_gap_seconds real,
              confidence text not null,
              message text not null,
              raw_json text not null
            )
            """
        )
        db.execute(
            """
            create table if not exists chargepoint_source_pairs (
              pair_key text primary key,
              captured_at text not null,
              chargepoint_session_key text not null,
              source text not null,
              meter text not null,
              sample_count integer not null,
              average_kw real,
              estimated_kwh real,
              delta_kwh real,
              raw_json text not null
            )
            """
        )


def nearest_event(events: list[dict[str, Any]], event_type: str, target: datetime, max_seconds: int) -> dict[str, Any] | None:
    typed = [event for event in events if event.get("eventType") == event_type]
    if not typed:
        return None
    best = min(typed, key=lambda event: abs((parse_dt(event["eventAt"]) - target).total_seconds()))
    gap = abs((parse_dt(best["eventAt"]) - target).total_seconds())
    if gap > max_seconds:
        return None
    result = dict(best)
    result["gapSeconds"] = gap
    return result


def build_pair(session: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    start_at = parse_dt(session["startAt"])
    end_at = parse_dt(session["endAt"])
    sense_start = nearest_event(events, "ev_on", start_at, 15 * 60)
    sense_end = nearest_event(events, "ev_off", end_at, 15 * 60)

    if sense_start and sense_end:
        confidence = "high"
        message = "Sense EV start and stop both match ChargePoint session boundaries."
    elif sense_start:
        confidence = "partial"
        message = "Sense EV start matches ChargePoint, but Sense did not show a matching stop near the ChargePoint end."
    else:
        confidence = "none"
        message = "No nearby Sense EV start was visible for this ChargePoint session."

    pair = {
        "capturedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "chargepointSessionKey": session_key(session),
        "chargepointStartAt": session["startAt"],
        "chargepointEndAt": session["endAt"],
        "chargepointEnergyKwh": session["energyKwh"],
        "senseStartAt": sense_start.get("eventAt") if sense_start else None,
        "senseEndAt": sense_end.get("eventAt") if sense_end else None,
        "startGapSeconds": sense_start.get("gapSeconds") if sense_start else None,
        "endGapSeconds": sense_end.get("gapSeconds") if sense_end else None,
        "confidence": confidence,
        "message": message,
    }
    pair["pairKey"] = hashlib.sha256(json.dumps(pair, sort_keys=True).encode()).hexdigest()
    return pair


def save_sessions(data: dict[str, Any], source_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    captured_at = data.get("capturedAt") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    source = data.get("source")
    pairs: list[dict[str, Any]] = []
    with sqlite3.connect(DB_PATH) as db:
        for session in data["sessions"]:
            key = session_key(session)
            db.execute(
                """
                insert or replace into chargepoint_sessions (
                  session_key, captured_at, start_at, end_at, duration, station_type,
                  station, address, energy_kwh, cost_usd, source, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    captured_at,
                    session["startAt"],
                    session["endAt"],
                    session.get("duration"),
                    session.get("stationType"),
                    session.get("station"),
                    session.get("address"),
                    session["energyKwh"],
                    session.get("costUsd"),
                    source,
                    json.dumps(session, sort_keys=True),
                ),
            )
            pair = build_pair(session, data.get("senseEvEvents", []))
            pairs.append(pair)
            db.execute(
                """
                insert or replace into energy_session_pairs (
                  pair_key, captured_at, chargepoint_session_key, sense_start_at,
                  sense_end_at, start_gap_seconds, end_gap_seconds, confidence,
                  message, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair["pairKey"],
                    pair["capturedAt"],
                    key,
                    pair["senseStartAt"],
                    pair["senseEndAt"],
                    pair["startGapSeconds"],
                    pair["endGapSeconds"],
                    pair["confidence"],
                    pair["message"],
                    json.dumps(pair, sort_keys=True),
                ),
            )
        for pair in source_pairs:
            db.execute(
                """
                insert or replace into chargepoint_source_pairs (
                  pair_key, captured_at, chargepoint_session_key, source, meter,
                  sample_count, average_kw, estimated_kwh, delta_kwh, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair["pairKey"],
                    captured_at,
                    pair["chargepointSessionKey"],
                    pair["source"],
                    pair["meter"],
                    pair["sampleCount"],
                    pair.get("averageKw"),
                    pair.get("estimatedKwh"),
                    pair.get("deltaKwh"),
                    json.dumps(pair, sort_keys=True),
                ),
            )
    return pairs


def build_source_pairs(data: dict[str, Any]) -> list[dict[str, Any]]:
    samples = load_monitor_samples()
    source_pairs: list[dict[str, Any]] = []
    meters = [
        ("Envoy", "Consumption Total"),
        ("Envoy", "Consumption Net"),
        ("Envoy", "Production"),
        ("Envoy", "Storage"),
        ("Sense", "Whole Home"),
    ]
    for session in data["sessions"]:
        start_at = parse_dt(session["startAt"])
        end_at = parse_dt(session["endAt"])
        for source, meter in meters:
            estimate = estimate_kwh(samples, source, meter, start_at, end_at)
            row = {
                "pairKey": hashlib.sha256(
                    f"{session_key(session)}|{source}|{meter}".encode()
                ).hexdigest(),
                "chargepointSessionKey": session_key(session),
                "chargepointStartAt": session["startAt"],
                "chargepointEndAt": session["endAt"],
                "chargepointEnergyKwh": session["energyKwh"],
                "source": source,
                "meter": meter,
                **estimate,
            }
            if estimate.get("estimatedKwh") is not None:
                row["deltaKwh"] = estimate["estimatedKwh"] - float(session["energyKwh"])
            source_pairs.append(row)
    return source_pairs


def write_report(
    data: dict[str, Any],
    pairs: list[dict[str, Any]],
    source_pairs: list[dict[str, Any]],
    alarm_context: dict[str, Any],
    sce_context: dict[str, Any],
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sourceCapturedAt": data.get("capturedAt"),
        "pairCount": len(pairs),
        "pairs": pairs,
        "sourcePairs": source_pairs,
        "alarm": alarm_context,
        "sce": sce_context,
    }
    (DATA_DIR / "latest_chargepoint_pairs.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# ChargePoint Pairing",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- ChargePoint sessions imported: `{len(data.get('sessions', []))}`",
        f"- ChargePoint visible total: `{data.get('visibleTotals', {}).get('energyKwh', 'n/a')}` kWh / `${data.get('visibleTotals', {}).get('costUsd', 'n/a')}`",
        "- Source pairing: session-level Envoy/Sense estimates use monitor samples inside each ChargePoint interval; Alarm.com is day/window-level; SCE requires overlapping utility interval exports.",
        "",
        "## Session Boundary Events",
        "",
        "| ChargePoint start | ChargePoint end | Energy kWh | Sense EV start gap | Sense EV end gap | Confidence |",
        "|---|---|---:|---:|---:|---|",
    ]
    for pair in pairs:
        start_gap = "n/a" if pair["startGapSeconds"] is None else f"{pair['startGapSeconds']:.0f}s"
        end_gap = "n/a" if pair["endGapSeconds"] is None else f"{pair['endGapSeconds']:.0f}s"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{pair['chargepointStartAt']}`",
                    f"`{pair['chargepointEndAt']}`",
                    f"{pair['chargepointEnergyKwh']:.3f}",
                    start_gap,
                    end_gap,
                    f"`{pair['confidence']}`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    for pair in pairs:
        lines.append(f"- {pair['message']}")

    comparable_sessions = {
        row["chargepointSessionKey"]
        for row in source_pairs
        if row["source"] == "Envoy" and row["meter"] == "Consumption Total" and row["sampleCount"] > 0
    }
    lines.extend(
        [
            "",
            "## Session-Level Envoy / Sense Estimates",
            "",
            f"- ChargePoint sessions with Envoy samples inside the interval: `{len(comparable_sessions)}`",
            "- `Envoy Consumption Total` is whole-home site load, not EV-only load; compare it as context, not as a direct equality check.",
            "",
            "| ChargePoint start | ChargePoint kWh | Envoy total est kWh | Envoy samples | Sense est kWh | Sense samples |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    by_session: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in source_pairs:
        by_session[row["chargepointSessionKey"]][f"{row['source']}:{row['meter']}"] = row
    for session in data["sessions"]:
        key = session_key(session)
        envoy = by_session[key].get("Envoy:Consumption Total", {})
        sense = by_session[key].get("Sense:Whole Home", {})
        if not envoy and not sense:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{session['startAt']}`",
                    f"{session['energyKwh']:.3f}",
                    fmt(envoy.get("estimatedKwh")),
                    str(envoy.get("sampleCount", 0)),
                    fmt(sense.get("estimatedKwh")),
                    str(sense.get("sampleCount", 0)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Alarm.com Daily Context",
            "",
            f"- Alarm.com source captured: `{alarm_context.get('capturedAtLocal')}`",
            f"- ChargePoint kWh from `2026-06-03` onward: `{fmt(alarm_context.get('recentChargepointKwhSince2026_06_03'))}`",
            f"- Alarm.com Energy Clamp 7d: `{fmt(alarm_context.get('alarm7dEnergyClampKwh'))}` kWh",
            f"- ChargePoint share of Alarm.com 7d Energy Clamp: `{pct(alarm_context.get('recentChargepointShareOfAlarm7d'))}`",
            "",
            "| Date | ChargePoint kWh | Alarm.com Energy Clamp kWh | ChargePoint share |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in alarm_context.get("daily", []):
        if row.get("alarmEnergyClampKwh") is None and row["date"] < "2026-06-03":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['date']}`",
                    fmt(row.get("chargepointKwh")),
                    fmt(row.get("alarmEnergyClampKwh")),
                    pct(row.get("chargepointShareOfAlarm")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## SCE Context",
            "",
            f"- Green Button coverage: `{sce_context.get('coverageStart') or 'n/a'}` to `{sce_context.get('coverageEnd') or 'n/a'}`",
            f"- Green Button net import: `{fmt(sce_context.get('netImportKwh'), 1)}` kWh",
            f"- Overlaps ChargePoint window: `{sce_context.get('overlapsChargepointWindow')}`",
            f"- {sce_context.get('message')}",
        ]
    )
    (REPORT_DIR / "chargepoint_pairing.md").write_text("\n".join(lines) + "\n")


def fmt(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def main() -> int:
    data = load_source()
    init_db()
    source_pairs = build_source_pairs(data)
    pairs = save_sessions(data, source_pairs)
    write_report(data, pairs, source_pairs, build_alarm_context(data["sessions"]), build_sce_context(data["sessions"]))
    print(REPORT_DIR / "chargepoint_pairing.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
