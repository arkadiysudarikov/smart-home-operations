"""Durable five-minute reminders, stopped by explicit household acknowledgement."""
import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INTERVAL = 300
ACTIONABLE = {'washer_tank_low', 'washer', 'dryer', 'combo', 'energy_high_on', 'energy_ok_off',
              'household_reminder', 'laundry_health', 'freezer_temperature',
              'doorbell_press', 'help_request', 'energy_low'}


def clock():
    return datetime.now(timezone.utc).timestamp()


@contextmanager
def state(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'announcement_ack.json'
    with (directory / 'announcement_ack.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            value = {'pending': {}}
        yield value
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.chmod(0o600)
        temporary.replace(path)


def record(directory, event, now=None):
    identifier = event['announcementId']
    if identifier not in ACTIONABLE | {'energy_high_clear'} and not identifier.startswith('calendar-'):
        return
    if event.get('ok') is not True or event.get('skipped'):
        return
    from announcement_envelope import encode
    encode(event['message'])
    now = clock() if now is None else now
    with state(directory) as value:
        pending = value.setdefault('pending', {})
        if (identifier == 'laundry_health' and 'healthy again' in event['message']
                or identifier == 'freezer_temperature' and 'back below' in event['message']):
            pending.pop(identifier, None)
            return
        if identifier == 'energy_high_clear':
            pending.pop('energy_high_on', None)
            pending.pop('energy_ok_off', None)
            return
        # A newer source announcement replaces its previous reminder. Duplicates
        # do not postpone the repeat indefinitely.
        previous = pending.get(identifier)
        if previous and previous['event']['message'] == event['message']:
            return
        pending[identifier] = {'event': event, 'dueAt': now + INTERVAL}


def acknowledge(directory):
    with state(directory) as value:
        count = len(value.get('pending', {}))
        value['pending'] = {}
        value['acknowledgedAt'] = clock()
        # Cancel the old one-shot snooze too, so acknowledgement really stops it.
        with (directory / 'announcement_followup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (directory / 'announcement_followup.json').write_text('{}')
    return count


def tick(directory, now=None):
    from announcement_followup import active, can_disclose
    from generate_alerts import run_indoor_homepod_announcement
    now = clock() if now is None else now
    attempted = 0
    # Serialize delivery with acknowledgements. Replays have a non-actionable ID
    # and cannot recursively re-enqueue themselves.
    with state(directory) as value:
        pending = value.setdefault('pending', {})
        for identifier, item in list(pending.items()):
            event = item['event']
            if identifier.startswith('calendar-') and not can_disclose(event, now):
                # Keep private while away, but discard ended/cancelled events.
                # The privacy gate deliberately fails closed without disclosing.
                continue
            if now < item['dueAt'] or active(identifier):
                continue
            if not 8 <= datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).hour < 21:
                continue
            item['dueAt'] = now + INTERVAL
            # Persist before relay, including uncertain outcomes and restarts.
            path = directory / 'announcement_ack.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value))
            temporary.chmod(0o600)
            temporary.replace(path)
            try:
                run_indoor_homepod_announcement('Unacknowledged announcement: ' + event['message'], 'dr_house_unacknowledged')
            except Exception:
                pass  # Retry at the next five-minute interval, never a tight loop.
            attempted += 1
    return attempted
