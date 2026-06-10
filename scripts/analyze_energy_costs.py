#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
BILL_PATH = DATA_DIR / "sce_bill_readings.csv"
OUT_JSON = DATA_DIR / "latest_energy_costs.json"
OUT_REPORT = REPORT_DIR / "energy_costs.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

ALWAYS_AVAILABLE_CONTEXT = {
    "macs": {
        "M2 Mac mini": 2,
        "M4 Mac mini": 2,
        "MacBook Pro": 1,
    },
    "appleTVs": 5,
    "homePods": "multiple",
    "note": "User-reported always-on network/media context on 2026-06-10.",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any, digits: int = 1) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def money(value: Any) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def rate(value: Any) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"${value:.3f}/kWh"


def pct(value: Any) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def parse_bill_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%y").replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def safe_div(numerator: Any, denominator: Any) -> float | None:
    numerator = num(numerator)
    denominator = num(denominator)
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def bill_export_credit(row: dict[str, Any]) -> float:
    return sum(
        value
        for value in [
            num(row.get("delivery_export_credit_dollars")),
            num(row.get("delivery_export_bonus_credit_dollars")),
            num(row.get("cpa_export_credit_dollars")),
        ]
        if value is not None
    )


def load_bill_rates() -> list[dict[str, Any]]:
    if not BILL_PATH.exists():
        return []
    bills: list[dict[str, Any]] = []
    with BILL_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            start = parse_bill_date(row.get("period_start", ""))
            end = parse_bill_date(row.get("period_end", ""))
            import_kwh = num(row.get("import_kwh_sce"))
            export_kwh = num(row.get("export_kwh_sce"))
            net_import_kwh = num(row.get("import_minus_export_kwh"))
            delivery_charge = num(row.get("delivery_charge_sce"))
            generation_charge = num(row.get("generation_charge_cpa"))
            energy_charge = sum(value for value in [delivery_charge, generation_charge] if value is not None)
            export_credit = bill_export_credit(row)
            days = (end - start).days if start and end else None
            bill = {
                "source": row.get("source"),
                "periodStart": start.date().isoformat() if start else "",
                "periodEnd": end.date().isoformat() if end else "",
                "periodDays": days,
                "importKwh": import_kwh,
                "exportKwh": export_kwh,
                "netImportKwh": net_import_kwh,
                "energyChargeUsd": energy_charge,
                "deliveryChargeUsd": delivery_charge,
                "generationChargeUsd": generation_charge,
                "exportCreditUsd": export_credit,
                "importRateUsdPerKwh": safe_div(energy_charge, import_kwh),
                "netImportRateUsdPerKwh": safe_div(energy_charge, net_import_kwh),
                "exportCreditRateUsdPerKwh": safe_div(export_credit, export_kwh),
                "exportToImportRatio": safe_div(export_kwh, import_kwh),
            }
            import_rate = bill["importRateUsdPerKwh"]
            export_rate = bill["exportCreditRateUsdPerKwh"]
            avoided_export_value = (
                import_rate - export_rate if import_rate is not None and export_rate is not None else None
            )
            bill["solarSelfConsumptionValueUsdPerKwh"] = avoided_export_value
            bill["batterySelfConsumptionValueUsdPerKwh"] = avoided_export_value
            bill["selfConsumptionValueUsdPerKwh"] = avoided_export_value
            bills.append(bill)
    return sorted(bills, key=lambda item: item.get("periodStart") or "")


def weighted_rates(bills: list[dict[str, Any]]) -> dict[str, Any]:
    import_kwh = sum(num(item.get("importKwh")) or 0 for item in bills)
    export_kwh = sum(num(item.get("exportKwh")) or 0 for item in bills)
    net_kwh = sum(num(item.get("netImportKwh")) or 0 for item in bills)
    energy_charge = sum(num(item.get("energyChargeUsd")) or 0 for item in bills)
    export_credit = sum(num(item.get("exportCreditUsd")) or 0 for item in bills)
    import_rate = safe_div(energy_charge, import_kwh)
    export_rate = safe_div(export_credit, export_kwh)
    avoided_export_value = import_rate - export_rate if import_rate is not None and export_rate is not None else None
    return {
        "billCount": len(bills),
        "importKwh": import_kwh,
        "exportKwh": export_kwh,
        "netImportKwh": net_kwh,
        "energyChargeUsd": energy_charge,
        "exportCreditUsd": export_credit,
        "importRateUsdPerKwh": import_rate,
        "netImportRateUsdPerKwh": safe_div(energy_charge, net_kwh),
        "exportCreditRateUsdPerKwh": export_rate,
        "solarSelfConsumptionValueUsdPerKwh": avoided_export_value,
        "batterySelfConsumptionValueUsdPerKwh": avoided_export_value,
        "selfConsumptionValueUsdPerKwh": avoided_export_value,
    }


def estimated_grid_cost(kwh: Any, model: dict[str, Any], rate_key: str = "importRateUsdPerKwh") -> float | None:
    kwh = num(kwh)
    rate_value = num(model.get(rate_key))
    if kwh is None or rate_value is None:
        return None
    return kwh * rate_value


def load_chargepoint(model: dict[str, Any]) -> dict[str, Any]:
    sessions_payload = load_json(DATA_DIR / "chargepoint_sessions.json")
    sessions = sessions_payload.get("sessions") or []
    visible = sessions_payload.get("visibleTotals") or {}
    energy_kwh = num(visible.get("energyKwh"))
    cost_usd = num(visible.get("costUsd"))
    latest = sessions[0] if sessions else {}
    latest_kwh = num(latest.get("energyKwh"))
    latest_cost = num(latest.get("costUsd"))
    actual_rate = safe_div(cost_usd, energy_kwh)
    latest_rate = safe_div(latest_cost, latest_kwh)
    return {
        "capturedAt": sessions_payload.get("capturedAt"),
        "sessionCount": visible.get("sessionCount") or len(sessions),
        "energyKwh": energy_kwh,
        "costUsd": cost_usd,
        "actualRateUsdPerKwh": actual_rate,
        "latestSession": latest,
        "latestSessionRateUsdPerKwh": latest_rate,
        "latestSessionAtSceImportRateUsd": estimated_grid_cost(latest_kwh, model),
        "visibleTotalAtSceImportRateUsd": estimated_grid_cost(energy_kwh, model),
    }


def load_sense_baseline(model: dict[str, Any]) -> dict[str, Any]:
    devices = load_json(DATA_DIR / "sense_devices_latest.json")
    always_on = None
    for device in devices.get("devices") or []:
        if str(device.get("name") or "").lower() == "always on":
            always_on = device
            break
    trends = load_json(DATA_DIR / "sense_trends_latest.json")
    return {
        "capturedAt": devices.get("capturedAt") or trends.get("capturedAt"),
        "alwaysOnDevicePresent": always_on is not None,
        "alwaysOnName": always_on.get("name") if always_on else "",
        "alwaysAvailableContext": ALWAYS_AVAILABLE_CONTEXT,
        "note": "Always-on Macs, Apple TVs, and HomePods should be treated as expected base load first, then optimized after HVAC/EV/pumps/mystery heat are separated.",
        "exampleCostPerContinuousWattPerYearUsd": num(model.get("importRateUsdPerKwh")) * 8.76
        if num(model.get("importRateUsdPerKwh")) is not None
        else None,
    }


def load_envoy_costs(model: dict[str, Any]) -> dict[str, Any]:
    bill_home = load_json(DATA_DIR / "latest_bill_home_pairing.json")
    envoy = bill_home.get("envoy") or {}
    meters = envoy.get("meters") or {}
    site_load = num((meters.get("Consumption Total") or {}).get("deltaKwh"))
    grid_net = num((meters.get("Consumption Net") or {}).get("deltaKwh"))
    production = num((meters.get("Production") or {}).get("deltaKwh"))
    storage = num((meters.get("Storage") or {}).get("deltaKwh"))
    return {
        "coverage": envoy.get("coverage") or {},
        "siteLoadKwh": site_load,
        "gridNetImportKwh": grid_net,
        "solarProductionKwh": production,
        "storageDeltaKwh": storage,
        "siteLoadAtImportRateUsd": estimated_grid_cost(site_load, model),
        "gridNetAtImportRateUsd": estimated_grid_cost(grid_net, model),
        "gridNetAtNetImportRateUsd": estimated_grid_cost(grid_net, model, "netImportRateUsdPerKwh"),
    }


def load_alarm_costs(model: dict[str, Any]) -> dict[str, Any]:
    bill_home = load_json(DATA_DIR / "latest_bill_home_pairing.json")
    alarm = bill_home.get("alarm") or {}
    period = alarm.get("periodKwh") or {}
    estimates: dict[str, Any] = {}
    for key, kwh in period.items():
        estimates[key] = {
            "kwh": kwh,
            "atImportRateUsd": estimated_grid_cost(kwh, model),
            "atNetImportRateUsd": estimated_grid_cost(kwh, model, "netImportRateUsdPerKwh"),
        }
    return {
        "capturedAtLocal": alarm.get("capturedAtLocal"),
        "dashboard": alarm.get("dashboard") or {},
        "periodEstimates": estimates,
    }


def load_sce_interval_costs(model: dict[str, Any]) -> dict[str, Any]:
    all_energy = load_json(DATA_DIR / "latest_all_energy_pairs.json")
    summary = (all_energy.get("sceGreenButton") or {}).get("summary") or {}
    delivered = num(summary.get("deliveredKwh"))
    received = num(summary.get("receivedKwh"))
    net_import = num(summary.get("netImportKwh"))
    import_cost = estimated_grid_cost(delivered, model)
    export_credit = estimated_grid_cost(received, model, "exportCreditRateUsdPerKwh")
    return {
        "coverageStart": summary.get("coverageStart"),
        "coverageEnd": summary.get("coverageEnd"),
        "intervalCount": summary.get("intervalCount"),
        "deliveredKwh": delivered,
        "receivedKwh": received,
        "netImportKwh": net_import,
        "importCostAtLatestBillRateUsd": import_cost,
        "exportCreditAtLatestBillRateUsd": export_credit,
        "netCostAtLatestBillRatesUsd": import_cost - export_credit
        if import_cost is not None and export_credit is not None
        else None,
    }


def build_payload() -> dict[str, Any]:
    bills = load_bill_rates()
    latest = bills[-1] if bills else {}
    weighted = weighted_rates(bills)
    model = latest or weighted
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
        "model": {
            "basis": "latestClosedSceBill" if latest else "weightedBills",
            "latestClosedBill": latest,
            "weightedBills": weighted,
        },
        "billRates": bills,
        "sceIntervals": load_sce_interval_costs(model),
        "envoy": load_envoy_costs(model),
        "alarm": load_alarm_costs(model),
        "chargepoint": load_chargepoint(model),
        "senseBaseline": load_sense_baseline(model),
    }


def write_report(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    model = payload.get("model", {})
    latest = model.get("latestClosedBill") or {}
    weighted = model.get("weightedBills") or {}
    intervals = payload.get("sceIntervals") or {}
    envoy = payload.get("envoy") or {}
    alarm = payload.get("alarm") or {}
    chargepoint = payload.get("chargepoint") or {}
    baseline = payload.get("senseBaseline") or {}

    lines = [
        "# Energy Costs",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Cost basis: `{model.get('basis') or 'n/a'}`",
        "",
        "## Bill-Derived Rates",
        "",
        f"- Latest closed SCE bill: `{latest.get('periodStart') or 'n/a'}` to `{latest.get('periodEnd') or 'n/a'}`",
        f"- Import: `{fmt(latest.get('importKwh'), 0)}` kWh at `{rate(latest.get('importRateUsdPerKwh'))}`",
        f"- Export: `{fmt(latest.get('exportKwh'), 0)}` kWh credited at `{rate(latest.get('exportCreditRateUsdPerKwh'))}`",
        f"- Net import: `{fmt(latest.get('netImportKwh'), 0)}` kWh at bill-equivalent `{rate(latest.get('netImportRateUsdPerKwh'))}`",
        f"- Energy charge: `{money(latest.get('energyChargeUsd'))}`; export credits: `{money(latest.get('exportCreditUsd'))}`",
        f"- Direct solar self-consumption value: `{rate(latest.get('solarSelfConsumptionValueUsdPerKwh'))}`",
        f"- Battery-backed self-consumption value: `{rate(latest.get('batterySelfConsumptionValueUsdPerKwh'))}`",
        f"- Weighted bill import rate across `{weighted.get('billCount') or 0}` parsed bills: `{rate(weighted.get('importRateUsdPerKwh'))}`",
        "",
        "## SCE Interval Cost Pairing",
        "",
        f"- Interval coverage: `{intervals.get('coverageStart') or 'n/a'}` to `{intervals.get('coverageEnd') or 'n/a'}`",
        f"- Delivered/imported: `{fmt(intervals.get('deliveredKwh'), 1)}` kWh -> `{money(intervals.get('importCostAtLatestBillRateUsd'))}` at latest bill import rate",
        f"- Received/exported: `{fmt(intervals.get('receivedKwh'), 1)}` kWh -> `{money(intervals.get('exportCreditAtLatestBillRateUsd'))}` at latest bill export-credit rate",
        f"- Net cost equivalent: `{money(intervals.get('netCostAtLatestBillRatesUsd'))}`",
        "",
        "## Home Load Cost Equivalents",
        "",
        f"- Envoy window: `{(envoy.get('coverage') or {}).get('start') or 'n/a'}` to `{(envoy.get('coverage') or {}).get('end') or 'n/a'}`",
        f"- Site load: `{fmt(envoy.get('siteLoadKwh'), 1)}` kWh -> `{money(envoy.get('siteLoadAtImportRateUsd'))}` at latest bill import rate",
        f"- Grid net import: `{fmt(envoy.get('gridNetImportKwh'), 1)}` kWh -> `{money(envoy.get('gridNetAtImportRateUsd'))}` at latest bill import rate",
        f"- Grid net import with bill-equivalent net rate: `{money(envoy.get('gridNetAtNetImportRateUsd'))}`",
        "",
        "## Alarm.com Period Estimates",
        "",
        f"- Captured: `{alarm.get('capturedAtLocal') or 'n/a'}`",
        "| Period | kWh | Import-rate cost | Bill-net-rate cost |",
        "|---|---:|---:|---:|",
    ]
    for key, item in sorted((alarm.get("periodEstimates") or {}).items()):
        lines.append(
            f"| `{key}` | {fmt(item.get('kwh'), 1)} | {money(item.get('atImportRateUsd'))} | {money(item.get('atNetImportRateUsd'))} |"
        )
    lines.extend(
        [
            "",
            "## ChargePoint",
            "",
            f"- Visible home sessions: `{chargepoint.get('sessionCount') or 0}` sessions, `{fmt(chargepoint.get('energyKwh'), 1)}` kWh, `{money(chargepoint.get('costUsd'))}`",
            f"- Actual ChargePoint rate: `{rate(chargepoint.get('actualRateUsdPerKwh'))}`",
            f"- Same energy at latest SCE import rate: `{money(chargepoint.get('visibleTotalAtSceImportRateUsd'))}`",
            f"- Latest session actual rate: `{rate(chargepoint.get('latestSessionRateUsdPerKwh'))}`",
            "",
            "## Always-On Context",
            "",
            "- Known always-available devices: `2 M2 Mac minis`, `2 M4 Mac minis`, `1 MacBook Pro`, `5 Apple TVs`, and multiple HomePods.",
            f"- One continuous watt costs about `{money(baseline.get('exampleCostPerContinuousWattPerYearUsd'))}` per year at the latest SCE import rate.",
            "- Treat this as an expected base-load floor first; optimize the unexplained part after EV, HVAC, pumps, and heat signatures are separated.",
            "",
            "## Bill Period Rates",
            "",
            "| Period | Import | Export | Net import | Energy charge | Import rate | Export credit rate | Solar self-consumption value | Battery self-consumption value |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for bill in payload.get("billRates") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{bill.get('periodStart')} to {bill.get('periodEnd')}`",
                    fmt(bill.get("importKwh"), 0),
                    fmt(bill.get("exportKwh"), 0),
                    fmt(bill.get("netImportKwh"), 0),
                    money(bill.get("energyChargeUsd")),
                    rate(bill.get("importRateUsdPerKwh")),
                    rate(bill.get("exportCreditRateUsdPerKwh")),
                    rate(bill.get("solarSelfConsumptionValueUsdPerKwh")),
                    rate(bill.get("batterySelfConsumptionValueUsdPerKwh")),
                ]
            )
            + " |"
        )
    OUT_REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_payload()
    write_report(payload)
    print(OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
