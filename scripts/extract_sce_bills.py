#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime
    raise SystemExit(
        "Missing pypdf. Run with the bundled Codex Python runtime from "
        "~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LEGACY_EXTRACT = Path.home() / "Documents" / "sce_enphase_reconciliation" / "sce_bill_extract.csv"
SKIP_DISCOVERY_DIRS = {"Codex", ".git", "node_modules"}


@dataclass
class BillReading:
    source: str
    prepared_date: str = ""
    due_date: str = ""
    period_start: str = ""
    period_end: str = ""
    amount_due: str = ""
    new_charges: str = ""
    delivery_charge_sce: str = ""
    generation_charge_cpa: str = ""
    import_kwh_sce: str = ""
    export_kwh_sce: str = ""
    midpeak_kwh: str = ""
    offpeak_kwh: str = ""
    superoffpeak_kwh: str = ""
    delivery_export_credit_dollars: str = ""
    delivery_export_bonus_credit_dollars: str = ""
    cpa_export_kwh: str = ""
    cpa_export_credit_dollars: str = ""
    cpa_relevant_period_ytd_kwh: str = ""
    import_minus_export_kwh: str = ""

    def key(self) -> tuple[str, str]:
        return (self.period_start, self.period_end)

    def as_dict(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in FIELDNAMES}


FIELDNAMES = [
    "source",
    "prepared_date",
    "due_date",
    "period_start",
    "period_end",
    "amount_due",
    "new_charges",
    "delivery_charge_sce",
    "generation_charge_cpa",
    "import_kwh_sce",
    "export_kwh_sce",
    "midpeak_kwh",
    "offpeak_kwh",
    "superoffpeak_kwh",
    "delivery_export_credit_dollars",
    "delivery_export_bonus_credit_dollars",
    "cpa_export_kwh",
    "cpa_export_credit_dollars",
    "cpa_relevant_period_ytd_kwh",
    "import_minus_export_kwh",
]


def clean_number(raw: str | None) -> str:
    if raw is None:
        return ""
    return raw.replace(",", "").strip()


def money(raw: str | None) -> str:
    return clean_number(raw).replace("$", "")


def find_one(pattern: str, text: str, flags: int = re.S) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else ""


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def is_sce_bill(text: str) -> bool:
    return "Your electricity bill" in text and "TOU-D-PRIME" in text


def parse_bill(path: Path) -> BillReading | None:
    text = read_pdf(path)
    if not is_sce_bill(text):
        return None
    flat = re.sub(r"\s+", " ", text)
    bill = BillReading(source=str(path))
    bill.prepared_date = find_one(r"Date bill prepared\s+([0-9/]+)", flat)
    bill.due_date = find_one(r"Due by\s+([0-9/]+)", flat)
    bill.amount_due = money(find_one(r"Amount due\s+\$([0-9,.]+)", flat))
    bill.new_charges = money(find_one(r"Your new charges\s+\$([0-9,.]+)", flat))

    period = re.search(r"Billing period:\s*([0-9/]+)\s+to\s+([0-9/]+)", flat)
    if not period:
        period = re.search(r"([0-9/]+)\s+to\s+([0-9/]+)\s+TOU-D-PRIME", flat)
    if period:
        bill.period_start = period.group(1)
        bill.period_end = period.group(2)

    summary = re.search(
        r"Summary of your billing detail.*?TOU-D-PRIME\s+\(SCE\)\s+\$([0-9,.]+).*?SBP TOU-D-PRIME\s+\$([0-9,.]+)",
        flat,
        re.S,
    )
    if summary:
        bill.delivery_charge_sce = money(summary.group(1))
        bill.generation_charge_cpa = money(summary.group(2))
    if not bill.delivery_charge_sce:
        bill.delivery_charge_sce = money(find_one(r"Your rate: TOU-D-PRIME \(SCE\).*?Your new charges\s+\$([0-9,.]+)", flat))
    if not bill.generation_charge_cpa:
        bill.generation_charge_cpa = money(find_one(r"Sub-Total of CPA Generation Charges\s+\$([0-9,.]+)", flat))

    bill.import_kwh_sce = clean_number(find_one(r"Total electricity you used this month in kWh\s+([0-9,]+)", flat))
    bill.export_kwh_sce = clean_number(find_one(r"Total electricity you exported this month in kWh\s+([0-9,]+)", flat))
    bill.midpeak_kwh = clean_number(find_one(r"Mid peak\s+([0-9,]+)\s+kWh\s+x", flat, re.I | re.S))
    bill.offpeak_kwh = clean_number(find_one(r"Off peak\s+([0-9,]+)\s+kWh\s+x", flat, re.I | re.S))
    bill.superoffpeak_kwh = clean_number(find_one(r"Super off peak\s+([0-9,]+)\s+kWh\s+x", flat, re.I | re.S))

    delivery_export = re.search(r"Energy export credit - Delivery\s+([0-9,.]+)\s+kWh\s+x\s+-\$[0-9.]+\s+-\$([0-9,.]+)", flat)
    if delivery_export:
        bill.delivery_export_credit_dollars = delivery_export.group(2)
    delivery_bonus = re.search(r"Energy export bonus credit\s+[0-9,.]+\s+kWh\s+x\s+-\$[0-9.]+\s+-\$([0-9,.]+)", flat)
    if delivery_bonus:
        bill.delivery_export_bonus_credit_dollars = delivery_bonus.group(1)

    cpa_export = re.search(r"Energy Export Credit\s+-([0-9,.]+)\s+kWh\s+@\s+[0-9.]+\s+-\$([0-9,.]+)", flat)
    if cpa_export:
        bill.cpa_export_kwh = clean_number(cpa_export.group(1))
        bill.cpa_export_credit_dollars = cpa_export.group(2)
    bill.cpa_relevant_period_ytd_kwh = clean_number(
        find_one(r"cumulative kWh relevant period\s+year-to-date:\s+([0-9,.]+)\s+kWh", flat, re.I | re.S)
    )

    try:
        bill.import_minus_export_kwh = str(float(bill.import_kwh_sce) - float(bill.export_kwh_sce))
    except ValueError:
        pass
    return bill


def discover_pdfs() -> list[Path]:
    roots = [Path.home() / "Documents", Path.home() / "Downloads"]
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda _exc: None):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DISCOVERY_DIRS]
            for filename in filenames:
                if filename.startswith("ViewBill") and filename.endswith(".pdf"):
                    path = Path(dirpath) / filename
                    found[str(path)] = path
    return sorted(found.values())


def load_legacy() -> list[BillReading]:
    if not LEGACY_EXTRACT.exists():
        return []
    out: list[BillReading] = []
    with LEGACY_EXTRACT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            bill = BillReading(source=row.get("source", ""))
            for key in FIELDNAMES:
                if key in row:
                    setattr(bill, key, row.get(key) or "")
            out.append(bill)
    return out


def sort_key(bill: BillReading) -> tuple[datetime, str]:
    for raw in (bill.period_start, bill.prepared_date):
        try:
            return (datetime.strptime(raw, "%m/%d/%y"), bill.source)
        except ValueError:
            continue
    return (datetime.min, bill.source)


def merge_bills(legacy: list[BillReading], parsed: list[BillReading]) -> list[BillReading]:
    merged: dict[tuple[str, str], BillReading] = {}
    for bill in legacy + parsed:
        key = bill.key()
        if not all(key):
            key = (bill.prepared_date, bill.source)
        existing = merged.get(key)
        if existing is None or Path(bill.source).exists():
            merged[key] = bill
    return sorted(merged.values(), key=sort_key)


def write_outputs(bills: list[BillReading], parsed_sources: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / "sce_bill_readings.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for bill in bills:
            writer.writerow(bill.as_dict())

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        "# SCE Bill Readings",
        "",
        f"- Generated: `{generated_at}`",
        f"- Bill rows: `{len(bills)}`",
        f"- Parsed PDF sources this run: `{len(parsed_sources)}`",
        "",
        "| Period | Prepared | SCE import kWh | SCE export kWh | Net import kWh | SCE delivery | CPA generation | Source |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for bill in bills:
        period = f"{bill.period_start} to {bill.period_end}"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{period}`",
                    f"`{bill.prepared_date}`",
                    bill.import_kwh_sce,
                    bill.export_kwh_sce,
                    bill.import_minus_export_kwh,
                    bill.delivery_charge_sce,
                    bill.generation_charge_cpa,
                    f"`{Path(bill.source).name}`",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- `{csv_path}`",
            f"- `{REPORT_DIR / 'sce_bill_readings.md'}`",
        ]
    )
    (REPORT_DIR / "sce_bill_readings.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parsed: list[BillReading] = []
    for path in discover_pdfs():
        bill = parse_bill(path)
        if bill is not None:
            parsed.append(bill)
    bills = merge_bills(load_legacy(), parsed)
    write_outputs(bills, [bill.source for bill in parsed])
    print(REPORT_DIR / "sce_bill_readings.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
