"""Durable reminders with capped progressive backoff and acknowledgement."""
import fcntl
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INTERVAL = 300
MAX_INTERVAL = 3600


def repeat_interval(count):
    """Initial reminder at five minutes, then 10, 20, 40, and 60 minutes."""
    return min(MAX_INTERVAL, INTERVAL * 2 ** min(max(int(count), 0), 4))
ACTIONABLE = {'washer', 'dryer', 'combo', 'energy_high_on', 'energy_ok_off',
              'laundry_health', 'freezer_temperature',
              'doorbell_press', 'help_request', 'energy_low', 'detergent_tank_low'}


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
        started = event.get('attemptStartedAt')
        if started is not None:
            try:
                began = datetime.fromisoformat(started.replace('Z','+00:00')).timestamp()
                cleared = max(value.get('acknowledgedAt', 0), value.get('applianceAcknowledgedAt', {}).get(identifier, 0))
                if cleared >= began:
                    return
            except (ValueError, TypeError, AttributeError):
                return
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
        pending[identifier] = {'event': event, 'dueAt': now + INTERVAL, 'repeatCount': 0}


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


def acknowledge_appliance(directory, identifier):
    """Opening one appliance clears only its reminders and matching snooze."""
    if identifier not in {'washer', 'dryer', 'combo'}:
        raise ValueError('Unknown laundry appliance')
    return clear_source(directory, identifier)


def clear_source(directory, identifier):
    """Resolve one observed source without clearing unrelated reminders."""
    if identifier not in ACTIONABLE:
        raise ValueError('Unknown announcement source')
    with state(directory) as value:
        removed = value.setdefault('pending', {}).pop(identifier, None) is not None
        value.setdefault('applianceAcknowledgedAt', {})[identifier] = clock()
        with (directory / 'announcement_followup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = directory / 'announcement_followup.json'
            try:
                snooze = json.loads(path.read_text())
            except (OSError, ValueError):
                snooze = {}
            if snooze.get('event', {}).get('announcementId') == identifier:
                path.write_text('{}')
    return removed


def tick(directory, now=None):
    from announcement_followup import active, calendar_disposition
    from generate_alerts import run_indoor_homepod_announcement
    now = clock() if now is None else now
    attempted = 0
    claims = []
    # Claim and persist before any external work. Never hold the queue lock
    # while checking Calendar, UniFi or a speaker relay.
    with state(directory) as value:
        pending = value.setdefault('pending', {})
        for identifier, item in list(pending.items()):
            # Legacy combined notices have no sensor identity or live-condition
            # proof. Never replay them after the source episode has changed.
            if identifier == 'household_reminder':
                pending.pop(identifier)
                continue
            event = item['event']
            if now < item['dueAt'] or active(identifier):
                continue
            if not 8 <= datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).hour < 21:
                continue
            # Suppression checks do not consume a repeat. Reserve a short retry
            # while external checks run; the actual attempt advances backoff.
            item['dueAt'] = now + INTERVAL
            token = item['claim'] = uuid.uuid4().hex
            claims.append((identifier, token, event))
    for identifier, token, event in claims:
        disposition = calendar_disposition(event, now)
        with state(directory) as value:
            item = value.get('pending', {}).get(identifier)
            if not item or item.get('claim') != token:
                continue  # Cleared or replaced while the external check ran.
            if disposition == 'expired':
                value['pending'].pop(identifier)
                continue
            if disposition != 'allowed':
                continue
            item['repeatCount'] = int(item.get('repeatCount', 0)) + 1
            item['dueAt'] = now + repeat_interval(item['repeatCount'])
        try:
            run_indoor_homepod_announcement('Unacknowledged announcement: ' + event['message'], 'dr_house_unacknowledged')
        except Exception:
            pass  # An uncertain delivery also backs off; never a tight loop.
        attempted += 1
    return attempted
