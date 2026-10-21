"""Resolve laundry reminders from fresh appliance evidence, including brief door opens."""
import fcntl
import json
from datetime import datetime
from announcement_ack import state as acknowledgement_state


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None


def clear(directory, identifier, observed_at):
    """Never clear an announcement newer than the evidence acknowledging it."""
    observed = timestamp(observed_at)
    if observed is None:
        return False
    removed = False
    with acknowledgement_state(directory) as value:
        pending = value.setdefault('pending', {})
        item = pending.get(identifier)
        event_at = timestamp((item or {}).get('event', {}).get('at'))
        if item and event_at is not None and event_at <= observed:
            pending.pop(identifier)
            removed = True
        with (directory / 'announcement_followup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = directory / 'announcement_followup.json'
            try:
                snooze = json.loads(path.read_text())
            except (OSError, ValueError):
                snooze = {}
            event = snooze.get('event') or {}
            at = timestamp(event.get('at'))
            if event.get('announcementId') == identifier and at is not None and at <= observed:
                temporary = path.with_suffix('.tmp')
                temporary.write_text('{}')
                temporary.replace(path)
                removed = True
    return removed


def reconcile(prior, current, updated, actions, now, config):
    updated = dict(updated)
    actions = list(actions)
    if current.get('fresh') is not True:
        return updated, actions, None
    appliance = config.get('id', 'washer')
    now_text = now.isoformat(timespec='seconds')
    opened = timestamp(current.get('doorLastOpenedAt'))
    finished = timestamp(updated.get('washFinishedAt') or updated.get('finishedAt'))
    opened_after_finish = bool(opened is not None and finished is not None and finished <= opened <= now.timestamp())
    unloaded = current.get('doorOpen') is True or opened_after_finish
    new_cycle = (updated.get('lastCycleActive') is True and prior.get('lastCycleActive') is False
                 or current.get('inUse') is True and prior.get('lastInUse') is False)
    clear_at = now_text if unloaded or new_cycle else None
    if unloaded:
        updated.update(awaitingUnload=False, unloadedAt=now_text, reminderSent=False,
                       finishPulseUntil=None, reminderPulseUntil=None)
        actions = [a for a in actions if a not in ('finish_on', 'reminder_on', 'notify_reminder', 'announce_finish')]
        actions.extend(['finish_off', 'reminder_off'])
    if appliance == 'washer':
        low = current.get('detergentTankLow')
        if low is True and not prior.get('detergentTankLowActive'):
            updated['detergentTankLowActive'] = True
            updated['detergentTankLowAnnounced'] = False
            actions.append('notify_tank_low')
        if low is True and not updated.get('detergentTankLowAnnounced'):
            start, end = config.get('announcement_start_hour', 8), config.get('announcement_end_hour', 21)
            if (start <= now.hour < end if start <= end else now.hour >= start or now.hour < end):
                actions.append('announce_tank_low')
                updated['detergentTankLowAnnounced'] = True
        elif low is False:
            updated['detergentTankLowActive'] = False
            updated['detergentTankLowAnnounced'] = False
    if clear_at:
        updated.pop('notificationRetry', None)
        updated['notificationDeliveryStatus'] = 'cancelled'
    return updated, list(dict.fromkeys(actions)), clear_at

DELIVERY_ACTIONS = {'notify_finish', 'announce_finish', 'notify_reminder', 'notify_tank_low', 'announce_tank_low'}


def retry_actions(prior, current, updated, actions, now, cleared=False):
    retry = prior.get('notificationRetry') or {}
    created = timestamp(retry.get('createdAt'))
    started = timestamp(updated.get('washStartedAt') or updated.get('cycleStartedAt'))
    if (current.get('fresh') and not cleared and created is not None
            and 0 <= now.timestamp()-created <= 900 and retry.get('attempts', 0) < 3
            and not (started is not None and started > created)):
        pending = retry.get('actions', [])
        if current.get('detergentTankLow') is False:
            pending = [a for a in pending if a not in ('notify_tank_low','announce_tank_low')]
        if not 8 <= now.hour < 21:
            pending = [a for a in pending if not a.startswith('announce_')]
        actions = list(dict.fromkeys([*actions, *pending]))
    return actions


def remember_delivery(prior, updated, actions, results, now):
    attempted = set(actions) & DELIVERY_ACTIONS
    if not attempted:
        return updated
    failed = [r['action'] for r in results if r.get('action') in attempted and not r.get('ok')]
    updated = dict(updated)
    old = prior.get('notificationRetry') or {}
    if failed:
        retrying = bool(set(old.get('actions', [])) & set(failed))
        attempts = int(old.get('attempts', 0)) + 1 if retrying else 1
        updated['notificationRetry'] = {'actions': failed, 'attempts': attempts,
                                        'createdAt': old.get('createdAt') if retrying else now.isoformat(timespec='seconds')}
        updated['notificationDeliveryStatus'] = 'failed' if attempts >= 3 else 'retry_pending'
    else:
        updated.pop('notificationRetry', None)
        updated['notificationDeliveryStatus'] = 'accepted'
    return updated
