"""Adopt a direct Alarm.com light-on into the existing five-minute timer."""
import json
import fcntl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def should_check(hold, previous, edges, now):
    if hold.get('active') or now-previous.get('checkedAt',0) >= 120:
        return True
    try:
        edge = datetime.fromisoformat(edges['lastActivityAt'].replace('Z','+00:00')).timestamp()
        return 0 <= now-edge <= 300 and edge > previous.get('checkedAt',0)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def needs_timer(snapshot, hold, now):
    if hold.get('active'):
        return False
    try:
        at = datetime.fromisoformat(snapshot['captured_at'].replace('Z', '+00:00')).timestamp()
        if not 0 <= now - at <= 300:
            return False
    except (KeyError, ValueError, TypeError):
        return False
    values = snapshot.get('homeEvents', {}).get('currentCharacteristics', {})
    light = {v.get('characteristic'): v.get('value') for v in values.values()
             if v.get('accessory') == 'Garage Light' and v.get('service') == 'Garage Light'}
    return light.get('On') is True and light.get('Brightness') == 100


def adopt(controller):
    with controller.GARAGE_LIGHT_HOLD_LOCK:
        existing = controller.read_garage_light_hold_state()
        if existing.get('active'):
            return {'ok': True, 'adopted': False, 'reason': 'timer already active'}
        current = controller.garage_light_status()
        if not current.get('ok'):
            return {'ok': False, 'error': 'could not verify garage light state'}
        light = current.get('light') or {}
        if light.get('on') is not True or light.get('brightness') != 100:
            return {'ok': True, 'adopted': False, 'reason': 'light is off or manually dimmed'}
        now = controller.local_now()
        seconds = controller.garage_light_hold_seconds()
        from datetime import timedelta
        state = {**existing, 'active': True, 'status': 'holding',
                 'lastActivityAt': now.isoformat(timespec='seconds'),
                 'lastPhysicalActivityAt': now.isoformat(timespec='seconds'),
                 'holdUntil': (now + timedelta(seconds=seconds)).isoformat(timespec='seconds'),
                 'holdSeconds': seconds, 'startedState': light, 'lightId': controller.GARAGE_LIGHT_ID,
                 'lastTrigger': 'direct light-on recovered by monitor', 'lastSource': 'monitor',
                 'lastError': None}
        state.pop('retryAt', None)
        controller.write_garage_light_hold_state(state)
        controller.append_garage_activity_event({'type': 'adopt-direct-on', 'holdUntil': state['holdUntil']})
        controller.schedule_garage_light_hold_check(state)
        return {'ok': True, 'adopted': True, 'holdUntil': state['holdUntil']}


def main():
    with (ROOT/'data/garage_reconcile.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        return reconcile()


def reconcile():
    try:
        hold = json.loads((ROOT / 'data/garage_light_hold.json').read_text())
    except (OSError, ValueError):
        hold = {}
    config = json.loads((ROOT / 'config/sources.json').read_text())
    if hold.get('active') and not config.get('garage_light', {}).get('camera_motion', {}).get('enabled'):
        print(json.dumps({'ok': True, 'adopted': False}))
        return
    status = ROOT/'data/latest_garage_reconcile.json'
    def read(path):
        try:return json.loads(path.read_text())
        except (OSError,ValueError):return {}
    now=datetime.now(timezone.utc).timestamp()
    if not should_check(hold,read(status),read(ROOT/'data/garage_camera_edges.json'),now):
        return
    # Throttle idle provider logins independently of the energy monitor.
    temp=status.with_suffix('.tmp');temp.write_text(json.dumps({'checkedAt':now})+'\n');temp.chmod(0o600);temp.replace(status)
    port = config['actions']['port']
    request = urllib.request.Request(f'http://127.0.0.1:{port}/action/garage-reconcile', method='POST')
    with urllib.request.urlopen(request, timeout=90) as response:
        print(response.read().decode())


if __name__ == '__main__':
    main()
