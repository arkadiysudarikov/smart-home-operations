"""Deploy the action plugin to the running child Node installation, not a spare copy."""
import argparse
import json
import shutil
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone


def active_root():
    home = Path.home()
    config = json.loads((home/'.homebridge/config.json').read_text())
    platform = next(p for p in config['platforms'] if p.get('platform') == 'SmartHomeActions')
    port = int(platform['_bridge']['port'])
    pids = subprocess.check_output(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],text=True,timeout=5).split()
    if len(pids) != 1:
        raise RuntimeError('Cannot resolve the running actions child')
    command = subprocess.check_output(['/bin/ps','-p',pids[0],'-o','command='],text=True,timeout=5)
    if 'homebridge-smart-home-actions' not in command:
        raise RuntimeError('Actions child identity mismatch')
    files = subprocess.check_output(['/usr/sbin/lsof','-a','-p',pids[0],'-d','txt','-Fn'],text=True,timeout=5).splitlines()
    nodes = [Path(x[1:]) for x in files if x.startswith('n/') and x.endswith('/bin/node')]
    if len(nodes) != 1:
        raise RuntimeError('Cannot resolve the active actions Node installation')
    return nodes[0].parent.parent/'lib/node_modules/homebridge-smart-home-actions'


def install(source, runtime, plugin):
    entries = []
    backup = runtime/'data/backups'/('actions-plugin-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    backup.mkdir(parents=True,exist_ok=True)
    for name in ('index.js','package.json'):
        original = source/'plugins/homebridge-smart-home-actions'/name
        target = plugin/name
        if target.exists():shutil.copy2(target,backup/name)
        temp = target.with_suffix(target.suffix+'.smart-home.tmp')
        shutil.copy2(original,temp);temp.replace(target)
        entries.append({'source':str(original.relative_to(source)), 'destination':str(target)})
    path = runtime/'data/installed_code_manifest.json'
    path.write_text(json.dumps({'files':entries},indent=2)+'\n');path.chmod(0o600)


def reload_child(platform_name):
    if platform_name not in {'SmartHomeActions','HomebridgeDummy'}:
        raise ValueError('Only the two local control bridges may be reloaded')
    config=json.loads((Path.home()/'.homebridge/config.json').read_text())
    bridge=next(p['_bridge']['username'] for p in config['platforms'] if p.get('platform')==platform_name)
    account='1588EThompson'
    password=subprocess.check_output(['/usr/bin/security','find-generic-password','-s','Smart Home Homebridge UI localhost:8581','-a',account,'-w'],text=True,timeout=10).strip()
    request=urllib.request.Request('http://127.0.0.1:8581/api/auth/login',data=json.dumps({'username':account,'password':password}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=10) as response:
        token=json.load(response)['access_token']
    request=urllib.request.Request('http://127.0.0.1:8581/api/server/restart/'+urllib.parse.quote(bridge,safe=''),data=b'',method='PUT',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request,timeout=15) as response:
        response.read()


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--runtime-root',type=Path,required=True)
    p.add_argument('--reload',action='store_true')
    args=p.parse_args();install(args.source_root,args.runtime_root,active_root())
    if args.reload:reload_child('SmartHomeActions')
