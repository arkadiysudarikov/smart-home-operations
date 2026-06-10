#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
BILL_PATH = DATA_DIR / "sce_bill_readings.csv"
ALARM_PATH = ROOT / "config" / "alarm_energy_readings.json"
LEGACY_ALARM_PATH = DATA_DIR / "alarm_energy_readings.json"
SOURCE_ALARM_PATH = Path.home() / "Documents" / "Smart Home" / "config" / "alarm_energy_readings.json"
SENSE_ENVOY_PATH = DATA_DIR / "latest_meter_reconciliation.json"
OUT_JSON = DATA_DIR / "latest_bill_home_pairing.json"
OUT_REPORT = REPORT_DIR / "bill_home_pairing.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

ENVOY_LIFETIME_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), energy lifetime: ([\d.-]+) kWh")
ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")
SENSE_RE = re.compile(r"Watts: ([\d.-]+), Current: ([\d.-]+), Voltage: ([\d.-]+)")


def parse_iso(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def parse_bill_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%y").date()
    except ValueError:
        return None


def num(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def load_bills() -> list[dict[str, Any]]:
    if not BILL_PATH.exists():
        return []
    bills: list[dict[str, Any]] = []
    with BILL_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            start = parse_bill_date(row.get("period_start", ""))
            end = parse_bill_date(row.get("period_end", ""))
            days = (end - start).days if start and end else None
            import_kwh = num(row.get("import_kwh_sce"))
            export_kwh = num(row.get("export_kwh_sce"))
            net_kwh = num(row.get("import_minus_export_kwh"))
            delivery = num(row.get("delivery_charge_sce"))
            generation = num(row.get("generation_charge_cpa"))
            total_energy_charge = (delivery or 0) + (generation or 0) if delivery is not None or generation is not None else None
            bills.append(
                {
                    **row,
                    "period_start_date": start.isoformat() if start else "",
                    "period_end_date": end.isoformat() if end else "",
                    "period_days": days,
                    "import_kwh_sce": import_kwh,
                    "export_kwh_sce": export_kwh,
                    "net_import_kwh": net_kwh,
                    "delivery_charge_sce": delivery,
                    "generation_charge_cpa": generation,
                    "total_energy_charge": total_energy_charge,
                    "import_kwh_per_day": import_kwh / days if import_kwh is not None and days else None,
                    "export_kwh_per_day": export_kwh / days if export_kwh is not None and days else None,
                    "net_import_kwh_per_day": net_kwh / days if net_kwh is not None and days else None,
                    "export_to_import_ratio": export_kwh / import_kwh if import_kwh else None,
                    "charge_per_import_kwh": total_energy_charge / import_kwh if total_energy_charge is not None and import_kwh else None,
                    "charge_per_net_kwh": total_energy_charge / net_kwh if total_energy_charge is not None and net_kwh else None,
                }
            )
    return sorted(bills, key=lambda item: item.get("period_start_date", ""))


def load_alarm() -> dict[str, Any]:
    for path in [ALARM_PATH, LEGACY_ALARM_PATH, SOURCE_ALARM_PATH]:
        if path.exists():
            return json.loads(path.read_text())
    return {"dashboard": {}, "dailyKwh": [], "periodKwh": []}


def load_envoy_lifetime() -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {}
    powers: dict[str, list[dict[str, Any]]] = {}
    if not DB_PATH.exists():
        return {"meters": {}, "coverage": {}}
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute(
            """
            select captured_at, message
            from home_events
            where component = 'Enphase Envoy'
            order by captured_at asc
            """
        ):
            captured_at = parse_iso(row["captured_at"])
            message = row["message"] or ""
            lifetime = ENVOY_LIFETIME_RE.search(message)
            if lifetime:
                rows.setdefault(lifetime.group(1), []).append(
                    {"capturedAt": captured_at.isoformat(timespec="seconds"), "dt": captured_at, "kwh": float(lifetime.group(2))}
                )
                continue
            power = ENVOY_POWER_RE.search(message)
            if power:
                powers.setdefault(power.group(1), []).append({"dt": captured_at, "kw": float(power.group(2))})

    meters: dict[str, Any] = {}
    starts: list[datetime] = []
    ends: list[datetime] = []
    for meter, samples in rows.items():
        if len(samples) < 2:
            continue
        first = samples[0]
        last = samples[-1]
        starts.append(first["dt"])
        ends.append(last["dt"])
        meter_powers = [item["kw"] for item in powers.get(meter, [])]
        meters[meter] = {
            "firstCapturedAt": first["capturedAt"],
            "lastCapturedAt": last["capturedAt"],
            "firstLifetimeKwh": first["kwh"],
            "lastLifetimeKwh": last["kwh"],
            "deltaKwh": last["kwh"] - first["kwh"],
            "samples": len(samples),
            "avgKw": mean(meter_powers) if meter_powers else None,
        }
    return {
        "meters": meters,
        "coverage": {
            "start": min(starts).isoformat(timespec="seconds") if starts else "",
            "end": max(ends).isoformat(timespec="seconds") if ends else "",
            "hours": (max(ends) - min(starts)).total_seconds() / 3600 if starts and ends else None,
        },
    }


def load_sense_summary() -> dict[str, Any]:
    if not SENSE_ENVOY_PATH.exists():
        return {}
    try:
        payload = json.loads(SENSE_ENVOY_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return payload.get("senseEnvoySummary", {})


def load_sense_coverage() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {}
    samples: list[datetime] = []
    watts: list[float] = []
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute(
            """
            select captured_at, message
            from home_events
            where component = 'Sense Energy Meter'
            order by captured_at asc
            """
        ):
            match = SENSE_RE.search(row["message"] or "")
            if not match:
                continue
            samples.append(parse_iso(row["captured_at"]))
            watts.append(float(match.group(1)))
    return {
        "sampleCount": len(samples),
        "start": samples[0].isoformat(timespec="seconds") if samples else "",
        "end": samples[-1].isoformat(timespec="seconds") if samples else "",
        "avgKw": mean(watts) / 1000 if watts else None,
    }


def build_payload() -> dict[str, Any]:
    bills = load_bills()
    alarm = load_alarm()
    envoy = load_envoy_lifetime()
    sense = load_sense_coverage()
    sense_envoy = load_sense_summary()

    alarm_daily = [item for item in alarm.get("dailyKwh", []) if item.get("meter") == "Energy Clamp"]
    alarm_period = {item["period"]: item["kwh"] for item in alarm.get("periodKwh", []) if item.get("meter") == "Energy Clamp"}
    alarm_daily_total = sum(float(item["kwh"]) for item in alarm_daily)
    alarm_mtd = num((alarm.get("dashboard") or {}).get("monthToDateKwh"))
    latest_bill = bills[-1] if bills else {}
    latest_bill_end = parse_bill_date(latest_bill.get("period_end", "")) if latest_bill else None
    envoy_start = parse_iso(envoy["coverage"]["start"]).date() if envoy.get("coverage", {}).get("start") else None

    return {
        "generatedAt": datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
        "bills": bills,
        "latestClosedBill": latest_bill,
        "envoy": envoy,
        "sense": sense,
        "senseEnvoySummary": sense_envoy,
        "alarm": {
            "capturedAtLocal": alarm.get("capturedAtLocal"),
            "dashboard": alarm.get("dashboard", {}),
            "dailyRows": alarm_daily,
            "dailyTotalKwh": alarm_daily_total,
            "dailyTotalMinusDashboardMtdKwh": alarm_daily_total - alarm_mtd if alarm_mtd is not None else None,
            "periodKwh": alarm_period,
        },
        "overlap": {
            "latestBillEndsBeforeEnvoyStartsDays": (envoy_start - latest_bill_end).days if envoy_start and latest_bill_end else None,
            "closedBillDirectlyOverlapsEnvoySense": False,
        },
    }


def fmt(value: Any, digits: int = 1) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def pct(value: Any) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def write_report(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")

    latest_bill = payload.get("latestClosedBill") or {}
    envoy_meters = payload.get("envoy", {}).get("meters", {})
    alarm = payload.get("alarm", {})
    dashboard = alarm.get("dashboard", {})
    sense_envoy = payload.get("senseEnvoySummary") or {}
    all_sense_envoy = sense_envoy.get("all") or {}

    lines = [
        "# Bill / Home Energy Pairing",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Closed SCE bills loaded: `{len(payload.get('bills', []))}`",
        f"- Latest closed SCE bill: `{latest_bill.get('period_start', 'n/a')} to {latest_bill.get('period_end', 'n/a')}`",
        f"- Envoy/Sense monitor coverage: `{payload.get('envoy', {}).get('coverage', {}).get('start') or 'n/a'}` to `{payload.get('envoy', {}).get('coverage', {}).get('end') or 'n/a'}`",
        f"- Direct closed-bill overlap with Envoy/Sense: `no`",
        "",
        "## What Can Be Paired Now",
        "",
        f"- The latest closed bill ends `{fmt(payload.get('overlap', {}).get('latestBillEndsBeforeEnvoyStartsDays'), 0)}` days before the Envoy/Sense monitor starts.",
        "- Envoy, Sense, and Alarm.com can be paired against each other for the current June monitor window.",
        "- SCE bills can be compared to Alarm.com billing-scale aggregates, but not strictly paired to Envoy/Sense bill-period totals until older Envoy/Sense history or fresh SCE interval data is available.",
        "",
        "## Latest Closed SCE Bill",
        "",
        "| Period | Import kWh | Export kWh | Net import kWh | Import/day | Export ratio | Charge/import kWh | Charge/net kWh |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(
            [
                f"`{latest_bill.get('period_start', '')} to {latest_bill.get('period_end', '')}`",
                fmt(latest_bill.get("import_kwh_sce"), 0),
                fmt(latest_bill.get("export_kwh_sce"), 0),
                fmt(latest_bill.get("net_import_kwh"), 0),
                fmt(latest_bill.get("import_kwh_per_day"), 1),
                pct(latest_bill.get("export_to_import_ratio")),
                f"${fmt(latest_bill.get('charge_per_import_kwh'), 3)}",
                f"${fmt(latest_bill.get('charge_per_net_kwh'), 3)}",
            ]
        )
        + " |",
        "",
        "## Envoy Lifetime Deltas",
        "",
        "| Envoy meter | First | Last | Delta kWh | Avg live kW |",
        "|---|---|---|---:|---:|",
    ]
    for meter in ["Consumption Total", "Consumption Net", "Production", "Storage"]:
        row = envoy_meters.get(meter) or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{meter}`",
                    f"`{row.get('firstCapturedAt', 'n/a')}`",
                    f"`{row.get('lastCapturedAt', 'n/a')}`",
                    fmt(row.get("deltaKwh"), 1),
                    fmt(row.get("avgKw"), 3),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Alarm.com Pairing Scale",
            "",
            f"- Alarm.com captured: `{alarm.get('capturedAtLocal')}`",
            f"- Alarm.com dashboard current period: `{fmt(dashboard.get('monthToDateKwh'), 0)}` kWh.",
            f"- Alarm.com daily rows currently on disk sum to `{fmt(alarm.get('dailyTotalKwh'), 1)}` kWh.",
            f"- Alarm.com daily rows minus dashboard current period: `{fmt(alarm.get('dailyTotalMinusDashboardMtdKwh'), 1)}` kWh.",
            f"- Alarm.com 7-day / 21-day / 6-month / 12-month Energy Clamp: `{fmt(alarm.get('periodKwh', {}).get('7d'), 0)}` / `{fmt(alarm.get('periodKwh', {}).get('21d'), 0)}` / `{fmt(alarm.get('periodKwh', {}).get('6m'), 0)}` / `{fmt(alarm.get('periodKwh', {}).get('12m'), 0)}` kWh.",
            f"- Alarm.com last billing value: `{fmt(dashboard.get('energyClampLastBillingKwh'), 0)}` kWh; average billing value: `{fmt(dashboard.get('energyClampAverageBillingKwh'), 0)}` kWh.",
            "",
            "## Current Home Cross-Checks",
            "",
            f"- Envoy Consumption Total delta in the monitor window: `{fmt((envoy_meters.get('Consumption Total') or {}).get('deltaKwh'), 1)}` kWh.",
            f"- Envoy Consumption Net delta in the monitor window: `{fmt((envoy_meters.get('Consumption Net') or {}).get('deltaKwh'), 1)}` kWh.",
            f"- Envoy Production delta in the monitor window: `{fmt((envoy_meters.get('Production') or {}).get('deltaKwh'), 1)}` kWh.",
            f"- Sense sample coverage: `{payload.get('sense', {}).get('sampleCount', 0)}` samples, average observed `{fmt(payload.get('sense', {}).get('avgKw'), 3)}` kW.",
            f"- Sense/Envoy paired samples: `{all_sense_envoy.get('count', 0)}`; average Envoy minus Sense `{fmt(all_sense_envoy.get('avgEnvoyMinusSenseKw'), 3)}` kW; average Sense/Envoy `{pct(all_sense_envoy.get('avgSenseToEnvoyRatio'))}`.",
            "",
            "## All Closed Bills",
            "",
            "| Period | Import | Export | Net | Import/day | Export ratio | Total charge |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for bill in payload.get("bills", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{bill.get('period_start')} to {bill.get('period_end')}`",
                    fmt(bill.get("import_kwh_sce"), 0),
                    fmt(bill.get("export_kwh_sce"), 0),
                    fmt(bill.get("net_import_kwh"), 0),
                    fmt(bill.get("import_kwh_per_day"), 1),
                    pct(bill.get("export_to_import_ratio")),
                    f"${fmt(bill.get('total_energy_charge'), 2)}",
                ]
            )
            + " |"
        )

    alarm_mismatch = num(alarm.get("dailyTotalMinusDashboardMtdKwh"))
    if alarm_mismatch is not None and abs(alarm_mismatch) >= 25:
        alarm_capture_note = "- Alarm.com's dashboard current-period value and copied daily bars disagree, so Alarm.com needs a fresh export/screen scrape before it can be treated as a bill-period source of truth."
    elif alarm_mismatch is not None:
        alarm_capture_note = f"- Alarm.com's dashboard current-period value and copied daily bars now agree within `{fmt(abs(alarm_mismatch), 1)}` kWh."
    else:
        alarm_capture_note = "- Alarm.com daily bars are not available yet, so it is not ready to use as a bill-period source of truth."

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- SCE import/export is utility grid exchange, not whole-home consumption. Envoy Consumption Total and Alarm.com Energy Clamp are the closer whole-home comparables.",
            "- The latest closed bill shows high export relative to import: `251 kWh` exported against `658 kWh` imported, so net grid import was only `407 kWh`.",
            "- Alarm.com's last billing number is much higher than the SCE net import number because it appears to track site consumption, not net utility import.",
            "- The current Envoy monitor window and Alarm.com daily totals are in the same rough scale, but their windows are not identical, so treat that as a sanity check rather than a settled reconciliation.",
            alarm_capture_note,
            "- Sense is still not matching Envoy as a whole-home total in the current paired samples; Envoy and Alarm.com are the stronger pair for whole-home load.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_payload()
    write_report(payload)
    print(OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
