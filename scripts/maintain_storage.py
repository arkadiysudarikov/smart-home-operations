#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "smart_home.sqlite"
REPORT_DIR = ROOT / "reports"
SNAPSHOT_NAME_RE = re.compile(r"^(\d{8})T(\d{6})([+-]\d{4}|Z)\.json$")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc).astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


def snapshot_file_datetime(path: Path) -> datetime | None:
    match = SNAPSHOT_NAME_RE.match(path.name)
    if not match:
        return None
    date_part, time_part, offset = match.groups()
    if offset == "Z":
        offset = "+0000"
    try:
        return datetime.strptime(f"{date_part}{time_part}{offset}", "%Y%m%d%H%M%S%z")
    except ValueError:
        return None


def prune_snapshot_files(days: int) -> tuple[int, int]:
    if not SNAPSHOT_DIR.exists():
        return (0, 0)
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=days)
    deleted = 0
    bytes_deleted = 0
    for path in SNAPSHOT_DIR.glob("*.json"):
        try:
            captured_at = snapshot_file_datetime(path)
            if captured_at is None:
                captured_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()
            if captured_at >= cutoff:
                continue
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            bytes_deleted += size
        except FileNotFoundError:
            continue
    return deleted, bytes_deleted


def compact_db(config: dict) -> dict:
    if not DB_PATH.exists():
        return {
            "snapshotsDeleted": 0,
            "eventsDeleted": 0,
            "snapshotRawRowsCompacted": 0,
            "dbBytesBefore": 0,
            "dbBytesAfter": 0,
        }
    retention = config["retention"]
    db_bytes_before = DB_PATH.stat().st_size
    snapshot_cutoff = cutoff_iso(int(retention["snapshot_db_days"]))
    event_cutoff = cutoff_iso(int(retention["home_event_days"]))
    keep_raw_rows = int(retention["keep_recent_snapshot_raw_json_rows"])
    with sqlite3.connect(DB_PATH) as db:
        before = db.total_changes
        db.execute("delete from snapshots where captured_at < ?", (snapshot_cutoff,))
        snapshots_deleted = db.total_changes - before

        before = db.total_changes
        db.execute("delete from home_events where captured_at < ?", (event_cutoff,))
        events_deleted = db.total_changes - before

        before = db.total_changes
        db.execute(
            """
            update snapshots
               set raw_json = '{}'
             where id not in (
               select id from snapshots order by captured_at desc limit ?
             )
               and raw_json != '{}'
            """,
            (keep_raw_rows,),
        )
        compacted = db.total_changes - before

        db.execute("pragma optimize")
        db.commit()
        if retention.get("vacuum_after_cleanup", False) and (snapshots_deleted or events_deleted or compacted):
            db.execute("vacuum")
    return {
        "snapshotsDeleted": snapshots_deleted,
        "eventsDeleted": events_deleted,
        "snapshotRawRowsCompacted": compacted,
        "dbBytesBefore": db_bytes_before,
        "dbBytesAfter": DB_PATH.stat().st_size,
    }


def write_report(file_result: tuple[int, int], db_result: dict, config: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    deleted_files, deleted_bytes = file_result
    lines = [
        "# Smart Home Storage Maintenance",
        "",
        f"- Generated: `{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}`",
        f"- Snapshot file retention: `{config['retention']['snapshot_files_days']}` days",
        f"- Snapshot DB retention: `{config['retention']['snapshot_db_days']}` days",
        f"- Home event DB retention: `{config['retention']['home_event_days']}` days",
        f"- Snapshot files deleted: `{deleted_files}`",
        f"- Snapshot file bytes deleted: `{deleted_bytes}`",
        f"- Snapshot DB rows deleted: `{db_result['snapshotsDeleted']}`",
        f"- Home event rows deleted: `{db_result['eventsDeleted']}`",
        f"- Snapshot raw payload rows compacted: `{db_result['snapshotRawRowsCompacted']}`",
        f"- DB size before: `{db_result['dbBytesBefore']}` bytes",
        f"- DB size after: `{db_result['dbBytesAfter']}` bytes",
    ]
    (REPORT_DIR / "maintenance.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    config = load_config()
    file_result = prune_snapshot_files(int(config["retention"]["snapshot_files_days"]))
    db_result = compact_db(config)
    write_report(file_result, db_result, config)
    print(REPORT_DIR / "maintenance.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
