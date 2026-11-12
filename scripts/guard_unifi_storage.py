#!/usr/bin/env python3
"""Guard local storage and recover the Alarm.com bridge after a disk-full startup."""
import http.client
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from additional_announcements import load, save

GIB = 1024 ** 3
MIN_FREE_BYTES = 12 * GIB
ALARM_PROCESS = 'homebridge: homebridge-node-alarm-dot-com'


def alarm_restart_has_headroom(homebridge):
    return shutil.disk_usage(homebridge).free >= MIN_FREE_BYTES


def failed_alarm_publication(log_path, bridge_id):
    pattern = re.compile(
        r'^\[([^\]]+)\].*Failed to publish bridge accessory.*ENOSPC.*'
        rf'\.AccessoryInfo\.{re.escape(bridge_id)}\.json\.(\d+)\.tmp'
    )
    with log_path.open('rb') as log:
        log.seek(max(0, log.seek(0, os.SEEK_END) - 16 * 1024 ** 2))
        lines = log.read().decode('utf-8', errors='replace').splitlines()
    for line in reversed(lines):
        match = pattern.search(re.sub(r'\x1b\[[0-9;]*m', '', line))
        if match:
            failed_at = datetime.strptime(match[1], '%m/%d/%Y, %I:%M:%S %p')
            return int(match[2]), failed_at
    return None


def process_record(pid):
    result = subprocess.run(
        ['/bin/ps', '-p', str(pid), '-o', 'uid=,ppid=,lstart=,comm='],
        capture_output=True, text=True, timeout=3,
        env={**os.environ, 'LC_ALL': 'C'},
    )
    if result.returncode != 0:
        return None
    uid, parent, weekday, month, day, clock, year, command = result.stdout.split(maxsplit=7)
    started = datetime.strptime(f'{weekday} {month} {day} {clock} {year}', '%a %b %d %H:%M:%S %Y')
    return int(uid), int(parent), started, command.strip()


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def hap_responds(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/accessories')
        return connection.getresponse().status in (200, 470)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def recover_alarm_bridge(homebridge, attempt_path):
    config = load(homebridge / 'config.json')
    bridges = [
        row.get('_bridge', {}) for row in config.get('platforms', [])
        if row.get('platform') == 'Alarmdotcom' and row.get('disabled') is not True
    ]
    if len(bridges) != 1:
        return 'not_configured'
    port = bridges[0].get('port')
    bridge_id = str(bridges[0].get('username', '')).replace(':', '').upper()
    if type(port) is not int or not 1 <= port <= 65535 or not re.fullmatch(r'[0-9A-F]{12}', bridge_id):
        return 'invalid_configuration'
    if listening(port):
        return 'listening'
    if not alarm_restart_has_headroom(homebridge):
        return 'waiting_for_disk_space'

    failure = failed_alarm_publication(homebridge / 'homebridge.log', bridge_id)
    if failure is None:
        return 'no_disk_full_publication_error'
    pid, failed_at = failure
    child = process_record(pid)
    if child is None or child[0] != os.getuid() or child[3] != ALARM_PROCESS:
        return 'failed_process_gone'
    parent = process_record(child[1])
    if parent is None or parent[0] != child[0] or parent[3] != 'homebridge':
        return 'failed_process_not_supervised'
    if not 0 <= (failed_at - child[2]).total_seconds() <= 120:
        return 'publication_error_from_different_process'

    previous = load(attempt_path)
    failed_stamp = failed_at.isoformat()
    if previous.get('failedAt') == failed_stamp or time.time() - previous.get('attemptedAt', 0) < 3600:
        return 'recovery_already_attempted'
    save(attempt_path, {'failedAt': failed_stamp, 'attemptedAt': time.time(), 'pid': pid})
    if process_record(pid) != child or listening(port) or not alarm_restart_has_headroom(homebridge):
        return 'state_changed_before_restart'

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if hap_responds(port):
            return 'recovered'
        time.sleep(1)
    return 'restart_did_not_publish'

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
        if status['freeBytesAfter'] < MIN_FREE_BYTES:
            status.update(ok=False, error='M4 has less than 12 GiB free')
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        status.update(ok=False, error=str(exc))
    try:
        status['alarmBridge'] = recover_alarm_bridge(
            home / '.homebridge', root / 'data/alarm_bridge_storage_recovery.json',
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        status.update(ok=False, alarmBridge='recovery_check_failed')
    if status.get('alarmBridge') == 'restart_did_not_publish':
        status['ok'] = False
    destination = root / 'data/latest_unifi_storage_guard.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(status, indent=2) + '\n')
    temporary.replace(destination)
    print(json.dumps(status))
    return 0 if status['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
