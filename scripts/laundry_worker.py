"""Independent appliance capture; never wait for energy providers or speakers."""
import fcntl
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    if ROOT != Path.home()/'Library/Application Support/SmartHomeMonitor':
        raise SystemExit('Laundry worker requires deployed runtime')
    import refresh_energy as refresh
    data = ROOT/'data'
    with (data/'laundry_worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        config = json.loads((ROOT/'config/sources.json').read_text())
        if not config.get('laundry_worker', {}).get('enabled'):
            return
        py = refresh.python_path()
        steps = [refresh.run_step('capture', refresh.laundry_capture_command(), timeout=170)]
        if steps[0]['ok']:
            for name in ('washer', 'dryer', 'combo'):
                steps.append(refresh.run_step(name, [py, 'scripts/washer_notifier.py', '--appliance', name, '--queue-only'], timeout=15))
        steps.append(refresh.run_step('recovery', [py, 'scripts/recover_smarthq_laundry.py'], timeout=90, optional=True))
        result = {'generatedAt':datetime.now(timezone.utc).isoformat(), 'ok':all(s['ok'] for s in steps), 'steps':steps}
        path = data/'latest_laundry_worker.json'
        temp = path.with_suffix('.tmp');temp.write_text(json.dumps(result,indent=2)+'\n');temp.chmod(0o600);temp.replace(path)


if __name__ == '__main__':
    main()
