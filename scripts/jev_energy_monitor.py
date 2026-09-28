"""Scheduled, budgeted Jev energy advice and conservative low-energy delivery."""
import argparse
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

import energy_low as low
from additional_announcements import load, save, stamp, daytime
from announcement_pause import active
from jev_laundry_review import call_jev

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home()/'Library/Application Support/SmartHomeMonitor'


def fresh(value, now, seconds=180):
    return 0 <= now-stamp(value) <= seconds


def sample_from(data, now):
    sense = load(data/'sense_now_latest.json')
    presence = load(data/'display_awake_presence_state.json')
    snapshot = load(data/'latest.json')
    virtual = load(data/'latest_homekit_virtual_sensors.json')
    watts = sense.get('watts')
    valid_power = (sense.get('ok') is True and sense.get('online') is True
                   and fresh(sense.get('capturedAt'), now) and low.number(watts))
    home = presence.get('homePresent')
    seen = presence.get('lastConfirmedAt')
    if type(home) is not bool or not low.number(seen) or not 0 <= now-seen <= 90:
        home = None
    hvac, ev = None, None
    # Context values are HomeKit observations, not invented defaults for absent devices.
    if (fresh(snapshot.get('captured_at'), now)
            and fresh(virtual.get('generatedAt'), now)
            and fresh(virtual.get('freshness', {}).get('alarmPortalGeneratedAt'), now)):
        values = snapshot.get('homeEvents', {}).get('currentCharacteristics', {}).values()
        thermostat = [v.get('value') for v in values if v.get('plugin') == 'homebridge-node-alarm-dot-com'
                      and v.get('characteristic') == 'CurrentHeatingCoolingState']
        if thermostat and all(type(v) is int and v in (0,1,2) for v in thermostat):
            hvac = any(v != 0 for v in thermostat)
    # The virtual Car Charging switch can be off merely because evidence is
    # missing. Only an explicit fresh Sense EV category supplies this context.
    car = [v.get('watts') for v in sense.get('devices', []) if v.get('id') == 'category-ev']
    if valid_power and len(car) == 1 and low.number(car[0]):
        ev = car[0] >= 500
    return {'at': sense.get('capturedAt'), 'source': 'sense_whole_house',
            'load_kw': watts/1000 if valid_power else None,
            'verified': valid_power, 'home': home, 'hvac': hvac, 'ev': ev}


def review_request(sample, usual):
    return {'model': 'jev-1.13.0', 'state': {
        'whole_house_kw': sample['load_kw'], 'matching_history_kw': usual,
        'someone_home': sample['home'], 'hvac_running': sample['hvac'], 'ev_charging': sample['ev']},
        'questions': {'energy_review': {'type': 'choice',
            'instructions': 'Review energy evidence. Null is unknown. Do not infer appliance faults or causes. Missing context or baseline requires insufficient_data. This is advisory; never authorizes announcements.',
            'criteria': {'unusual_low': 'Complete matching context and load is less than half baseline.',
                         'unusual_high': 'Complete matching context and load is more than twice baseline.',
                         'typical': 'Complete context and load is within half to twice baseline.',
                         'insufficient_data': 'Any context or baseline is unknown.'}}}}


def tick(data, config, now, *, mutate=False, ask=call_jev, deliver=None):
    sample = sample_from(data, now)
    path = data/'jev_energy_state.json'
    state = load(path)
    history = state.get('history', [])
    history = [r for r in history if 0 <= now-stamp(r.get('at')) <= 22*86400]
    prior = state.get('detector', {})
    if config.get('enabled') is not True:
        return {'status': 'disabled', 'calls': 0}
    now_text = datetime.fromtimestamp(now, timezone.utc).isoformat()
    detector, decision = low.evaluate(sample, history, prior, now_text, enabled=True)
    if sample['verified'] and low.context(sample) is not None:
        if not history or history[-1]['at'] != sample['at']:
            history.append(sample)
    state.update(history=history, detector=detector)
    report = {'generatedAt': now_text, 'status': decision['status'], 'calls': 0,
              'historySamples': len(history), 'contextKnown': low.context(sample) is not None,
              'freshPower': sample['verified'], 'delivery': None}
    # A single owner lock surrounds this function. Persist attempts before network.
    last_call = state.get('lastCall', 0)
    review_due = now-last_call >= 43200
    candidate_due = decision['status'] == 'needs_jev' and now-last_call >= 21600
    if mutate and sample['verified'] and daytime(now) and (review_due or candidate_due):
        state['lastCall'] = now
        save(path, state)
        report['callAttempted'] = True
        is_candidate = candidate_due
        request = decision['request'] if is_candidate else review_request(sample, low.baseline(sample, history, low.timestamp(now_text)))
        try:
            response = ask(request)
            report['calls'] = 1
            report['model'] = response.get('model')
            report['usage'] = response.get('usage')
            if is_candidate:
                checked = datetime.now(timezone.utc).timestamp()
                current = sample_from(data, checked)
                # Any context/load change while the API was running cancels delivery.
                if current != sample or not fresh(sample['at'], checked):
                    report['status'] = 'changed_during_request'
                else:
                    detector, result = low.evaluate(sample, history, detector,
                        datetime.fromtimestamp(checked, timezone.utc).isoformat(), enabled=True,
                        reply={'request_hash': decision['request_hash'], 'response': response})
                    state['detector'] = detector
                    save(path, state)
                    report['status'] = result['status']
                    if result['status'] == 'eligible' and not active('energy_low') and deliver:
                        report['delivery'] = deliver(result['message'], 'energy_low').get('status')
            else:
                answer = response.get('answers', {}).get('energy_review', {})
                choice = answer.get('choice')
                if response.get('model') != 'jev-1.13.0' or answer.get('type') != 'choice' or choice not in request['questions']['energy_review']['criteria']:
                    raise ValueError('Invalid advisory answer')
                report['advisory'] = choice
                state['lastAdvisory'] = {'at': now_text, 'choice': choice}
        except Exception as error:
            # Never print potentially credential-bearing request/exception details.
            report['status'] = 'jev_unavailable'
            report['errorType'] = type(error).__name__
    if mutate:
        save(path, state)
        save(data/'latest_jev_energy.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deliver', action='store_true')
    args = parser.parse_args()
    if args.deliver and ROOT != RUNTIME.resolve():
        raise RuntimeError('Requires deployed runtime')
    data = ROOT/'data'
    with (data/'jev_energy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from generate_alerts import run_indoor_homepod_announcement
        report = tick(data, load(ROOT/'config/jev_energy.json'), datetime.now(timezone.utc).timestamp(),
                      mutate=args.deliver, deliver=run_indoor_homepod_announcement)
        print(json.dumps(report))


if __name__ == '__main__': main()
