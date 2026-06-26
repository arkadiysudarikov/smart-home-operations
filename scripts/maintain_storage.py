#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SCE_DOWNLOAD_DIR = DATA_DIR / "sce-downloads"
DB_PATH = DATA_DIR / "smart_home.sqlite"
REPORT_DIR = ROOT / "reports"
SNAPSHOT_NAME_RE = re.compile(r"^(\d{8})T(\d{6})([+-]\d{4}|Z)\.json$")
SCE_DOWNLOAD_NAME_RE = re.compile(
    r"^(?:UtilityAPI_intervals|SCE_Usage_(?:UtilityAPI|GBC))_(\d{8})T(\d{6})([+-]\d{4}|Z)\.(?:json|csv|xml)$",
    re.I,
)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc).astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


def snapshot_file_datetime(path: Path) -> datetime | None:
    return datetime_from_filename(path, SNAPSHOT_NAME_RE)


def sce_download_datetime(path: Path) -> datetime | None:
    return datetime_from_filename(path, SCE_DOWNLOAD_NAME_RE)


def datetime_from_filename(path: Path, pattern: re.Pattern[str]) -> datetime | None:
    match = pattern.match(path.name)
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
    return prune_files_by_age(SNAPSHOT_DIR, "*.json", days, snapshot_file_datetime)


def prune_sce_downloads(days: int, keep_recent_pairs: int) -> tuple[int, int]:
    if not SCE_DOWNLOAD_DIR.exists():
        return (0, 0)
    candidates = [
        path
        for path in SCE_DOWNLOAD_DIR.iterdir()
        if path.is_file() and SCE_DOWNLOAD_NAME_RE.match(path.name)
    ]
    recent_keep = {
        path
        for path in sorted(
            candidates,
            key=lambda item: sce_download_datetime(item)
            or datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc).astimezone(),
            reverse=True,
        )[: max(0, keep_recent_pairs * 2)]
    }
    return prune_files_by_age(SCE_DOWNLOAD_DIR, "*", days, sce_download_datetime, recent_keep)


def prune_files_by_age(
    directory: Path,
    glob_pattern: str,
    days: int,
    parse_datetime: Callable[[Path], datetime | None],
    keep_paths: set[Path] | None = None,
) -> tuple[int, int]:
    if not directory.exists():
        return (0, 0)
    keep_paths = keep_paths or set()
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=days)
    deleted = 0
    bytes_deleted = 0
    for path in directory.glob(glob_pattern):
        if path in keep_paths or not path.is_file():
            continue
        try:
            captured_at = parse_datetime(path)
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


def write_report(
    file_result: tuple[int, int],
    sce_result: tuple[int, int],
    db_result: dict,
    config: dict,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    deleted_files, deleted_bytes = file_result
    sce_deleted_files, sce_deleted_bytes = sce_result
    lines = [
        "# Smart Home Storage Maintenance",
        "",
        f"- Generated: `{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}`",
        f"- Snapshot file retention: `{config['retention']['snapshot_files_days']}` days",
        f"- SCE download retention: `{config['retention'].get('sce_download_files_days', config['retention']['snapshot_files_days'])}` days",
        f"- Snapshot DB retention: `{config['retention']['snapshot_db_days']}` days",
        f"- Home event DB retention: `{config['retention']['home_event_days']}` days",
        f"- Snapshot files deleted: `{deleted_files}`",
        f"- Snapshot file bytes deleted: `{deleted_bytes}`",
        f"- SCE download files deleted: `{sce_deleted_files}`",
        f"- SCE download file bytes deleted: `{sce_deleted_bytes}`",
        f"- Snapshot DB rows deleted: `{db_result['snapshotsDeleted']}`",
        f"- Home event rows deleted: `{db_result['eventsDeleted']}`",
        f"- Snapshot raw payload rows compacted: `{db_result['snapshotRawRowsCompacted']}`",
        f"- DB size before: `{db_result['dbBytesBefore']}` bytes",
        f"- DB size after: `{db_result['dbBytesAfter']}` bytes",
    ]
    (REPORT_DIR / "maintenance.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    config = load_config()
    retention = config["retention"]
    file_result = prune_snapshot_files(int(retention["snapshot_files_days"]))
    sce_result = prune_sce_downloads(
        int(retention.get("sce_download_files_days", retention["snapshot_files_days"])),
        int(retention.get("sce_download_keep_recent_pairs", 12)),
    )
    db_result = compact_db(config)
    write_report(file_result, sce_result, db_result, config)
    print(REPORT_DIR / "maintenance.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
