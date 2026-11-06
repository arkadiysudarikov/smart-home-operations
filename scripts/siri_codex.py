"""Local Siri-to-Codex handoff. Dictation is stdin data, never shell source.

No listener, remote key, permission expansion, or automatic approval is installed.
"""
import argparse
import json
import os
import subprocess
import sys
import uuid
import fcntl
from datetime import datetime,timezone
from pathlib import Path

JOBS = Path.home()/'Library/Application Support/SmartHomeMonitor/data/codex_voice_jobs'
WORKSPACE = Path.home()/'Documents/Codex Voice Tasks'
CODEX = Path.home()/'.local/bin/codex'


def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.chmod(0o600);tmp.replace(path)


def command(workspace,output):
    return [str(CODEX),'-a','on-request','exec','--sandbox','workspace-write','--skip-git-repo-check',
            '-C',str(workspace),'--json','--output-last-message',str(output),'-']


def normalize(text):
    text=text.strip()
    if not text or len(text)>8000 or '\x00' in text:
        raise ValueError('Please dictate a request between 1 and 8000 characters.')
    return text


def start(text,*,spawn=subprocess.Popen):
    text=normalize(text)
    JOBS.mkdir(parents=True,exist_ok=True);JOBS.chmod(0o700)
    WORKSPACE.mkdir(parents=True,exist_ok=True)
    active=0
    for path in list(JOBS.glob('*/job.json'))[-100:]:
        try:
            row=json.loads(path.read_text())
            if row.get('state') in ('queued','running'):
                try:os.kill(row.get('pid',-1),0)
                except (OSError,TypeError):continue
                active+=1
        except (OSError,ValueError):continue
    if active>=3:
        return 'Three Siri Codex requests are already running. Check Codex before starting another.'
    job=JOBS/uuid.uuid4().hex;job.mkdir(mode=0o700)
    prompt=job/'request.txt';prompt.write_text(text);prompt.chmod(0o600)
    write(job/'job.json',{'state':'queued','createdAt':datetime.now(timezone.utc).isoformat()})
    with (job/'worker.log').open('w') as log,(job/'start.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        os.chmod(job/'worker.log',0o600)
        process=spawn([sys.executable,str(Path(__file__).resolve()),'--run',str(job)],
                      stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        write(job/'job.json',{'state':'queued','pid':process.pid,'createdAt':datetime.now(timezone.utc).isoformat()})
    return 'I started your Codex request on this Mac. Say Codex Request Status to check it. Requests needing approval will stop for review.'


def run(job):
    job=job.resolve()
    if job.parent != JOBS.resolve():raise ValueError('Invalid job path')
    request=normalize((job/'request.txt').read_text())
    state={'state':'running','pid':os.getpid(),'startedAt':datetime.now(timezone.utc).isoformat()}
    with (job/'start.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        write(job/'job.json',state)
    try:
        with (job/'events.jsonl').open('w') as events,(job/'errors.log').open('w') as errors:
            os.chmod(job/'events.jsonl',0o600);os.chmod(job/'errors.log',0o600)
            result=subprocess.run(command(WORKSPACE,job/'result.txt'),input=request,text=True,
                                  stdout=events,stderr=errors,timeout=1800,check=False)
        state['exitCode']=result.returncode;state['state']='finished' if result.returncode==0 else 'needs_review'
        for line in (job/'events.jsonl').read_text().splitlines():
            try:
                event=json.loads(line)
                if event.get('type')=='thread.started':state['threadId']=event.get('thread_id')
            except ValueError:continue
        if (job/'result.txt').exists():os.chmod(job/'result.txt',0o600)
    except (OSError,subprocess.TimeoutExpired) as error:
        state.update(state='needs_review',errorType=type(error).__name__)
    state['finishedAt']=datetime.now(timezone.utc).isoformat();write(job/'job.json',state)


def status():
    paths=sorted(JOBS.glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not paths:return 'No Siri Codex requests have been started on this Mac.'
    path=paths[0];row=json.loads(path.read_text())
    if row.get('state')=='finished':
        result=path.with_name('result.txt')
        return ('Codex finished. '+result.read_text()[:2000]) if result.exists() else 'Codex finished; check its local job record.'
    if row.get('state')=='needs_review':return 'The Codex request needs review. Check the local job record; it was not automatically approved.'
    try:os.kill(row.get('pid',-1),0)
    except (OSError,TypeError):return 'The Codex worker stopped unexpectedly. Check the local job record.'
    return 'Your Codex request is still running.'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path);parser.add_argument('--status',action='store_true');args=parser.parse_args()
    if args.run:run(args.run)
    elif args.status:print(status())
    else:
        try:print(start(sys.stdin.read(8002)))
        except ValueError as error:print(str(error));return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
