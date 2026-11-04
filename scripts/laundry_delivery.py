"""Durable laundry delivery jobs; external work never holds appliance state locks."""
import argparse
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def transaction(data, appliance):
    data.mkdir(parents=True, exist_ok=True)
    path = data / f'{appliance}_notifier_state.json'
    with (data / f'{appliance}_notifier.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
        yield state
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(state, indent=2, sort_keys=True)+'\n')
        temp.chmod(0o600)
        temp.replace(path)


def enqueue(state, actions, now):
    jobs = state.setdefault('deliveryOutbox', [])
    # Keep failures/uncertain attempts visible; bound completed history.
    terminal = {'done', 'failed', 'uncertain', 'cancelled'}
    done = [j for j in jobs if j['status'] in terminal][-40:]
    jobs[:] = [j for j in jobs if j['status'] not in terminal] + done
    for action in actions:
        # A reset/start supersedes an old undelivered pulse for this sensor.
        if action.endswith('_off'):
            opposite = action[:-4]+'_on'
            superseded = {opposite}
            if action == 'finish_off':
                superseded |= {'notify_finish', 'announce_finish'}
            if action == 'reminder_off':
                superseded |= {'notify_reminder'}
            jobs[:] = [j for j in jobs if not (j['status'] == 'pending' and j['action'] in superseded)]
        jobs.append({'id': uuid.uuid4().hex, 'action': action, 'status': 'pending', 'createdAt': now})


def process(data, appliance, config, execute):
    results = []
    with transaction(data, appliance) as state:
        ids = [j['id'] for j in state.get('deliveryOutbox', []) if j['status'] == 'pending']
        for job in state.get('deliveryOutbox', []):
            if job['status'] == 'attempting':
                try:
                    os.kill(job['pid'], 0)
                except (ProcessLookupError, KeyError):
                    job['status'] = 'uncertain'
                except PermissionError:
                    pass
    for identifier in ids:
        with transaction(data, appliance) as state:
            job = next((j for j in state.get('deliveryOutbox', []) if j['id'] == identifier), None)
            if not job or job['status'] != 'pending':
                continue
            if job['action'].startswith('announce_'):
                try:
                    acknowledged = json.loads((data/'announcement_ack.json').read_text())
                    cleared = max(acknowledged.get('acknowledgedAt', 0), acknowledged.get('applianceAcknowledgedAt', {}).get(appliance, 0))
                    began = datetime.fromisoformat(job['createdAt'].replace('Z','+00:00')).timestamp()
                    if cleared >= began:
                        job['status'] = 'cancelled'
                        continue
                except (OSError, ValueError):
                    pass
            job.update(status='attempting', pid=os.getpid())
            action = job['action']
            created = job['createdAt']
        # Persist the claim before invoking a possibly uncertain transport.
        try:
            response = execute([action], {**config, '_announcement_created_at':created}, False)
            result = response[0] if response else {'action': action, 'ok': True, 'skipped': True}
        except Exception as error:
            result = {'action': action, 'ok': False, 'errorType': type(error).__name__, 'uncertain': True}
        with transaction(data, appliance) as state:
            job = next((j for j in state.get('deliveryOutbox', []) if j['id'] == identifier), None)
            if job:
                job.update(status='done' if result.get('ok') else 'uncertain' if result.get('uncertain') else 'failed',
                           result=result, finishedAt=datetime.now(timezone.utc).isoformat())
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    if ROOT != Path.home()/'Library/Application Support/SmartHomeMonitor':
        raise SystemExit('Delivery requires deployed runtime')
    import washer_notifier as notifier
    full = notifier.load_json(ROOT/'config/sources.json', {})
    with (ROOT/'data/laundry_delivery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        for name in ('washer', 'dryer', 'combo'):
            config = {'id':name, 'display_name':name.title(), **full.get('homepod_announcements', {}),
                      **full.get(f'{name}_notifications', {})}
            if config.get('enabled'):
                process(ROOT/'data', name, config, notifier.execute_actions)


if __name__ == '__main__':
    main()
