"""Bounded household decision support. No mailbox access, audio, or device controls.

Only fixed categories, counts, and measurements leave this module. Model choices
must pass both response validation and local evidence gates before use.
"""
import argparse
import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from additional_announcements import load, save, stamp, daytime
from energy_low import number
from jev_laundry_review import call_jev

MODEL = 'jev-1.13.0'
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home()/'Library/Application Support/SmartHomeMonitor'
CATEGORIES = {
    'hvac': {'central ac', 'air conditioner', 'heat pump', 'furnace'},
    'ev': {'electric vehicle', 'ev charger', 'car charger'},
    'laundry': {'washer', 'dryer', 'washing machine', 'clothes dryer'},
}
LABELS = {'hvac':'heating or cooling', 'ev':'EV charging', 'laundry':'laundry'}
OPTIONS = {
    'contributor': {'hvac':'Measured HVAC contributor', 'ev':'Measured EV contributor',
        'laundry':'Measured laundry contributor', 'multiple':'Multiple measured contributors', 'unknown':'No supported explanation'},
    'attention': {'temperature':'Abnormal temperature', 'laundry':'Conflicting laundry readings',
        'openings':'Open or unlocked entry', 'energy':'High consumption', 'none':'No verified issue'},
    'laundry': {'conflict':'Positive countdown conflicts with idle cycle flag', 'unknown':'No verified conflict'},
    'routine': {'announce':'A routine issue merits attention', 'combine':'Combine multiple routine issues',
        'quiet':'No additional routine summary needed'},
    'detail': {'energy':'Measured energy details', 'laundry':'Laundry evidence',
        'openings':'Reporting opening counts', 'unknown':'No supporting detail'},
    'savings': {'cooling_open':'Cooling with an opening', 'away_high':'Owner carried device away and unusually high matching load',
        'unknown':'No verified opportunity'},
}


def fresh(value, now, seconds=180):
    return 0 <= now-stamp(value) <= seconds


def facts(data, now, sample=None):
    """Extract fresh facts without sending names, IDs, locations, or timestamps."""
    from jev_energy_monitor import sample_from
    import energy_low
    import generate_alerts
    sample = sample_from(data, now) if sample is None else sample
    context = load(data/'latest_energy_high_context.json')
    energy_known = (fresh(context.get('generatedAt'), now) and fresh(context.get('sampleAt'), now)
                    and number(context.get('liveLoadKw')) and number(context.get('thresholdKw'))
                    and context['thresholdKw'] > 0)
    load_kw = context['liveLoadKw'] if energy_known else None
    measured = {key:0. for key in CATEGORIES}
    for source in context.get('candidates', []) if energy_known else []:
        if source.get('source') != 'Sense' or not fresh(source.get('capturedAt'), now):
            continue
        seen = set()
        for device in source.get('devices', []):
            name = str(device.get('name', '')).strip().lower()
            identity = str(device.get('id') or name)
            watts = device.get('watts')
            if identity in seen or not number(watts) or watts < 200 or identity == 'solar':
                continue
            seen.add(identity)
            for category, names in CATEGORIES.items():
                if name in names or (category == 'ev' and identity == 'category-ev'):
                    measured[category] += watts/1000
    # Contradictory/overlapping estimates are not a supported attribution.
    if load_kw is None or sum(measured.values()) > load_kw*1.1:
        measured = {key:0. for key in CATEGORIES}
    alarm = load(data/'latest_alarm_com.json')
    observed = generate_alerts.household_observations(alarm,
        datetime.fromtimestamp(now, timezone.utc).isoformat(), max_age=3)
    openings = sum(v['state'] in ('open','unlocked') for k,v in observed.items()
                   if k.startswith(('sensors:','garages:','locks:')))
    reporting = sum(k.startswith(('sensors:','garages:','locks:')) for k in observed)
    hot = any(k.startswith('temperature:hot:') and v['state']=='open' for k,v in observed.items())
    cold = any(k.startswith('temperature:cold:') and v['state']=='open' for k,v in observed.items())
    cooling_open = any(k.startswith('cooling:') and v['state']=='open' for k,v in observed.items())
    laundry = load(data/'latest_smarthq_laundry_state.json')
    conflicts, remaining = 0, []
    if laundry.get('ok') is True and fresh(laundry.get('capturedAt'),now):
        for name, device in laundry.get('devices',{}).items():
            if name not in ('washer','dryer','combo') or not fresh(device.get('apiLastSuccessAt'),now):
                continue
            seconds = device.get('remainingSeconds')
            if number(seconds) and 0 < seconds <= 86400:
                remaining.append(round(seconds/60,1))
                if device.get('cycleActive') is False:
                    conflicts += 1
    history = load(data/'jev_energy_state.json').get('history', [])
    usual = energy_low.baseline(sample, history, datetime.fromtimestamp(now,timezone.utc)) if sample['verified'] and energy_low.context(sample) is not None else None
    return {'load_kw':load_kw, 'threshold_kw':context['thresholdKw'] if energy_known else None,
        'high':load_kw >= context['thresholdKw'] if energy_known else None,
        'measured_kw':{k:round(v,3) for k,v in measured.items()},
        'openings':openings, 'reporting_openings':reporting, 'hot':hot, 'cold':cold,
        'cooling_open':cooling_open, 'laundry_conflicts':conflicts, 'remaining_minutes':remaining[:3],
        'owner_home':sample['home'], 'usual_kw':usual,
        'away_high':sample['home'] is False and usual is not None and sample['load_kw'] > 2*usual,
        'daytime':daytime(now)}


def request(evidence):
    return {'model':MODEL, 'state':evidence, 'questions':{
        key:{'type':'choice', 'criteria':choices,
             'instructions':'Select only supported facts. Null means unknown. Do not infer faults or completion. Routine is advisory only; never suppress safety or laundry. Use unknown/none/quiet when unsupported.'}
        for key,choices in OPTIONS.items()}}


def digest(evidence):
    return hashlib.sha256(json.dumps(evidence,sort_keys=True,allow_nan=False).encode()).hexdigest()


def supported(e):
    contributors = {k for k,v in e['measured_kw'].items() if v>=.2} if e['high'] is True else set()
    if len(contributors)>1:
        contributors.add('multiple')
    attention = set()
    if e['hot'] or e['cold']: attention.add('temperature')
    if e['laundry_conflicts']: attention.add('laundry')
    if e['openings']: attention.add('openings')
    if e['high'] is True: attention.add('energy')
    details = ({'energy'} if e['load_kw'] is not None else set())
    if e['remaining_minutes']: details.add('laundry')
    if e['reporting_openings']: details.add('openings')
    savings = {'unknown'}
    if e['cooling_open']: savings.add('cooling_open')
    if e['away_high']: savings.add('away_high')
    routine = {'quiet'}
    if e['daytime'] and attention: routine.add('announce')
    if e['daytime'] and len(attention)>1: routine.add('combine')
    return {'contributor':contributors|{'unknown'}, 'attention':attention|{'none'},
        'laundry':{'unknown','conflict'} if e['laundry_conflicts'] else {'unknown'},
        'routine':routine, 'detail':details|{'unknown'}, 'savings':savings}


def validated(response, evidence):
    """Each answer fails independently. Probabilities do not override evidence."""
    results = {}
    if not isinstance(response,dict) or response.get('model') != MODEL:
        return results
    allowed = supported(evidence)
    for key, options in OPTIONS.items():
        try:
            a = response['answers'][key]
            p = a['probabilities']
            confidence = a['confidence']
            choice = a['choice']
            if (a['type']=='choice' and choice in allowed[key] and set(p)==set(options)
                and all(number(v) and v<=1 for v in p.values()) and abs(sum(p.values())-1)<.001
                and number(confidence) and .9<=confidence<=1
                and p[choice]>=.9 and p[choice]==max(p.values())):
                results[key]=choice
        except (KeyError,TypeError,AttributeError):
            pass
    return results


def remember(data, evidence, response, now):
    report = {'at':now, 'hash':digest(evidence), 'choices':validated(response,evidence),
              'model':MODEL, 'usage':response.get('usage'), 'audioExecuted':False}
    save(data/'jev_house_advice.json',report)
    return report


def cached(data, evidence, now):
    report = load(data/'jev_house_advice.json')
    at = report.get('at')
    if not number(at) or not 0<=now-at<=600 or report.get('hash')!=digest(evidence):
        return {}
    # Reapply local gates even to persisted model choices.
    allowed = supported(evidence)
    return {k:v for k,v in report.get('choices',{}).items() if k in allowed and v in allowed[k]}


def refresh(data, now, *, ask=call_jev, interval=300):
    """Persist attempt before API; unknown/failure always uses local fallback."""
    with (data/'jev_house.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status':'busy','calls':0}
        e = facts(data,now)
        if cached(data,e,now):
            return {'status':'cached','calls':0}
        state = load(data/'jev_house_attempt.json')
        last = state.get('at',0)
        if not number(last) or now-last<interval:
            return {'status':'cooldown','calls':0}
        save(data/'jev_house_attempt.json',{'at':now})
        try:
            response = ask(request(e))
            result = remember(data,e,response,now)
            return {'status':'reviewed','calls':1,'accepted':result['choices'],'usage':result['usage']}
        except Exception as error:
            return {'status':'unavailable','calls':0,'errorType':type(error).__name__}


def energy_text(e, choices):
    if e['load_kw'] is None: return 'Current energy readings are unavailable.'
    if not e['high']: return 'Energy use is normal.'
    key = choices.get('contributor')
    keys = [k for k,v in e['measured_kw'].items() if v>=.2] if key=='multiple' else [key]
    details = [f"{LABELS[k]}, about {e['measured_kw'][k]:.1f} kilowatts" for k in keys
               if k in LABELS and e['measured_kw'][k]>=.2]
    if not details: return 'Energy is high; no verified appliance explanation is available.'
    return 'Energy is high. Sense estimates '+ '; '.join(details)+'. These are contributors, not proof of the full cause.'


def attention_text(e, choices):
    issues = {}
    if e['hot'] or e['cold']: issues['temperature']='Indoor temperature is unusually '+('hot' if e['hot'] else 'cold')+'.'
    if e['laundry_conflicts']: issues['laundry']='Laundry reports time remaining despite an idle flag. Completion is not confirmed.'
    if e['openings']: issues['openings']=f"{e['openings']} reporting doors, windows, garages, or locks are open or unlocked."
    if e['high']: issues['energy']='Energy use is high.'
    # Temperature is never demoted; Jev reorders other verified facts, never drops them.
    order = ['temperature',choices.get('attention'),'laundry','openings','energy']
    result = []
    for key in order:
        if key in issues and issues[key] not in result: result.append(issues[key])
    if not result: result=['No issues identified in the available readings.']
    if not e['reporting_openings'] or e['load_kw'] is None:
        result.append('Some sensor readings are unavailable; this is not an all-clear.')
    return ' '.join(result)


def savings_text(e, choices):
    choice = choices.get('savings')
    if choice=='cooling_open' and e['cooling_open']:
        return 'Cooling is running with a reporting door or window open. Closing it may reduce energy use.'
    if choice=='away_high' and e['away_high']:
        return 'Your carried device is not reporting home and consumption is unusually high for matching conditions. Others may still be home; check what is running. Nothing was switched off.'
    return 'No energy-saving opportunity is verified in the current readings.'


def render(mode, data, now):
    e = facts(data,now)
    c = cached(data,e,now)
    if mode=='energy': return energy_text(e,c)
    if mode in ('attention','complications'): return attention_text(e,c)
    if mode=='savings': return savings_text(e,c)
    if mode=='diagnostics':
        return ('Laundry telemetry conflicts: time remains despite an idle flag. The completion detector still requires its own verified finish evidence.'
                + (' The model review also flags this conflict.' if c.get('laundry')=='conflict' else '')
                if e['laundry_conflicts'] else 'No fresh laundry telemetry conflict is identified. This does not confirm completion or announcement delivery.')
    if mode=='routine':
        return {'announce':'A verified routine issue merits attention.',
            'combine':'Several routine issues could be combined into one report.',
            'quiet':'No additional routine summary is recommended.'}.get(c.get('routine'),'No confident routine-summary recommendation is available.')+' This is advice only; safety and laundry alerts are unchanged.'
    if mode=='energy_detail':
        if e['load_kw'] is None: return 'Current energy details are unavailable.'
        return f"House load is {e['load_kw']:.1f} kilowatts; the threshold is {e['threshold_kw']:.1f}. "+energy_text(e,c)
    if mode=='detail':
        choice=c.get('detail')
        if choice=='energy': return render('energy_detail',data,now)
        if choice=='laundry': return 'Reporting laundry countdowns: '+', '.join(f'{v:g} minutes' for v in e['remaining_minutes'])+'. Time remaining is not a finish signal.'
        if choice=='openings': return f"{e['openings']} of {e['reporting_openings']} reporting openings or locks are open or unlocked."
        return 'No additional verified detail is available.'
    raise ValueError('Unknown advisory mode')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--mode',choices=('energy','attention','savings','diagnostics','routine','detail'),default='attention')
    args=parser.parse_args()
    now=datetime.now(timezone.utc).timestamp()
    if args.review:
        if ROOT.resolve()!=RUNTIME.resolve(): raise SystemExit('Requires deployed runtime')
        print(json.dumps(refresh(ROOT/'data',now)))
    else:
        print(render(args.mode,ROOT/'data',now))


if __name__=='__main__': main()
