"""Explicit dog-presence choice for departure; unknown always selects Stay."""
import json
from urllib.request import urlopen

FLAG_URL = 'http://127.0.0.1:63743/?id=smart_home_dogs_with_us_v1&get=On'


def selected_mode(read):
    try:
        value = read()
    except (OSError, ValueError, TimeoutError):
        value = None
    return 'away' if value is True else 'stay'


def read_flag():
    with urlopen(FLAG_URL, timeout=5) as response:
        return json.load(response).get('value')


def depart(arm, read=read_flag):
    mode = selected_mode(read)
    result = arm(mode)
    return {**result, 'selectedMode': mode, 'dogsWithUs': mode == 'away'}
