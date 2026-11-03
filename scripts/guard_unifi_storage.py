#!/usr/bin/env python3
"""Bound UniFi desktop WAL growth without deleting database transactions."""
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

GIB = 1024 ** 3

def needs_checkpoint(wal_bytes, free_bytes):
    return wal_bytes >= GIB or (wal_bytes >= 128 * 1024 ** 2 and free_bytes < 5 * GIB)


def checkpoint(path):
    with sqlite3.connect(str(path), timeout=5) as db:
        result = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if result[0] != 0:
        raise RuntimeError('UniFi checkpoint is busy; database was preserved')
    return list(result)


def main():
    home = Path.home()
    db = home / 'Library/Group Containers/group.com.ubnt.unifinetwork/unifi.apple.network.UniFiNetworkData.resources/DataModel.sqlite'
    wal = Path(str(db) + '-wal')
    root = home / 'Library/Application Support/SmartHomeMonitor'
    status = {'at': datetime.now().astimezone().isoformat(), 'ok': True,
              'action': 'none', 'freeBytes': shutil.disk_usage(home).free,
              'walBytes': wal.stat().st_size if wal.exists() else 0}
    try:
        if db.is_file() and needs_checkpoint(status['walBytes'], status['freeBytes']):
            # Close only the optional desktop client holding old read transactions.
            # Never stop UniFi OS Server or Homebridge's occupancy integration.
            running = subprocess.run(['/usr/bin/pgrep', '-f', '/UniFi.app/'], capture_output=True).returncode == 0
            if running:
                subprocess.run(['/usr/bin/osascript', '-e', 'tell application "UniFi" to quit'],
                               check=True, timeout=15, capture_output=True)
            status['checkpoint'] = checkpoint(db)
            status['action'] = 'checkpointed'
            status['walBytesAfter'] = wal.stat().st_size if wal.exists() else 0
        status['freeBytesAfter'] = shutil.disk_usage(home).free
        if status['freeBytesAfter'] < 5 * GIB:
            status.update(ok=False, error='M4 has less than 5 GiB free')
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        status.update(ok=False, error=str(exc))
    destination = root / 'data/latest_unifi_storage_guard.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(status, indent=2) + '\n')
    temporary.replace(destination)
    print(json.dumps(status))
    return 0 if status['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
