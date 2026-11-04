"""Wire the existing camera mirror's native commands without replacing automations."""
import argparse
import json
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path


def configure(runtime, homebridge):
    from deploy_actions_plugin import active_root
    plugin = active_root().parent/'homebridge-dummy'
    package = json.loads((plugin/'package.json').read_text())
    implementation = (plugin/'dist/accessory/onoff/onoff.js').read_text()
    if package.get('version') != '2.1.4' or not all(x in implementation for x in (
        'this.executeCommand(this.config.commandOn)', 'this.executeCommand(this.config.commandOff)')):
        raise RuntimeError('Unreviewed Dummy command hooks; configuration unchanged')
    path = homebridge/'config.json'
    config = json.loads(path.read_text())
    platform = next(p for p in config['platforms'] if p.get('platform') == 'HomebridgeDummy')
    switch = next(a for a in platform['accessories'] if a.get('id') == 'smart_home_garage_camera_motion_v1')
    python = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
    helper = runtime/'scripts/garage_camera_event.py'
    if not helper.exists():
        raise RuntimeError('Deploy the camera event helper first')
    expected = {key:shlex.join([str(python),str(helper),value]) for key,value in [('commandOn','on'),('commandOff','off')]}
    for key,value in expected.items():
        if switch.get(key) and switch[key] != value:
            raise RuntimeError('Existing camera command differs; refusing to overwrite it')
    if all(switch.get(k)==v for k,v in expected.items()):
        return {'changed':False}
    backup = homebridge/'codex-backups'/('garage-camera-hooks-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    backup.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup/'config.json')
    switch.update(expected)
    temp = path.with_suffix('.json.tmp');temp.write_text(json.dumps(config,indent=4)+'\n');temp.chmod(0o600);temp.replace(path)
    proof = runtime/'data/garage_camera_hooks.json'
    proof.write_text(json.dumps({'configuredAt':datetime.now(timezone.utc).isoformat(),'nativeCommands':expected,'naturalEdgeObserved':False},indent=2)+'\n');proof.chmod(0o600)
    return {'changed':True,'bridge':platform['_bridge']['username']}


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True)
    p.add_argument('--reload',action='store_true')
    args=p.parse_args();result=configure(args.runtime_root,Path.home()/'.homebridge')
    if args.reload and result['changed']:
        from deploy_actions_plugin import reload_child
        reload_child('HomebridgeDummy')
    print(json.dumps(result))
