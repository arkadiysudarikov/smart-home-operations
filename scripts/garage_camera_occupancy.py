"""Read the Home camera-motion bridge without treating phone Wi-Fi as occupancy."""
import json
import urllib.request
from datetime import timedelta


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
        return {'enabled': True, 'source': 'home-camera-motion', 'occupied': value}
    except (OSError, ValueError) as exc:
        return {'enabled': True, 'source': 'home-camera-motion', 'occupied': None, 'error': str(exc)}


def guard(controller, state, now):
    """Keep an active camera signal lit; allow five minutes after it clears."""
    occupancy = read(controller.garage_light_config())
    if occupancy is None:
        return False
    state['occupancy'] = occupancy
    if occupancy['occupied'] is True:
        state.update(lastActivityAt=now.isoformat(timespec='seconds'), cameraWasOccupied=True,
                     status='camera-occupied', holdUntil=(now+timedelta(seconds=state.get('holdSeconds',300))).isoformat(timespec='seconds'))
    elif occupancy['occupied'] is None:
        state.update(status='camera-unavailable', retryAt=(now+timedelta(seconds=60)).isoformat(timespec='seconds'),lastError=occupancy['error'])
    elif state.pop('cameraWasOccupied',False):
        state.update(lastActivityAt=now.isoformat(timespec='seconds'),status='camera-clear-countdown',
                     holdUntil=(now+timedelta(seconds=state.get('holdSeconds',300))).isoformat(timespec='seconds'))
    else:
        return False
    controller.write_garage_light_hold_state(state)
    controller.schedule_garage_light_hold_check(state)
    return True
