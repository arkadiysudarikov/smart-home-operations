"""Evidence-only household features; no arbitrary model prose or device controls."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from additional_announcements import load, save, stamp

LOCAL = ZoneInfo('America/Los_Angeles')
OPTIONS = {
    'change': {'energy':'Energy status changed', 'openings':'Reporting opening count changed',
               'laundry':'Laundry countdown availability changed', 'none':'No comparable change'},
    'defer': {'energy':'Energy optimization can wait', 'none':'No supported deferral'},
    'timing': {'solar':'Sustained measured solar surplus now', 'unknown':'No verified best time'},
    'oddity': {'laundry':'Laundry status conflicts with countdown', 'cooling':'Cooling with an opening',
               'energy':'High use while owner device is away', 'none':'No supported combination'},
    'recap': {'issues':'Briefly summarize verified issues', 'quiet':'No useful routine recap'},
}


def enrich(data, e, now):
    previous = load(data/'jev_house_check_baseline.json')
    old = previous.get('facts', {}) if 0 <= now-stamp(previous.get('at')) <= 86400 else {}
    changes = []
    for category, key in [('energy','high'), ('openings','openings'), ('laundry','remaining_minutes')]:
        comparable = key in old and e.get(key) is not None and old[key] is not None
        if category == 'openings':
            comparable &= e.get('reporting_openings',0)>0 and e.get('reporting_openings')==old.get('reporting_openings')
        if category == 'laundry':
            # Do not turn countdown progress into repeated changes or imply completion.
            comparable = False
        if comparable and e[key] != old[key]: changes.append(category)
    import generate_alerts as alerts
    solar = alerts.solar_surplus_sample(load(data/'latest_envoy_direct.json'),
        datetime.fromtimestamp(now,timezone.utc).isoformat())
    solar_state = load(data/'jev_solar_window.json')
    sustained = (solar is not None and solar[1] is True and solar_state.get('active') is True
        and 0 <= now-stamp(solar_state.get('last')) <= 180
        and 600 <= now-stamp(solar_state.get('since')) <= 86400)
    return {**e, 'changes':changes, 'comparison_available':bool(old), 'solar_window':bool(sustained)}


def supported(e):
    urgent = e.get('hot') or e.get('cold') or e.get('laundry_conflicts') or e.get('openings')
    odd = {'none'}
    if e.get('laundry_conflicts'): odd.add('laundry')
    if e.get('cooling_open'): odd.add('cooling')
    if e.get('away_high'): odd.add('energy')
    issues = urgent or e.get('high')
    return {'change':set(e.get('changes',[]))|{'none'},
        'defer':{'energy','none'} if e.get('high') is True and not urgent else {'none'},
        'timing':{'solar','unknown'} if e.get('solar_window') else {'unknown'},
        'oddity':odd, 'recap':{'issues','quiet'} if issues else {'quiet'}}


def checkpoint(data, e, now):
    save(data/'jev_house_check_baseline.json', {'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
        'facts':{k:e.get(k) for k in ('high','openings','reporting_openings','remaining_minutes')}})


def render(mode,e,c):
    if mode=='changes':
        if not e.get('comparison_available'): return 'No recent check to compare. This check establishes a baseline.'
        messages={'energy':'Energy is now '+('high.' if e.get('high') else 'normal.'),
            'openings':f"There are now {e.get('openings',0)} reporting openings or locks open or unlocked."}
        keys=list(e.get('changes',[])); first=c.get('change')
        if first in keys: keys.remove(first); keys.insert(0,first)
        return ' '.join(messages[k] for k in keys if k in messages) or 'No changes in comparable energy and opening readings. Missing readings are not an all-clear.'
    if mode=='wait':
        if c.get('defer')=='energy' and 'energy' in supported(e)['defer']:
            return 'Energy optimization can wait. This does not defer safety alerts or appliance problems.'
        return 'Nothing is verified safe to defer. Check current alerts; missing readings are not an all-clear.'
    if mode=='laundrytime':
        if c.get('timing')=='solar' and e.get('solar_window'):
            return 'Surplus solar has lasted ten minutes. Now is a solar opportunity for laundry, not a guaranteed cheapest time. A verified time-of-use rate schedule is unavailable.'
        return 'No best laundry time is verified yet. I need sustained solar surplus or a verified time-of-use rate schedule.'
    if mode=='unusual':
        messages={'laundry':'Laundry shows time remaining with an idle flag; it is not confirmed finished.',
            'cooling':'Cooling is running with a reporting opening.',
            'energy':'Energy is unusually high for matching conditions while your device is away; others may be home.'}
        keys=[k for k in ('laundry','cooling','energy') if k in supported(e)['oddity']]
        first=c.get('oddity')
        if first in keys: keys.remove(first); keys.insert(0,first)
        prefix = 'Indoor temperature is unusually '+('hot. ' if e.get('hot') else 'cold. ') if e.get('hot') or e.get('cold') else ''
        return prefix+' '.join(messages[k] for k in keys) if prefix or keys else 'Nothing unusual is verified in available readings. This is not an all-clear.'
    if mode=='recap':
        from jev_house_advisor import attention_text
        return 'Evening recap. '+attention_text(e,c)
    raise ValueError('Unknown feature')


def scheduled(data,e,c,now,config,deliver):
    """Track solar independently; recap once/day, occupied, unpaused, 19–20 local."""
    from generate_alerts import solar_surplus_sample
    from announcement_pause import active
    iso=datetime.fromtimestamp(now,timezone.utc).isoformat()
    sample=solar_surplus_sample(load(data/'latest_envoy_direct.json'),iso)
    solar=load(data/'jev_solar_window.json')
    if sample and sample[1]:
        if not solar.get('active') or not 0<=now-stamp(solar.get('last'))<=180:
            solar={'since':iso}
        solar.update(active=True,last=iso)
    else: solar={'active':False,'last':iso}
    save(data/'jev_solar_window.json',solar)
    local=datetime.fromtimestamp(now,LOCAL); day=local.date().isoformat()
    state=load(data/'jev_evening_recap.json')
    if (not config.get('evening_recap_enabled') or local.hour!=19 or e.get('owner_home') is not True
        or not e.get('daytime') or c.get('recap')!='issues' or active('jev_evening_recap')
        or state.get('day')==day or not deliver): return 'not_due'
    # Persist before dispatch; no retry storm or catch-up announcement overnight.
    state={'day':day,'at':iso,'status':'attempted'}; save(data/'jev_evening_recap.json',state)
    result=deliver(render('recap',e,c),'jev_evening_recap')
    state['status']=result.get('status','unknown'); save(data/'jev_evening_recap.json',state)
    return state['status']
