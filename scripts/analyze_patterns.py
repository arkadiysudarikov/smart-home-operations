#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "smart_home.sqlite"
REPORT_DIR = ROOT / "reports"


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        raise SystemExit("No smart-home database yet. Run scripts/smart_home_snapshot.py first.")
    rows = []
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute("select * from snapshots order by captured_at asc"))
    metric_values: dict[str, list[float]] = defaultdict(list)
    warning_counts = []
    active_counts = []
    alarm_ok = 0
    for row in rows:
        warning_counts.append(row["warning_count"])
        active_counts.append(row["unifi_active_count"])
        alarm_ok += int(row["alarm_websocket"])
        metrics = json.loads(row["metrics_json"])
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                metric_values[key].append(float(value))
    lines = [
        "# Smart Home Patterns",
        "",
        f"- Generated: `{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}`",
        f"- Snapshots analyzed: `{len(rows)}`",
        "",
        "## Reliability",
        "",
        f"- Homebridge states observed: `{', '.join(sorted(set(row['homebridge_state'] or 'unknown' for row in rows)))}`",
        f"- Alarm.com websocket present: `{alarm_ok}/{len(rows)}` snapshots",
        f"- Warning count range: `{min(warning_counts) if warning_counts else 0}` to `{max(warning_counts) if warning_counts else 0}`",
        f"- UniFi active occupancy count range: `{min(active_counts) if active_counts else 0}` to `{max(active_counts) if active_counts else 0}`",
        "",
        "## Energy",
        "",
    ]
    if metric_values:
        for key, values in sorted(metric_values.items()):
            avg = sum(values) / len(values)
            lines.append(f"- {key}: min `{min(values):.3f}`, avg `{avg:.3f}`, max `{max(values):.3f}`")
    else:
        lines.append("- No numeric energy metrics captured yet.")
    lines.extend(
        [
            "",
            "## Next Automation Candidates",
            "",
            "- Alert when Homebridge is not `running`.",
            "- Alert when Alarm.com websocket is absent for multiple snapshots.",
            "- Track Enphase production, house load, battery level, and grid import/export trends.",
            "- Use UniFi occupancy stability before driving Home automations.",
        ]
    )
    (REPORT_DIR / "patterns.md").write_text("\n".join(lines) + "\n")
    print(REPORT_DIR / "patterns.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

