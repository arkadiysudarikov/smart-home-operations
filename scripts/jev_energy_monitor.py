"""Scheduled, budgeted Jev energy advice and conservative low-energy delivery."""
import argparse
import fcntl
import json
import math
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
    live_presence = load(data/'latest_display_awake.json')
    network = live_presence.get('unifi', {})
    current_home = live_presence.get('presence', {}).get('homePresent')
    if (live_presence.get('ok') is True and live_presence.get('mappingConfigured') is True
            and fresh(live_presence.get('generatedAt'),now,90)
            and network.get('ok') is True and network.get('cached') is False
            and type(current_home) is bool):
        home = current_home
    hvac, ev = None, None
    alarm = load(data/'latest_alarm_com.json')
    alarm_state = alarm.get('alarmState', {})
    if fresh(alarm.get('generatedAt'), now) and alarm_state.get('ok') is True:
        thermostats = [t for s in alarm_state.get('systems', [])
                       for t in s.get('components', {}).get('thermostats', [])]
        states = [str(t.get('stateText', '')).lower() for t in thermostats]
        if states and all(s in ('cooling','heating','off','idle') for s in states) and not any(t.get('isMalfunctioning') for t in thermostats):
            hvac = any(s in ('cooling','heating') for s in states)
    # Context values are HomeKit observations, not invented defaults for absent devices.
    if (hvac is None and fresh(snapshot.get('captured_at'), now)
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
    import jev_house_advisor as advisor
    evidence = advisor.facts(data, now, sample=sample)
    if mutate:
        # Every pass records fresh conflicts even when the API is on cooldown.
        # This is a diagnostic record, not a completion or delivery decision.
        save(data/'latest_jev_house_diagnostics.json', {
            'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'laundryConflicts':evidence['laundry_conflicts'],
            'acceptedAdvice':advisor.cached(data,evidence,now),
            'audioExecuted':False})
    now_text = datetime.fromtimestamp(now, timezone.utc).isoformat()
    detector, decision = low.evaluate(sample, history, prior, now_text, enabled=True)
    if sample['verified'] and low.context(sample) is not None:
        if not history or history[-1]['at'] != sample['at']:
            history.append(sample)
    state.update(history=history, detector=detector)
    report = {'generatedAt': now_text, 'status': decision['status'], 'calls': 0,
              'historySamples': len(history), 'contextKnown': low.context(sample) is not None,
              'freshPower': sample['verified'], 'delivery': None}
    report['lowEnergyStatus'] = decision['status']
    report['missingContext'] = [key for key in ('home','hvac','ev') if type(sample.get(key)) is not bool]
    # A single owner lock surrounds this function. Persist attempts before network.
    last_call = state.get('lastCall', 0)
    # Increase useful coverage without minute-by-minute model polling. The
    # shared monthly budget still applies to every scheduled/on-demand call.
    interval = config.get('review_interval_seconds', 43200)
    if type(interval) not in (int, float) or not math.isfinite(interval):
        interval = 43200
    interval = max(21600, min(86400, interval))
    review_due = now-last_call >= interval
    from zoneinfo import ZoneInfo
    local = datetime.fromtimestamp(now,ZoneInfo('America/Los_Angeles'))
    # One evening review can share the existing daily allocation, rather than
    # repeatedly polling Jev. Unknown EV context still blocks low-use alerts.
    evening_due = (config.get('evening_recap_enabled') and local.hour==19
        and state.get('eveningReviewDay')!=local.date().isoformat() and now-last_call>=300)
    review_due = review_due or evening_due
    candidate_due = decision['status'] == 'needs_jev' and now-last_call >= 21600
    report['reviewStatus'] = ('dry_run' if not mutate else 'stale_power' if not sample['verified']
        else 'quiet_hours' if not daytime(now) else 'due' if review_due or candidate_due else 'cooldown')
    if mutate and sample['verified'] and daytime(now) and (review_due or candidate_due):
        state['lastCall'] = now
        if evening_due: state['eveningReviewDay'] = local.date().isoformat()
        save(path, state)
        report['callAttempted'] = True
        is_candidate = candidate_due
        request = decision['request'] if is_candidate else review_request(sample, low.baseline(sample, history, low.timestamp(now_text)))
        try:
            if not is_candidate:
                batch = advisor.request(evidence)
                batch['state'] = {**batch['state'], **request['state']}
                batch['questions'].update(request['questions'])
                request = batch
                save(data/'jev_house_attempt.json', {'at':now})
            response = ask(request)
            report['calls'] = 1
            report['reviewStatus'] = 'completed'
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
                advisor.remember(data, evidence, response, now)
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
            report['reviewStatus'] = 'unavailable'
    if mutate:
        from jev_house_features import scheduled
        # Re-read after the model call; never dispatch from changed/stale facts.
        checked = datetime.now(timezone.utc).timestamp()
        current_evidence = advisor.facts(data, checked) if config.get('evening_recap_enabled') and local.hour==19 else evidence
        report['eveningRecap'] = scheduled(data,current_evidence,
            advisor.cached(data,current_evidence,checked),now,config,deliver)
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
