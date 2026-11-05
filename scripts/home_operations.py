"""Independent, evidence-gated household advice and quiet incident monitoring.

No device controls, rule edits, raw-log uploads, or model-generated spoken prose.
"""
import argparse
import fcntl
import hashlib
import json
import math
import subprocess
import shlex
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from additional_announcements import load, save, stamp, daytime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home()/'Library/Application Support/SmartHomeMonitor'
ROUTINE = {'detergent_tank_low', 'energy_high_on', 'energy_ok_off', 'energy_low'}


def fresh(record, key, now, seconds=180):
    return 0 <= now-stamp(record.get(key)) <= seconds


def enabled(data):
    return load(data.parent/'config/smart_operations.json').get('enabled') is True


def condition(data, identifier, now):
    """True=still actionable, False=resolved, None=insufficient current evidence."""
    if identifier == 'detergent_tank_low':
        laundry = load(data/'latest_smarthq_laundry_state.json')
        washer = laundry.get('devices', {}).get('washer', {})
        if not (laundry.get('ok') is True and fresh(laundry,'capturedAt',now)
                and fresh(washer,'detergentTankObservedAt',now)):
            return None
        refilled = load(data/'maintenance_resolutions.json').get('washerTankRefilledAt')
        if (refilled and washer.get('detergentTankLow') is True
                and stamp(washer.get('detergentTankSourceUpdatedAt')) <= stamp(refilled)):
            return False  # The user resolved this older appliance episode.
        value = washer.get('detergentTankLow')
        return value if type(value) is bool else None
    if identifier == 'energy_high_on':
        energy = load(data/'latest_energy_high_context.json')
        if not (fresh(energy,'generatedAt',now) and fresh(energy,'sampleAt',now)):
            return None
        watts, threshold = energy.get('liveLoadKw'), energy.get('thresholdKw')
        if all(type(v) in (int,float) and math.isfinite(v) for v in (watts,threshold)) and threshold > 0:
            return watts >= threshold
        return None
    if identifier == 'energy_ok_off':
        energy = load(data/'energy_ok_announcement.json')
        if fresh(energy,'updatedAt',now) and type(energy.get('energyOkActive')) is bool:
            return not energy['energyOkActive']
        return None
    if identifier == 'energy_low':
        from jev_energy_monitor import sample_from
        from energy_low import baseline,context
        sample = sample_from(data,now)
        if sample.get('verified') is not True or context(sample) is None:
            return None
        usual = baseline(sample,load(data/'jev_energy_state.json').get('history',[]),
                         datetime.fromtimestamp(now,timezone.utc))
        if usual is None:return None
        return sample['load_kw'] <= usual*.5 and usual-sample['load_kw'] >= .3
    if identifier in ('washer','dryer','combo'):
        laundry = load(data/'latest_smarthq_laundry_state.json')
        device = laundry.get('devices',{}).get(identifier,{})
        if not (laundry.get('ok') is True and fresh(laundry,'capturedAt',now)
                and fresh(device,'apiLastSuccessAt',now,300)):
            return None
        if device.get('doorOpen') is True or (device.get('cycleActive') is True
                and device.get('ventingActive') is not True):
            return False
        return True
    if identifier == 'laundry_health':
        health = load(data/'latest_announcement_health.json')
        if not fresh(health,'generatedAt',now):
            return None
        relevant = {'Laundry capture stale or missing','SmartHQ instrumentation needs review',
                    'Active laundry source stale','Laundry delivery failed or uncertain'}
        return bool(relevant.intersection(health.get('faults',[])))
    return True  # Explicit acknowledgment remains authoritative for other sources.


def routine_order(data, identifiers, now):
    """Reuse bounded model advice only after reapplying current evidence gates."""
    advice = load(data/'jev_house_advice.json')
    at = advice.get('at')
    attention = (advice.get('choices',{}).get('attention') if advice.get('model')=='jev-1.13.0'
                 and type(at) in (int,float) and 0 <= now-at <= 600 else None)
    preferred = {'detergent':'detergent_tank_low','energy':'energy_high_on'}.get(attention)
    return sorted(identifiers, key=lambda key:(key != preferred, key))


def household_order(data, pending, now):
    advice = load(data/'jev_house_advice.json');at=advice.get('at')
    attention = (advice.get('choices',{}).get('attention') if advice.get('model')=='jev-1.13.0'
                 and type(at) in (int,float) and 0 <= now-at <= 600 else None)
    def category(key):
        return 'temperature' if key.startswith('temperature:') else 'energy' if key.startswith('cooling:') else 'openings'
    return sorted(pending,key=lambda key:(category(key)!='temperature',category(key)!=attention))


def solar_sample(envoy, now, minimum=1000):
    """Use actual export and signed battery readings; discharge is not surplus PV."""
    if envoy.get('ok') is not True:
        return None
    for probe in envoy.get('probes',[]):
        if probe.get('productionStatus') != 200:
            continue
        payload = probe.get('production') or {}
        rows = {r.get('measurementType'):r for group in ('production','consumption','storage')
                for r in (payload.get(group) or []) if r.get('type')=='eim' and r.get('activeCount',0)>0}
        # IQ Battery storage can be present in /ivp/meters/readings while the
        # legacy production.json acb array reports activeCount=0. Match only an
        # enabled, healthy storage meter by its metadata; never assume zero.
        storage_ids = {m.get('eid') for m in (probe.get('meterInfo') or [])
                       if m.get('measurementType')=='storage' and m.get('state')=='enabled'
                       and m.get('meteringStatus')=='normal'}
        storage = [m for m in (probe.get('meters') or []) if m.get('eid') in storage_ids]
        if 'storage' not in rows and probe.get('metersStatus')==200 and len(storage)==1:
            rows['storage'] = {'wNow':storage[0].get('instantaneousDemand'),
                               'readingTime':storage[0].get('timestamp')}
        kinds = ('production','total-consumption','net-consumption','storage')
        if not all(k in rows for k in kinds):
            continue
        values, times = {}, []
        for kind in kinds:
            value, at = rows[kind].get('wNow'), rows[kind].get('readingTime')
            if (type(value) not in (int,float) or not math.isfinite(value)
                    or type(at) not in (int,float) or not math.isfinite(at) or not 0 <= now-at <= 180):
                return None
            values[kind] = value; times.append(at)
        if max(times)-min(times) > 30:
            return None
        # Envoy storage positive means discharge. Never call battery-funded export solar.
        surplus = (values['production'] >= minimum and values['net-consumption'] <= -minimum
                   and values['storage'] <= 50 and values['total-consumption'] >= 0)
        return {'at':min(times),'surplus':surplus,'exportWatts':-values['net-consumption'],
                'productionWatts':values['production'],'consumptionWatts':values['total-consumption'],
                'storageWatts':values['storage']}
    return None


def laundry_advice(data, state, now, config):
    sample = solar_sample(load(data/'latest_envoy_direct.json'),now,
                          config.get('minimum_solar_export_watts',1000))
    old = state.get('solar',{})
    if not sample or not sample['surplus']:
        state['solar'] = {'active':False}
        return {'status':'unavailable' if not sample else 'no_sustained_surplus', 'eligible':False}
    previous = old.get('last',0)
    continuous = (old.get('active') and 0 <= sample['at']-previous <= 180
                  and 0 <= now-old.get('checkedAt',0) <= 180)
    since = old.get('since') if continuous else sample['at']
    state['solar'] = {'active':True,'since':since,'last':sample['at'],'checkedAt':now,
                      'notified':old.get('notified',False) if continuous else False}
    sustained = sample['at']-since >= config.get('solar_window_seconds',600)
    laundry = load(data/'latest_smarthq_laundry_state.json')
    devices = laundry.get('devices',{})
    idle = (laundry.get('ok') is True and fresh(laundry,'capturedAt',now)
            and set(devices) >= {'washer','dryer','combo'}
            and all(fresh(devices[k],'apiLastSuccessAt',now,300)
                    and devices[k].get('cycleActive') is False for k in ('washer','dryer','combo')))
    return {'status':'solar_opportunity' if sustained else 'building_solar_window',
            'eligible':bool(sustained and idle and config.get('solar_advice_enabled')),
            'windowSeconds':sample['at']-since,'sample':sample,
            'message':'Surplus solar has lasted ten minutes without battery discharge. If you have laundry waiting, now is a solar opportunity. This is not a cheapest-price guarantee.'}


def consistency(data, homebridge, now):
    """Check explicit contracts, retaining unknown coverage instead of an all-clear."""
    sources = load(data/'latest_automation_consistency_sources.json')
    rules = sources.get('automationRules',{})
    video = sources.get('videoRules',{})
    alarm_fresh = fresh(sources,'generatedAt',now,1800) and rules.get('ok') is True
    findings = []
    if alarm_fresh:
        for rule in rules.get('garageLightRules',[]):
            if not rule.get('isPaused') and 'off' in str(rule.get('followUpAction','')).lower():
                findings.append({'id':'garage_fixed_off','message':'Alarm.com has a fixed garage-light shutoff that can conflict with camera occupancy. Review the garage-light rule timers.'})
        for expected in ('Doorbell - People and Deliveries','Doorbell - Button Press'):
            matches = [r for r in video.get('rules',[]) if r.get('name')==expected]
            if video.get('ok') is True and (not matches or any(r.get('isPaused') for r in matches)):
                findings.append({'id':'doorbell_recording_disabled','message':'A required doorbell recording rule is missing or paused. Review Alarm.com doorbell recording rules.'})
    accessories = list(homebridge.get('accessories',[]))
    accessories += [a for p in homebridge.get('platforms',[]) for a in p.get('accessories',[])]
    mirror = [a for a in accessories if a.get('name')=='Garage Camera Motion']
    if homebridge and not mirror:
        findings.append({'id':'camera_mirror_missing','message':'The Homebridge garage camera mirror is missing. Review the camera-aware lighting bridge before relying on automatic shutoff.'})
    for row in mirror:
        def hook(key, value):
            try:
                argv = shlex.split(row.get(key,''))
                return len(argv)==3 and argv[-2:]==[str(data.parent/'scripts/garage_camera_event.py'),value]
            except (TypeError,ValueError): return False
        if not all(hook(key,value) for key,value in [('commandOn','on'),('commandOff','off')]):
            findings.append({'id':'camera_mirror_hooks','message':'The garage camera mirror hooks differ from the deployed occupancy controller. Review Homebridge camera-motion commands.'})
    home = load(data/'home_automation_review.json')
    home_fresh = fresh(home,'reviewedAt',now,86400)
    if home_fresh:
        active = [r for r in home.get('rules',[]) if r.get('enabled') is True]
        for row in active:
            if row.get('trigger')=='last-departure' and row.get('action')=='arm-away' and row.get('condition')!='dogs-with-us':
                findings.append({'id':'departure_dogs','message':'An Apple Home departure rule selects Armed Away without the Dogs with us condition. Review departure security policy.'})
        groups = {}
        for row in active:
            key = (row.get('trigger'),row.get('target'),row.get('window','always'))
            groups.setdefault(key,set()).add(row.get('action'))
        if any({'on','off'} <= actions for actions in groups.values()):
            findings.append({'id':'home_opposite_actions','message':'Apple Home contains opposing actions for the same trigger and target. Review the conflicting automations.'})
        for trigger,target,action in [('garage-motion','garage-camera-mirror','on'),('garage-no-motion','garage-camera-mirror','off')]:
            covered = any(r.get('trigger')==trigger for r in home.get('rules',[]))
            if covered and not any(r.get('trigger')==trigger and r.get('target')==target
                                   and r.get('action')==action for r in active):
                findings.append({'id':'home_camera_rule','message':'An inspected Apple Home camera-mirror rule is disabled or conflicts with occupancy control. Review the garage motion automations.'})
    return {'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'findings':list({f['id']:f for f in findings}.values()),
            'coverage':{'alarmRules':'fresh' if alarm_fresh else 'unknown',
                        'homebridge':'read_current_config' if homebridge else 'unknown',
                        'appleHome':'review_snapshot_only' if home_fresh else 'stale_manual_review'},
            'complete':False,'controlsExecuted':False}


def health_issues(data, now, review):
    issues = {f['id']:f['message'] for f in review['findings']}
    health = load(data/'latest_announcement_health.json')
    if not fresh(health,'generatedAt',now,300):
        issues['health_scheduler'] = 'The announcement health report stopped updating. Check the home-monitor service.'
    else:
        mapping = {'Active laundry source stale':('laundry_source','Laundry status stopped updating during an active cycle. Check SmartHQ connectivity before relying on finish alerts.'),
                   'Laundry capture stale or missing':('laundry_capture','Laundry capture stopped updating. Check the independent laundry worker.'),
                   'SmartHQ instrumentation needs review':('laundry_instrumentation','A SmartHQ update changed the monitored integration. Review its heartbeat instrumentation.'),
                   'Announcement relay failed or log invalid':('relay','Announcement delivery failed. Check the iPhone relay and its last error.'),
                   'Laundry delivery failed or uncertain':('delivery','A laundry delivery failed or has an uncertain outcome. Review its delivery log before replaying it.')}
        for fault in health.get('faults',[]):
            if fault in mapping:
                key,message = mapping[fault]; issues[key] = message
    laundry = load(data/'latest_smarthq_laundry_state.json')
    if not fresh(laundry,'capturedAt',now,300):
        issues['laundry_capture'] = 'Laundry capture stopped updating. Check the independent laundry worker.'
    rules = load(data/'latest_automation_consistency_sources.json')
    if not fresh(rules,'generatedAt',now,3600) or rules.get('ok') is False:
        issues['rules_source'] = 'The automatic Alarm.com rule check cannot obtain current rules. Check the saved Alarm.com session.'
    runtime = load(data/'latest_home_local_runtime.json')
    if fresh(runtime,'generatedAt',now) and runtime.get('ok') is False:
        issues['local_runtime'] = 'A critical local Homebridge bridge or the household action service is unreachable. Check the local home services.'
    return issues


def local_runtime(homebridge, now):
    checks = {}
    for row in homebridge.get('platforms',[]):
        platform = row.get('platform')
        if platform not in ('Alarmdotcom','SmartHQ','HomebridgeDummy','SmartHomeActions') or row.get('disabled') is True:
            continue
        port = row.get('_bridge',{}).get('port')
        if type(port) is not int or not 1 <= port <= 65535:continue
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.5):checks[platform]=True
        except OSError:checks[platform]=False
    try:
        with urllib.request.urlopen('http://127.0.0.1:18765/health',timeout=2) as response:
            checks['householdActions'] = response.status==200
    except OSError:checks['householdActions']=False
    return {'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),'ok':all(checks.values()),'checks':checks}


def incidents(previous, issues, now, hold=600):
    current, due = {}, {}
    for key,message in issues.items():
        old = previous.get(key,{})
        continuous = 0 <= now-old.get('last',0) <= 180
        item = {'since':old.get('since',now) if continuous else now,'last':now,
                'notified':old.get('notified',False) if continuous else False,'message':message}
        current[key] = item
        if now-item['since'] >= hold and not item['notified']:
            due[key] = message
    return current,due  # Resolved episodes disappear; no recovery announcement or repeat spam.


def tick(data, now, config, *, deliver=None, notify=None, homebridge=None):
    state_path = data/'home_operations_state.json'
    state = load(state_path)
    review = consistency(data,homebridge or {},now)
    save(data/'latest_automation_consistency.json',review)
    issues = health_issues(data,now,review)
    state['incidents'], due = incidents(state.get('incidents',{}),issues,now,config.get('health_hold_seconds',600))
    advice = laundry_advice(data,state,now,config)
    from announcement_presence import allowed
    from announcement_pause import active
    messages = list(due.values())
    solar_due = advice['eligible'] and not state.get('solar',{}).get('notified')
    if solar_due:
        messages.append(advice['message'])
    result = None
    if messages and daytime(now) and allowed(data,now) and not active('smart_home_digest') and deliver:
        # At-most-once attempt: persist before the external relay, outside all queue locks.
        for key in due: state['incidents'][key]['notified'] = True
        if solar_due: state['solar']['notified'] = True
        save(state_path,state)
        text = 'Home update. '+' '.join(dict.fromkeys(messages))
        result = deliver(text,'smart_home_digest')
        if due and notify: notify(' '.join(due.values()),'Smart Home needs attention')
    save(state_path,state)
    report = {'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
              'issues':list(issues),'due':list(due),'delivery':result,
              'laundryAdvice':advice,'consistencyCoverage':review['coverage'],
              'controlsExecuted':False}
    save(data/'latest_home_operations.json',report)
    return report


def refresh_sources(data, config, now):
    """Bounded reads; persist attempts before network so failures cannot spin."""
    from refresh_energy import python_path, node_path
    outcomes = {}
    try:
        result = subprocess.run([python_path(),str(ROOT/'scripts/announcement_health.py')],
            capture_output=True,timeout=30,check=False)
        outcomes['healthExitCode'] = result.returncode
    except (OSError,subprocess.TimeoutExpired):
        outcomes['healthExitCode'] = None
    latest = load(data/'latest_alarm_com.json')
    if (fresh(latest,'generatedAt',now,config.get('rules_refresh_seconds',900))
            and latest.get('automationRules',{}).get('ok') is True
            and latest.get('videoRules',{}).get('ok') is True):
        save(data/'latest_automation_consistency_sources.json',{
            'generatedAt':latest['generatedAt'],'automationRules':latest['automationRules'],
            'videoRules':latest['videoRules'],'ok':True,'controlsExecuted':False})
        outcomes['rules'] = 'shared_fresh_capture'
    else:
        attempt = load(data/'home_rules_refresh_attempt.json')
        previous = attempt.get('at',0)
        if type(previous) in (int,float) and now-previous >= config.get('rules_refresh_seconds',900):
            save(data/'home_rules_refresh_attempt.json',{'at':now,'status':'attempted'})
            try:
                node = node_path()
                result = subprocess.run([node,str(ROOT/'scripts/capture_alarm_com.js'),'--rules-only'],
                    capture_output=True,timeout=45,check=False) if node else None
                captured = load(data/'latest_automation_consistency_sources.json')
                outcomes['rules'] = ('captured' if result and result.returncode==0
                                     and captured.get('ok') is True else 'failed')
            except (OSError,subprocess.TimeoutExpired): outcomes['rules'] = 'failed'
            save(data/'home_rules_refresh_attempt.json',{'at':now,'status':outcomes['rules']})
        else: outcomes['rules'] = 'cooldown'
    return outcomes


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--deliver',action='store_true');args=parser.parse_args()
    if ROOT != RUNTIME:
        raise SystemExit('Requires deployed runtime')
    data = ROOT/'data'; config = load(ROOT/'config/smart_operations.json')
    if config.get('enabled') is not True: return
    with (data/'home_operations.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        started = datetime.now(timezone.utc).timestamp()
        sources = refresh_sources(data,config,started)
        from generate_alerts import run_indoor_homepod_announcement
        from washer_notifier import mac_notification
        hb = load(Path.home()/'.homebridge/config.json')
        save(data/'latest_home_local_runtime.json',local_runtime(hb,datetime.now(timezone.utc).timestamp()))
        report=tick(data,datetime.now(timezone.utc).timestamp(),config,
                    deliver=run_indoor_homepod_announcement if args.deliver else None,
                    notify=mac_notification if args.deliver else None,homebridge=hb)
        # Advice stays bounded, private and optional. Only changed issue categories
        # request a review; never poll the model on every watt/countdown change.
        pending = load(data/'announcement_ack.json').get('pending',{})
        routine = sorted(set(pending).intersection(ROUTINE))
        fingerprint = hashlib.sha256(json.dumps([sorted(report['issues']),routine,report['laundryAdvice']['status']]).encode()).hexdigest()
        attempt = load(data/'home_operations_model_review.json')
        now = datetime.now(timezone.utc).timestamp()
        if (args.deliver and fingerprint != attempt.get('fingerprint')
                and (report['issues'] or routine or report['laundryAdvice']['status']=='solar_opportunity')
                and now-attempt.get('at',0) >= config.get('model_review_seconds',1800)):
            save(data/'home_operations_model_review.json',{'at':now,'fingerprint':fingerprint})
            from jev_house_advisor import refresh
            report['modelReview'] = refresh(data,now)
        report['sourceRefresh'] = sources
        save(data/'latest_home_operations.json',report)
        print(json.dumps({'generatedAt':report['generatedAt'],'issues':report['issues'],
                          'advice':report['laundryAdvice']['status'],'deliveryAttempted':report['delivery'] is not None}))


if __name__=='__main__':main()
