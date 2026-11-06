"""Durably record native Dummy switch commands; never operate a light or door."""
import argparse
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def record(data, occupied, now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    data.mkdir(parents=True, exist_ok=True)
    with (data/'garage_camera_edges.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = data/'garage_camera_edges.json'
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
        state.update(occupied=occupied, lastActivityAt=now)
        state['lastMotionAt' if occupied else 'lastClearAt'] = now
        temp = path.with_suffix('.tmp');temp.write_text(json.dumps(state)+'\n');temp.chmod(0o600);temp.replace(path)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('state', choices=['on', 'off'])
    args = parser.parse_args()
    if ROOT != Path.home()/'Library/Application Support/SmartHomeMonitor':
        raise SystemExit('Camera events require deployed runtime')
    record(ROOT/'data', args.state == 'on')
