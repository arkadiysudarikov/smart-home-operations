"""Local operational evidence for bounded Jev choices. Never export raw logs/rules."""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from additional_announcements import load, save, stamp

OPTIONS = {
    'logs': {'investigate':'Multiple recent fault lines need investigation',
             'watch':'Recent isolated fault needs observation',
             'startup':'Only identified startup disconnect noise', 'unknown':'Insufficient log evidence'},
    'automations': {'conflict':'Opposite actions may overlap',
                    'overlap':'Similar actions may overlap',
                    'none':'No candidate in reviewed coverage', 'unknown':'Insufficient automation evidence'},
}
COUNTS = ('timeouts','authentication','rate_limits','disconnects','startup_disconnects','other_faults')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
STAMP = re.compile(r'^\[([^]]+)\]')


def log_summary(path, now):
    """Read bounded tail, require dated evidence, classify locally; retain no text."""
    result = {key:0 for key in COUNTS}
    result.update(available=False, observed=0, truncated=False)
    try:
        with path.open('rb') as stream:
            size=stream.seek(0,2); stream.seek(max(0,size-2_000_000))
            raw=stream.read(2_000_000).decode(errors='replace').splitlines()
        result['truncated']=size>2_000_000
    except OSError:
        return result
    start=None
    for line in raw:
        line=ANSI.sub('',line); match=STAMP.match(line)
        if not match: continue
        try:
            at=datetime.strptime(match[1],'%m/%d/%Y, %I:%M:%S %p').replace(tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
        except ValueError: continue
        if not 0<=now-at<=900: continue
        result['observed']+=1
        if re.search(r'(Starting child bridge|Launched child bridge|Homebridge .* running)',line,re.I): start=at
        if 'ETIMEDOUT' in line: key='timeouts'
        elif re.search(r'\b401\b|unauthorized|login failed',line,re.I): key='authentication'
        elif re.search(r'\b429\b|too many requests',line,re.I): key='rate_limits'
        elif 'ECONNRESET' in line:
            key='startup_disconnects' if start is not None and 0<=at-start<=90 else 'disconnects'
        elif re.search(r'\berror\b|\bexception\b',line,re.I) and 'adding webhook' not in line.lower(): key='other_faults'
        else: continue
        result[key]=min(1000,result[key]+1)
    result['available']=result['observed']>0
    return result


def automation_summary(rules):
    """Candidates require identical trigger/timeframe/target; never infer intent."""
    groups={}; active=0
    for r in rules[:200] if isinstance(rules,list) else []:
        if not isinstance(r,dict): continue
        if r.get('enabled') is not True: continue
        if not all(isinstance(r.get(k),str) and r[k] for k in ('trigger','target','action','window')): continue
        active+=1
        groups.setdefault((r['trigger'],r['target'],r['window']),[]).append(r['action'])
    overlap=conflict=0
    for actions in groups.values():
        overlap+=max(0,len(actions)-len(set(actions)))
        if {'on','off'}<=set(actions) or {'lock','unlock'}<=set(actions) or {'arm','disarm'}<=set(actions): conflict+=1
    return {'reviewed_actions':active,'overlaps':overlap,'conflicts':conflict}


def alarm_rules(document):
    """Portal descriptions can omit sensor identity: results are candidates only."""
    result=[]
    for r in document.get('automationRules',{}).get('rules',[])[:200]:
        if not isinstance(r,dict): continue
        text=str(r.get('action','')).lower()
        if 'garage light' not in text: continue
        action='off' if 'off' in text else 'on' if 'on' in text or '100%' in text else None
        if action:
            result.append({'enabled':r.get('isPaused') is False,'trigger':r.get('trigger'),
                'window':r.get('timeframe'),'target':'garage_light','action':action})
    return result


def bounded_counts(value, keys):
    if not isinstance(value,dict): return None
    if not all(type(value.get(k)) is int and 0<=value[k]<=1000 for k in keys): return None
    return {k:value[k] for k in keys}


def facts(data,now):
    """Explicit output allowlist rejects arbitrary strings and expired snapshots."""
    record=load(data/'latest_jev_operations.json')
    logs=record.get('logs',{})
    counts=bounded_counts(logs,COUNTS)
    live=0<=now-stamp(record.get('generatedAt'))<=360
    log_facts=counts if live and counts and logs.get('available') is True else None
    reviewed=load(data/'home_automation_review.json')
    home_ok=0<=now-stamp(reviewed.get('reviewedAt'))<=7*86400
    home=automation_summary(reviewed.get('rules',[])) if home_ok else None
    alarm=load(data/'latest_alarm_com.json')
    a=alarm.get('automationRules',{})
    alarm_ok=a.get('ok') is True and 0<=now-stamp(a.get('checkedAt'))<=1800
    adc=automation_summary(alarm_rules(alarm)) if alarm_ok else None
    summaries=[s for s in (home,adc) if s is not None]
    return {'operations_logs':log_facts,
        'operations_automations':{k:sum(s[k] for s in summaries) for k in ('reviewed_actions','overlaps','conflicts')} if summaries else None,
        'operations_coverage':{'home_review':home is not None,'alarm_subset':adc is not None}}


def supported(e):
    logs=e.get('operations_logs'); auto=e.get('operations_automations')
    options={'unknown'}
    if logs is not None:
        faults=sum(logs[k] for k in COUNTS if k!='startup_disconnects')
        if faults>=2: options.add('investigate')
        elif faults: options.add('watch')
        elif logs['startup_disconnects']: options.add('startup')
    automation={'unknown'}
    if auto is not None:
        if auto['conflicts']: automation.add('conflict')
        if auto['overlaps']: automation.add('overlap')
        if not auto['conflicts'] and not auto['overlaps']: automation.add('none')
    return {'logs':options,'automations':automation}


def render(mode,e,c):
    if mode=='logs':
        logs=e.get('operations_logs')
        if logs is None: return 'Fresh Homebridge log evidence is unavailable. No all-clear is established.'
        faults=sum(logs[k] for k in COUNTS if k!='startup_disconnects')
        detail=', '.join(f'{logs[k]} {label}' for k,label in [('timeouts','timeouts'),('authentication','authentication failures'),('rate_limits','rate-limit responses'),('disconnects','disconnects'),('other_faults','other fault lines')] if logs[k])
        prefix='Investigate recent faults. ' if faults>=2 else 'Watch the recent fault. ' if faults else ''
        advice={'investigate':'Jev also recommends investigation. ', 'watch':'Jev recommends observation. ', 'startup':'Jev identifies the startup-adjacent category; this does not establish harmlessness. '}.get(c.get('logs'),'No confident Jev log recommendation is cached. ')
        return advice+prefix+(detail+'. ' if detail else 'No classified ongoing faults in the available fifteen-minute tail. ')+f"{logs['startup_disconnects']} disconnect lines occurred near startup. Counts are log lines, not distinct incidents. Unmatched or missing logs may hide other issues."
    auto=e.get('operations_automations')
    if auto is None: return 'Fresh automation review evidence is unavailable. No conflict-free claim is possible.'
    advice={'conflict':'Jev prioritizes opposing-action candidates. ', 'overlap':'Jev prioritizes similar-action candidates. ', 'none':'Jev found no candidate in the supplied limited coverage. '}.get(c.get('automations'),'No confident Jev automation recommendation is cached. ')
    return (advice+f"Reviewed {auto['reviewed_actions']} action entries in limited coverage: {auto['conflicts']} opposing-action groups and {auto['overlaps']} similar-action overlaps. "
        'These are review candidates; portal descriptions may omit which sensor triggers a rule. No automation was changed. Home review is manual, partial, and expires after seven days; Alarm.com coverage currently includes garage-light rules only.')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--collect',action='store_true')
    parser.add_argument('--publish-host',help='Existing household coordinator SSH host; receives sanitized counts only')
    args=parser.parse_args()
    if not args.collect: parser.error('Use --collect for the local sanitized log snapshot')
    root=Path(__file__).resolve().parents[1]; now=datetime.now(timezone.utc).timestamp()
    report={'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
        'logs':log_summary(Path.home()/'.homebridge/homebridge.log',now)}
    save(root/'data/latest_jev_operations.json',report)
    if args.publish_host:
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}',args.publish_host): parser.error('Invalid host')
        # Static remote program, JSON only on stdin. Never transport raw log data.
        remote="import sys,json,os; from pathlib import Path; p=Path.home()/'Library/Application Support/SmartHomeMonitor/data/latest_jev_operations.json'; d=json.load(sys.stdin); t=p.with_suffix('.incoming'); t.write_text(json.dumps(d)); t.chmod(0o600); os.replace(t,p)"
        import shlex
        subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',args.publish_host,
                        'python3 -c '+shlex.quote(remote)],input=json.dumps(report),text=True,check=True,timeout=25,capture_output=True)
    print(json.dumps(report))


if __name__=='__main__': main()
