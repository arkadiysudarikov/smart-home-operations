"""Read the Home camera-motion bridge without treating phone Wi-Fi as occupancy."""
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path


def read(config):
    camera = config.get('camera_motion') or {}
    if not camera.get('enabled'):
        return None
    url = camera.get('url', 'http://127.0.0.1:63743/?id=smart_home_garage_camera_motion_v1&get=On')
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            value = json.load(response).get('value')
        if not isinstance(value, bool):
            raise ValueError('camera motion state is unavailable')
        result = {'enabled': True, 'source': 'home-camera-motion', 'occupied': value}
        try:
            edges = json.loads((Path(__file__).resolve().parents[1]/'data/garage_camera_edges.json').read_text())
            result['lastActivityAt'] = edges.get('lastActivityAt')
        except (OSError, ValueError):
            pass
        return result
    except (OSError, ValueError) as exc:
        return {'enabled': True, 'source': 'home-camera-motion', 'occupied': None, 'error': str(exc)}


def guard(controller, state, now):
    """Keep an active camera signal lit; allow five minutes after it clears."""
    occupancy = read(controller.garage_light_config())
    if occupancy is None:
        return False
    state['occupancy'] = occupancy
    if occupancy['occupied'] is not None:
        state.pop('retryAt', None)
    if occupancy['occupied'] is True:
        state.update(lastActivityAt=now.isoformat(timespec='seconds'), cameraWasOccupied=True,
                     status='camera-occupied', holdUntil=(now+timedelta(seconds=state.get('holdSeconds',300))).isoformat(timespec='seconds'))
    elif occupancy['occupied'] is None:
        state.update(status='camera-unavailable', retryAt=(now+timedelta(seconds=60)).isoformat(timespec='seconds'),lastError=occupancy['error'])
    elif state.pop('cameraWasOccupied',False):
        state.update(lastActivityAt=now.isoformat(timespec='seconds'),status='camera-clear-countdown',
                     holdUntil=(now+timedelta(seconds=state.get('holdSeconds',300))).isoformat(timespec='seconds'))
    else:
        try:
            edge = datetime.fromisoformat(occupancy.get('lastActivityAt', '').replace('Z', '+00:00'))
            prior = datetime.fromisoformat(state['lastActivityAt'].replace('Z', '+00:00'))
            if not edge.tzinfo or not 0 <= (now-edge).total_seconds() < state.get('holdSeconds',300) or edge <= prior:
                return False
        except (KeyError, ValueError, TypeError, AttributeError):
            return False
        state.update(lastActivityAt=edge.isoformat(), status='camera-edge-countdown',
                     holdUntil=(edge+timedelta(seconds=state.get('holdSeconds',300))).isoformat())
    controller.write_garage_light_hold_state(state)
    controller.schedule_garage_light_hold_check(state)
    return True
