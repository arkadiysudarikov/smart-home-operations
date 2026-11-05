"""Explicit per-appliance unload acknowledgement, sharing notifier state locks."""
from datetime import datetime, timezone
from laundry_delivery import transaction, enqueue, process


def acknowledge_unloaded(root, appliance):
    if appliance not in {'washer', 'dryer', 'combo'}:
        raise ValueError('Unknown laundry appliance')
    import washer_notifier as notifier
    from announcement_ack import acknowledge_appliance
    data = root / 'data'
    now = datetime.now(timezone.utc).isoformat()
    with transaction(data, appliance) as state:
        if (not state.get('awaitingUnload') and not state.get('reminderLatched')
                and state.get('unloadedBy') != 'user'):
            return {'ok': True, 'appliance': appliance, 'alreadyClear': True,
                    'message': f'{appliance.title()} has no pending unload reminder.'}
        state.update(awaitingUnload=False, reminderLatched=False,
                     reminderPulseUntil=None, finishPulseUntil=None,
                     unloadedAt=now, unloadedBy='user')
        acknowledge_appliance(data, appliance)
        enqueue(state, ['finish_off', 'reminder_off'], now)
    full = notifier.load_json(root / 'config/sources.json', {})
    config = {'id': appliance, **full.get(f'{appliance}_notifications', {})}
    results = process(data, appliance, config, notifier.execute_actions)
    ok = all(item.get('ok') for item in results)
    return {'ok': ok, 'appliance': appliance, 'unloadedAt': now,
            'message': f'{appliance.title()} unload acknowledged.' if ok else
                       'Unload recorded, but the Home tile update needs attention.',
            'results': results}
