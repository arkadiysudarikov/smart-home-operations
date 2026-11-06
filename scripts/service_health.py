"""Read-only service inventory and evidence for household advice.

No discovery, authentication, controls, calendar contents, or raw logs leave here.
Configured bridges are never reported as proof that their devices work.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from additional_announcements import load, save, stamp

ROOT = Path(__file__).resolve().parents[1]
BRIDGES = {
    'alarm': ('Alarm.com', 'Alarmdotcom'), 'dyson': ('Dyson', 'DysonPureCoolPlatform'),
    'envoy': ('Enphase / Envoy', 'enphaseEnvoy'), 'unifi': ('UniFi', 'UnifiOccupancy'),
    'calendar': ('Calendar', 'CalendarScheduler'), 'laundry': ('SmartHQ', 'SmartHQ'),
    'virtual': ('HomeKit virtual sensors', 'HomebridgeDummy'),
    'tahoma': ('TaHoma', 'Tahoma'), 'actions': ('Household actions', 'SmartHomeActions'),
    'bravia': ('Sony Bravia', 'BraviaPlatform'),
}
FAULT_LABELS = {
    'laundry': 'Laundry telemetry or delivery needs attention.',
    'alarm': 'Alarm.com reports device trouble.',
    'sce': 'Utility interval coverage is stale or unavailable.',
    'meters': 'Solar meters disagree; check measurement coverage before trusting savings estimates.',
    'permissions': 'Homebridge backup or configuration permissions need review.',
    'homebridge': 'The Homebridge runtime needs attention.',
}
CHECKS = {
    'laundry': 'Check per-appliance heartbeats and the recovery report before relying on completion alerts.',
    'alarm': 'Reconcile retired devices in the local Alarm.com trouble report, then check connectivity of devices still in service.',
    'sce': 'Import a current SCE Green Button export, then compare its coverage with the API result.',
    'meters': 'Compare simultaneous Envoy and Sense solar readings and verify CT coverage and orientation.',
    'permissions': 'Review and restrict exposed Homebridge storage paths while preserving service access.',
    'homebridge': 'Check the Homebridge process and bridge listeners before troubleshooting individual accessories.',
}


def fresh(record, key, now, seconds):
    return 0 <= now-stamp(record.get(key)) <= seconds


def build(data, now):
    snapshot = load(data/'latest.json')
    current = fresh(snapshot, 'captured_at', now, 300)
    hb = snapshot.get('homebridge', {}) if current else {}
    config = hb.get('config', {})
    platforms = config.get('platforms', [])
    ports = {p.get('port') for p in hb.get('ports', [])}
    services = []
    for key, (name, platform) in BRIDGES.items():
        rows = [p for p in platforms if p.get('platform') == platform]
        enabled = [p for p in rows if p.get('disabled') is not True]
        reachable = sum(p.get('childBridgePort') in ports for p in enabled if p.get('childBridgePort'))
        services.append({'key':key, 'name':name, 'status':
            'unknown' if not current else 'not_configured' if not rows else 'disabled' if not enabled
            else 'bridge_reachable' if reachable == len(enabled) else 'configured',
            'configuredCount':len(rows), 'reachableBridges':reachable,
            'verification':'Bridge reachability does not verify device control, pairing, or automation.'})
    launchd=hb.get('launchd',{})
    services.append({'key':'homebridge','name':'Homebridge runtime','status':
        'running' if launchd.get('state')=='running' and launchd.get('ok') is True else 'unknown',
        'verification':'Process health is separate from accessory health.'})
    cached=snapshot.get('homeEvents',{}).get('currentCharacteristics',{}) if current else {}
    services.append({'key':'home_status','name':'Home Status dashboard','status':
        'cache_only' if any(v.get('plugin')=='homebridge-home-status' for v in cached.values()) else 'unknown',
        'verification':'Cached dashboard characteristics are not fresh telemetry or an active platform.'})
    disabled = config.get('disabledPlugins', [])
    services.append({'key':'mopar','name':'Mopar / Jeep','status':
        'disabled' if current and 'homebridge-mopar' in disabled else 'unknown',
        'verification':'Do not infer charging state from an absent integration.'})
    for key, name in [('apple_home','Apple Home / HomeKit'),('siri','Siri / Shortcuts'),
                      ('homepods','HomePods / Intercom'),('matter','Matter / Thread'),
                      ('att','AT&T internet'),('airplay','AirPlay')]:
        services.append({'key':key,'name':name,'status':'manual_verification',
                         'verification':'End-to-end operation requires a separate functional check.'})
    for key, name, file, time_key, age in [
        ('sense','Sense','sense_now_latest.json','capturedAt',180),
        ('chargepoint','ChargePoint','latest_chargepoint_refresh.json','finishedAt',86400),
        ('displays','Mac display / presence','latest_display_awake.json','generatedAt',90),
    ]:
        record=load(data/file)
        if key == 'sense' and 'sampleAt' in record:
            time_key = 'sampleAt'
        if key == 'chargepoint':
            source = load(data/'chargepoint_sessions.json')
            if source:
                record = {**record, 'sourceCapturedAt': source.get('capturedAt')}
                time_key = 'sourceCapturedAt'
        status='stale_or_missing' if not fresh(record,time_key,now,age) else 'source_ok' if record.get('ok') is True else 'needs_attention'
        services.append({'key':key,'name':name,'status':status,'verification':'Source status only; device behavior is separate.'})
    accessories={a.get('accessory') for a in config.get('accessories', [])}
    services.append({'key':'bubbler','name':'Bubbler / Delay Switch','status':
        'configured' if current and 'DelaySwitch' in accessories else 'unknown',
        'verification':'Physical pump operation is not established by a virtual switch.'})

    faults = {key:None for key in FAULT_LABELS}
    if launchd.get('ok') is True:
        faults['homebridge']=launchd.get('state')!='running'
    privacy = hb.get('security',{}).get('homebridgePermissions',{})
    if type(privacy.get('ok')) is bool: faults['permissions'] = not privacy['ok']
    health = load(data/'latest_announcement_health.json')
    if fresh(health,'generatedAt',now,180) and type(health.get('ok')) is bool:
        # Restrict the signal to laundry faults, not an unrelated relay event.
        known = {'Active laundry source stale','Laundry capture stale or missing',
                 'SmartHQ instrumentation needs review','Laundry delivery failed or uncertain'}
        faults['laundry'] = any(f in known for f in health.get('faults', []))
    alarm=load(data/'latest_alarm_com.json')
    trouble=alarm.get('troubleConditions', {})
    trouble_count=None
    if (fresh(alarm,'generatedAt',now,180) and trouble.get('ok') is True
            and fresh(trouble,'checkedAt',now,180) and type(trouble.get('count')) is int
            and trouble['count'] >= 0):
        trouble_count=trouble['count']; faults['alarm']=trouble_count > 0
    alerts=load(data/'latest_alerts.json')
    if fresh(alerts,'generatedAt',now,300):
        titles={a.get('title') for a in alerts.get('alerts', [])}
        # Alerts are used only for positive faults; their absence isn't a fresh source check.
        if 'Solar meters disagree' in titles: faults['meters']=True
        if 'Homebridge storage permissions are too open' in titles: faults['permissions']=True
    api=load(data/'latest_sce_api.json')
    combined=load(data/'latest_combined_energy_monitor.json')
    local_sce=(combined.get('sources') or {}).get('sce') or {}
    api_end=api.get('coverageEnd'); imported_end=local_sce.get('coverageEnd')
    # Actual coverage, not the download's timestamp, determines utility readiness.
    coverage=[stamp(t) for t in (api_end,imported_end) if 0 <= now-stamp(t)]
    if fresh(api,'finishedAt',now,86400) or fresh(combined,'generatedAt',now,300):
        faults['sce']=not coverage or now-max(coverage) > 48*3600
    services.append({'key':'sce','name':'SCE / Green Button','status':
        'unknown' if faults['sce'] is None else 'needs_attention' if faults['sce'] else 'coverage_current',
        'apiCoverageEnd':api_end,'importedCoverageEnd':imported_end,
        'verification':'API coverage and imported coverage are separate evidence.'})
    services.append({'key':'jev','name':'JEV / TypeSafe','status':'configured' if load(data.parent/'config/jev_energy.json').get('enabled') is True else 'disabled',
                     'verification':'Local configuration is not proof of a successful provider call.'})
    return {'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'services':services,'faults':faults,'securityTroubleCount':trouble_count,
            'needsAttention':any(v is True for v in faults.values()),
            'coverageComplete':False}


def evidence(report):
    # Explicit schema: never pass labels, dates, paths, IDs, or arbitrary text.
    return {'service_faults':{k:report['faults'].get(k) for k in FAULT_LABELS},
            'security_trouble_count':report.get('securityTroubleCount')}


def render(report):
    lines=['# Household service coverage', '', f"Generated: {report['generatedAt']}", '',
           'Configured or reachable does not mean functionally verified.', '',
           '| Service | Evidence status |', '| --- | --- |']
    lines += [f"| {s['name']} | {s['status']} |" for s in report['services']]
    lines += ['', '## Verified faults', '']
    lines += ['- '+FAULT_LABELS[k]+' '+CHECKS[k] for k,v in report['faults'].items() if v is True]
    sce=next(s for s in report['services'] if s['key']=='sce')
    lines += ['',f"SCE API coverage: {sce['apiCoverageEnd']}; imported coverage: {sce['importedCoverageEnd']}."]
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    args=parser.parse_args(); report=build(ROOT/'data',datetime.now(timezone.utc).timestamp())
    if args.write:
        if ROOT != Path.home()/'Library/Application Support/SmartHomeMonitor':
            raise SystemExit('Requires deployed runtime')
        save(ROOT/'data/latest_service_health.json',report)
        (ROOT/'reports/service_health.md').write_text(render(report))
    print(json.dumps(report))


if __name__=='__main__':main()
