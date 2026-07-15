#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

SCE_FILE_RE = re.compile(r"^SCE_Usage_(?:\d+|GBC|UtilityAPI)_.*\.(csv|xml)$", re.I)
TIME_RANGE_RE = re.compile(r"(.+?)\s+to\s+(.+)")
SENSE_RE = re.compile(r"Watts: ([\d.-]+), Current: ([\d.-]+), Voltage: ([\d.-]+)")
ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")


@dataclass
class SceInterval:
    start: datetime
    end: datetime
    delivered_kwh: float | None = None
    received_kwh: float | None = None
    qualities: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    @property
    def net_import_kwh(self) -> float | None:
        if self.delivered_kwh is None and self.received_kwh is None:
            return None
        return (self.delivered_kwh or 0.0) - (self.received_kwh or 0.0)


def normalize(text: str) -> str:
    return text.replace("\ufeff", "").replace("\xa0", " ").strip().strip('"')


def parse_local_dt(raw: str) -> datetime:
    raw = normalize(raw)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M%p",
        "%m/%d/%y %I:%M %p",
        "%m/%d/%y",
        "%m/%d/%Y",
    ):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=LOCAL_TZ)
        except ValueError:
            pass
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def parse_iso(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def interval_key(start: datetime, end: datetime) -> tuple[str, str]:
    return (
        start.astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
        end.astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
    )


def walk_sce_candidates(root: Path, max_depth: int | None, skip_dirs: set[str]) -> list[Path]:
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            relative = current.relative_to(root)
        except ValueError:
            continue
        depth = 0 if str(relative) == "." else len(relative.parts)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in skip_dirs and (max_depth is None or depth < max_depth)
        ]
        for filename in filenames:
            if SCE_FILE_RE.search(filename):
                candidates.append(current / filename)
    return candidates


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def external_file_scan_enabled() -> bool:
    return os.environ.get("SMART_HOME_SCAN_EXTERNAL_FILES", "").strip().lower() in {"1", "true", "yes", "on"}


def discover_sce_files(extra_files: list[Path], *, scan_external: bool = False) -> list[Path]:
    roots: dict[Path, int | None] = {DATA_DIR: None}
    if scan_external:
        roots.update(
            {
                Path.home() / "Downloads": 2,
                Path.home() / "Documents": 3,
                Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs": 4,
            }
        )
    skip_dirs = {
        ".cache",
        ".git",
        ".venv",
        "__pycache__",
        "Library",
        "node_modules",
    }
    found: dict[str, Path] = {}
    seen_content: set[str] = set()
    for path in extra_files:
        if path.exists() and path.is_file():
            found[str(path)] = path
            seen_content.add(file_fingerprint(path))
    for root, max_depth in roots.items():
        if not root.exists():
            continue
        for path in walk_sce_candidates(root, max_depth, skip_dirs):
            if not path.is_file():
                continue
            fingerprint = file_fingerprint(path)
            if fingerprint in seen_content:
                continue
            seen_content.add(fingerprint)
            found[str(path)] = path
    return sorted(found.values(), key=lambda item: (item.name, str(item)))


def merge_interval(
    intervals: dict[tuple[str, str], SceInterval],
    start: datetime,
    end: datetime,
    direction: str,
    kwh: float,
    quality: str,
    source: Path,
) -> None:
    key = interval_key(start, end)
    item = intervals.get(key)
    if item is None:
        item = SceInterval(start=start, end=end)
        intervals[key] = item
    if direction == "delivered":
        item.delivered_kwh = kwh
    elif direction == "received":
        item.received_kwh = kwh
    if quality:
        item.qualities.add(quality)
    item.sources.add(str(source))


def parse_sce_csv(path: Path, intervals: dict[tuple[str, str], SceInterval]) -> int:
    count = 0
    direction: str | None = None
    detail_header: dict[str, int] | None = None
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            normalized = [normalize(cell) for cell in row]
            first = normalized[0]
            compact = " ".join(normalized)
            lower = [cell.lower() for cell in normalized]
            if {
                "energy consumption time period start",
                "energy consumption time period end",
                "delivered",
                "received",
            }.issubset(set(lower)):
                detail_header = {name: idx for idx, name in enumerate(lower)}
                direction = None
                continue
            if detail_header is not None and len(row) > max(detail_header.values()):
                try:
                    start = parse_local_dt(normalized[detail_header["energy consumption time period start"]])
                    end = parse_local_dt(normalized[detail_header["energy consumption time period end"]])
                    delivered = float(normalized[detail_header["delivered"]].replace(",", ""))
                    received = float(normalized[detail_header["received"]].replace(",", ""))
                except ValueError:
                    continue
                merge_interval(intervals, start, end, "delivered", delivered, "", path)
                merge_interval(intervals, start, end, "received", received, "", path)
                count += 1
                continue
            if "Energy" in first and "Delivered time period" in compact:
                direction = "delivered"
                continue
            if "Energy" in first and "Received time period" in compact:
                direction = "received"
                continue
            if direction is None or len(row) < 2:
                continue
            match = TIME_RANGE_RE.match(first)
            if not match:
                continue
            try:
                start = parse_local_dt(match.group(1))
                end = parse_local_dt(match.group(2))
                kwh = float(normalize(row[1]).replace(",", ""))
            except ValueError:
                continue
            quality = normalize(row[2]) if len(row) > 2 else ""
            merge_interval(intervals, start, end, direction, kwh, quality, path)
            count += 1
    return count


def parse_sce_xml(path: Path, intervals: dict[tuple[str, str], SceInterval]) -> int:
    ns = {"atom": "http://www.w3.org/2005/Atom", "espi": "http://naesb.org/espi"}
    root = ET.fromstring(path.read_text(encoding="utf-8-sig", errors="replace"))
    count = 0
    direction: str | None = None
    for entry in root.findall("atom:entry", ns):
        title = normalize(entry.findtext("atom:title", default="", namespaces=ns)).lower()
        content = entry.find("atom:content", ns)
        if "energy delivered" in title:
            direction = "delivered"
            continue
        if "energy received" in title:
            direction = "received"
            continue
        if content is None or direction is None:
            continue
        block = content.find("espi:IntervalBlock", ns)
        if block is None:
            continue
        for reading in block.findall("espi:IntervalReading", ns):
            period = reading.find("espi:timePeriod", ns)
            value_raw = reading.findtext("espi:value", namespaces=ns)
            if period is None or value_raw is None:
                continue
            start_raw = period.findtext("espi:start", namespaces=ns)
            duration_raw = period.findtext("espi:duration", namespaces=ns)
            if start_raw is None or duration_raw is None:
                continue
            start = datetime.fromtimestamp(int(start_raw), tz=LOCAL_TZ)
            end = datetime.fromtimestamp(int(start_raw) + int(duration_raw), tz=LOCAL_TZ)
            quality = reading.findtext("espi:ReadingQuality/espi:quality", default="", namespaces=ns)
            merge_interval(intervals, start, end, direction, float(value_raw) / 1000.0, quality, path)
            count += 1
    return count


def load_existing_sce_usage_intervals(path: Path, intervals: dict[tuple[str, str], SceInterval]) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                start = parse_local_dt(row["start"])
                end = parse_local_dt(row["end"])
                delivered = float(row["delivered_kwh"]) if row.get("delivered_kwh") not in (None, "") else None
                received = float(row["received_kwh"]) if row.get("received_kwh") not in (None, "") else None
            except (KeyError, ValueError):
                continue
            if delivered is not None:
                merge_interval(intervals, start, end, "delivered", delivered, row.get("qualities") or "", path)
            if received is not None:
                merge_interval(intervals, start, end, "received", received, row.get("qualities") or "", path)
            count += 1
    return count


def load_sce_intervals(files: list[Path]) -> tuple[list[SceInterval], list[dict[str, Any]]]:
    intervals: dict[tuple[str, str], SceInterval] = {}
    file_stats: list[dict[str, Any]] = []
    existing_path = DATA_DIR / "sce_usage_intervals.csv"
    existing_before = len(intervals)
    existing_rows = load_existing_sce_usage_intervals(existing_path, intervals)
    if existing_rows:
        file_stats.append(
            {
                "path": str(existing_path),
                "parsedRows": existing_rows,
                "newIntervals": len(intervals) - existing_before,
                "modified": datetime.fromtimestamp(existing_path.stat().st_mtime, tz=LOCAL_TZ).isoformat(timespec="seconds"),
                "preserved": True,
            }
        )
    for path in files:
        if path == existing_path:
            continue
        before = len(intervals)
        parsed = parse_sce_csv(path, intervals) if path.suffix.lower() == ".csv" else parse_sce_xml(path, intervals)
        file_stats.append(
            {
                "path": str(path),
                "parsedRows": parsed,
                "newIntervals": len(intervals) - before,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=LOCAL_TZ).isoformat(timespec="seconds"),
            }
        )
    return sorted(intervals.values(), key=lambda item: item.start), file_stats


def load_monitor_samples() -> dict[str, list[dict[str, Any]]]:
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
            captured_at = parse_iso(row["captured_at"])
            message = row["message"] or ""
            if row["component"] == "Sense Energy Meter":
                match = SENSE_RE.search(message)
                if match:
                    samples["sense"].append(
                        {
                            "capturedAt": captured_at,
                            "kw": float(match.group(1)) / 1000.0,
                        }
                    )
                continue
            match = ENVOY_POWER_RE.search(message)
            if match:
                meter = match.group(1)
                samples[f"envoy:{meter}"].append(
                    {
                        "capturedAt": captured_at,
                        "kw": float(match.group(2)),
                    }
                )
    return samples


def build_sample_index(samples: list[dict[str, Any]]) -> dict[str, list[float]]:
    ordered = sorted(samples, key=lambda item: item["capturedAt"])
    timestamps: list[float] = []
    values: list[float] = []
    for item in ordered:
        timestamps.append(item["capturedAt"].timestamp())
        values.append(float(item["kw"]))
    return {"timestamps": timestamps, "values": values}


def estimate_interval_kwh(index: dict[str, list[float]], start: datetime, end: datetime) -> float | None:
    timestamps = index["timestamps"]
    if not timestamps:
        return None
    values = index["values"]
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    left = bisect.bisect_left(timestamps, start_ts)
    right = bisect.bisect_right(timestamps, end_ts)
    if left == right:
        return None

    def boundary_value(timestamp: float, insertion: int) -> float:
        if insertion <= 0:
            return values[0]
        if insertion >= len(timestamps):
            return values[-1]
        before_ts, after_ts = timestamps[insertion - 1], timestamps[insertion]
        before_value, after_value = values[insertion - 1], values[insertion]
        if after_ts == before_ts:
            return after_value
        fraction = (timestamp - before_ts) / (after_ts - before_ts)
        return before_value + (after_value - before_value) * fraction

    points: list[tuple[float, float]] = [
        (start_ts, boundary_value(start_ts, bisect.bisect_left(timestamps, start_ts)))
    ]
    points.extend(
        (timestamps[index], values[index])
        for index in range(left, right)
        if start_ts < timestamps[index] < end_ts
    )
    points.append((end_ts, boundary_value(end_ts, bisect.bisect_left(timestamps, end_ts))))
    watt_hours = sum(
        ((first_value + second_value) / 2.0) * ((second_ts - first_ts) / 3600.0)
        for (first_ts, first_value), (second_ts, second_value) in zip(points, points[1:])
    )
    return watt_hours


def build_overlap_pairs(intervals: list[SceInterval], samples: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    envoy_total = build_sample_index(samples.get("envoy:Consumption Total", []))
    envoy_net = build_sample_index(samples.get("envoy:Consumption Net", []))
    envoy_production = build_sample_index(samples.get("envoy:Production", []))
    envoy_storage = build_sample_index(samples.get("envoy:Storage", []))
    sense = build_sample_index(samples.get("sense", []))
    for interval in intervals:
        envoy_total_kwh = estimate_interval_kwh(envoy_total, interval.start, interval.end)
        envoy_net_kwh = estimate_interval_kwh(envoy_net, interval.start, interval.end)
        envoy_production_kwh = estimate_interval_kwh(envoy_production, interval.start, interval.end)
        envoy_storage_kwh = estimate_interval_kwh(envoy_storage, interval.start, interval.end)
        sense_kwh = estimate_interval_kwh(sense, interval.start, interval.end)
        if envoy_total_kwh is None and envoy_net_kwh is None and envoy_production_kwh is None and envoy_storage_kwh is None and sense_kwh is None:
            continue
        invalid_metrics: list[str] = []
        envoy_total_raw = envoy_total_kwh
        envoy_production_raw = envoy_production_kwh
        envoy_site_load_kwh = (
            envoy_total_kwh + envoy_storage_kwh
            if envoy_total_kwh is not None and envoy_storage_kwh is not None
            else envoy_total_kwh
        )
        envoy_site_load_raw = envoy_site_load_kwh
        if envoy_site_load_kwh is not None and envoy_site_load_kwh < 0:
            invalid_metrics.append("envoySiteLoadKwhEstimate")
            envoy_site_load_kwh = None
        if envoy_production_kwh is not None and envoy_production_kwh < -0.01:
            invalid_metrics.append("envoyProductionKwhEstimate")
            envoy_production_kwh = None
        elif envoy_production_kwh is not None and envoy_production_kwh < 0:
            envoy_production_kwh = 0.0
        pair = {
                "start": interval.start.isoformat(timespec="seconds"),
                "end": interval.end.isoformat(timespec="seconds"),
                "sceDeliveredKwh": interval.delivered_kwh,
                "sceReceivedKwh": interval.received_kwh,
                "sceNetImportKwh": interval.net_import_kwh,
                "envoyConsumptionTotalKwhEstimate": envoy_total_kwh,
                "envoyConsumptionNetKwhEstimate": envoy_net_kwh,
                "envoyProductionKwhEstimate": envoy_production_kwh,
                "envoyStorageKwhEstimate": envoy_storage_kwh,
                "envoySiteLoadKwhEstimate": envoy_site_load_kwh,
                "senseKwhEstimate": sense_kwh,
            }
        if invalid_metrics:
            pair["invalidMetrics"] = invalid_metrics
        if envoy_site_load_raw is not None and envoy_site_load_raw != envoy_site_load_kwh:
            pair["envoySiteLoadKwhRawEstimate"] = envoy_site_load_raw
        if envoy_production_raw is not None and envoy_production_raw != envoy_production_kwh:
            pair["envoyProductionKwhRawEstimate"] = envoy_production_raw
        pairs.append(pair)
    return pairs


def summarize_intervals(intervals: list[SceInterval]) -> dict[str, Any]:
    delivered = sum(item.delivered_kwh or 0.0 for item in intervals)
    received = sum(item.received_kwh or 0.0 for item in intervals)
    starts = [item.start for item in intervals]
    ends = [item.end for item in intervals]
    return {
        "intervalCount": len(intervals),
        "coverageStart": min(starts).isoformat(timespec="seconds") if starts else None,
        "coverageEnd": max(ends).isoformat(timespec="seconds") if ends else None,
        "deliveredKwh": delivered,
        "receivedKwh": received,
        "netImportKwh": delivered - received,
        "sourceCount": len({source for item in intervals for source in item.sources}),
    }


def summarize_samples(samples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, rows in samples.items():
        if not rows:
            continue
        out[key] = {
            "count": len(rows),
            "start": min(item["capturedAt"] for item in rows).isoformat(timespec="seconds"),
            "end": max(item["capturedAt"] for item in rows).isoformat(timespec="seconds"),
        }
    return out


def load_bill_extract() -> list[dict[str, Any]]:
    path = DATA_DIR / "sce_bill_readings.csv"
    if not path.exists():
        path = Path.home() / "Documents" / "sce_enphase_reconciliation" / "sce_bill_extract.csv"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def load_existing_realtime_pair_summary() -> dict[str, Any] | None:
    path = DATA_DIR / "latest_energy_pairs.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    recent = payload.get("pairs", [])[-24:]
    if not recent:
        return {"pairCount": payload.get("pairCount", 0)}
    ratios = [item["ratioSenseToEnvoyTotal"] for item in recent if item.get("ratioSenseToEnvoyTotal") is not None]
    return {
        "generatedAt": payload.get("generatedAt"),
        "pairCount": payload.get("pairCount", 0),
        "recentCount": len(recent),
        "recentAverageEnvoyMinusSenseKw": mean(item["deltaKw"] for item in recent),
        "recentAverageSenseToEnvoyRatio": mean(ratios) if ratios else None,
    }


def write_outputs(
    intervals: list[SceInterval],
    file_stats: list[dict[str, Any]],
    samples: dict[str, list[dict[str, Any]]],
    overlap_pairs: list[dict[str, Any]],
    bill_rows: list[dict[str, Any]],
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds")
    sce_summary = summarize_intervals(intervals)
    monitor_summary = summarize_samples(samples)
    realtime_summary = load_existing_realtime_pair_summary()

    csv_path = DATA_DIR / "sce_usage_intervals.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "start",
                "end",
                "delivered_kwh",
                "received_kwh",
                "net_import_kwh",
                "qualities",
                "source_count",
            ]
        )
        for item in intervals:
            writer.writerow(
                [
                    item.start.isoformat(timespec="seconds"),
                    item.end.isoformat(timespec="seconds"),
                    item.delivered_kwh,
                    item.received_kwh,
                    item.net_import_kwh,
                    ";".join(sorted(item.qualities)),
                    len(item.sources),
                ]
            )

    invalid_reading_counts: dict[str, int] = {}
    for pair in overlap_pairs:
        for metric in pair.get("invalidMetrics") or []:
            invalid_reading_counts[metric] = invalid_reading_counts.get(metric, 0) + 1
    payload = {
        "generatedAt": generated_at,
        "sceGreenButton": {
            "summary": sce_summary,
            "files": file_stats,
        },
        "smartHomeMonitor": monitor_summary,
        "overlapPairCount": len(overlap_pairs),
        "overlapPairs": overlap_pairs,
        "invalidReadingCounts": invalid_reading_counts,
        "sceBills": bill_rows,
        "realtimeSenseEnvoy": realtime_summary,
    }
    (DATA_DIR / "latest_all_energy_pairs.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# All Energy Reading Pairing",
        "",
        f"- Generated: `{generated_at}`",
        f"- SCE Green Button files loaded: `{len(file_stats)}`",
        f"- SCE interval rows after de-duplication: `{sce_summary['intervalCount']}`",
        f"- SCE interval coverage: `{sce_summary['coverageStart'] or 'n/a'}` to `{sce_summary['coverageEnd'] or 'n/a'}`",
        f"- SCE delivered / received / net import: `{sce_summary['deliveredKwh']:.1f}` / `{sce_summary['receivedKwh']:.1f}` / `{sce_summary['netImportKwh']:.1f}` kWh",
        f"- Smart Home overlap pairs with SCE intervals: `{len(overlap_pairs)}`",
        "",
        "## Current Pairing Status",
        "",
    ]
    if overlap_pairs:
        lines.append("- Current SCE interval data overlaps the Smart Home monitor database.")
    else:
        lines.extend(
            [
                "- No SCE interval data overlaps the current Smart Home monitor database.",
                "- The monitor has live Sense/Enphase readings, but the newest local SCE interval export is older than the monitor window.",
                "- To pair current utility readings, download a fresh SCE Green Button CSV or XML covering the monitor window and rerun this script.",
            ]
        )
    if realtime_summary:
        ratio = realtime_summary.get("recentAverageSenseToEnvoyRatio")
        ratio_text = "n/a" if ratio is None else f"{ratio:.1%}"
        lines.extend(
            [
                "",
                "## Live Sense / Enphase Pairing",
                "",
                f"- Existing realtime pairs: `{realtime_summary.get('pairCount', 0)}`",
                f"- Recent average Envoy minus Sense: `{realtime_summary.get('recentAverageEnvoyMinusSenseKw', 0):.3f} kW`",
                f"- Recent average Sense / Envoy total ratio: `{ratio_text}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Smart Home Monitor Coverage",
            "",
            "| Source | Samples | Start | End |",
            "|---|---:|---|---|",
        ]
    )
    for key in sorted(monitor_summary):
        item = monitor_summary[key]
        lines.append(f"| `{key}` | `{item['count']}` | `{item['start']}` | `{item['end']}` |")

    lines.extend(
        [
            "",
            "## SCE Bill-Level Readings",
            "",
            "| Period | Import kWh | Export kWh | Net import kWh | Source |",
            "|---|---:|---:|---:|---|",
        ]
    )
    if bill_rows:
        for row in bill_rows:
            period = f"{row.get('period_start', '')} to {row.get('period_end', '')}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{period}`",
                        row.get("import_kwh_sce", ""),
                        row.get("export_kwh_sce", ""),
                        row.get("import_minus_export_kwh", ""),
                        f"`{Path(row.get('source', '')).name}`",
                    ]
                )
                + " |"
            )
    else:
        lines.append("| n/a |  |  |  | No bill extract found |")

    lines.extend(
        [
            "",
            "## Loaded SCE Green Button Files",
            "",
            "| File | Parsed rows | New intervals | Modified |",
            "|---|---:|---:|---|",
        ]
    )
    for stat in file_stats:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{Path(stat['path']).name}`",
                    str(stat["parsedRows"]),
                    str(stat["newIntervals"]),
                    f"`{stat['modified']}`",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## SCE Download Handoff",
            "",
            "1. Sign in to SCE My Account.",
            "2. Open `Data Sharing & Download`.",
            "3. Choose the service account, select the last 12 months, and download CSV or XML Green Button data.",
            "4. Put the file in `data/sce-downloads/` or rerun `./scripts/analyze_all_energy_readings.py --scan-external-files` for a one-time scan of Downloads, Documents, and iCloud Drive.",
            "",
            "## Output Files",
            "",
            f"- `{csv_path}`",
            f"- `{DATA_DIR / 'latest_all_energy_pairs.json'}`",
            f"- `{REPORT_DIR / 'all_energy_pairing.md'}`",
        ]
    )
    (REPORT_DIR / "all_energy_pairing.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pair SCE, bill, Sense, and Enphase energy readings.")
    parser.add_argument("--sce-file", action="append", default=[], help="Specific SCE Green Button CSV/XML file to import.")
    parser.add_argument(
        "--scan-external-files",
        action="store_true",
        help="also scan Downloads, Documents, and iCloud Drive for SCE Green Button files",
    )
    args = parser.parse_args()

    files = discover_sce_files(
        [Path(item).expanduser() for item in args.sce_file],
        scan_external=args.scan_external_files or external_file_scan_enabled(),
    )
    intervals, file_stats = load_sce_intervals(files)
    samples = load_monitor_samples()
    overlap_pairs = build_overlap_pairs(intervals, samples)
    bill_rows = load_bill_extract()
    write_outputs(intervals, file_stats, samples, overlap_pairs, bill_rows)
    print(REPORT_DIR / "all_energy_pairing.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
