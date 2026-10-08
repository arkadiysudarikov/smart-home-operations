#!/usr/bin/env python3
"""Run Homebridge-side laundry capture/recovery on its verified SSH host.

The coordinator retains notification state and the existing Intercom relay.
Never convert SSH failure or an old remote capture into fresh laundry evidence.
"""
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)


def validate(payload, expected, now):
    if payload.get('host') != expected:
        raise ValueError('Remote laundry host identity mismatch')
    capture = payload.get('capture') or {}
    at = datetime.fromisoformat(capture['capturedAt'].replace('Z', '+00:00'))
    if not at.tzinfo or not 0 <= (now-at).total_seconds() <= 120:
        raise ValueError('Remote laundry capture is stale or future-dated')
    if capture.get('ok') is not True:
        raise ValueError('Remote laundry capture failed')
    return payload


def worker():
    from announcement_health import active_plugin, inspect_plugin
    from recover_smarthq_laundry import node_path
    states = json.load(sys.stdin)
    for appliance in ('washer', 'dryer', 'combo'):
        save(ROOT/'data'/f'{appliance}_notifier_state.json', states.get(appliance, {}))
    node = node_path()
    if not node:
        raise RuntimeError('Remote Node runtime missing')
    subprocess.run([node, str(ROOT/'scripts/capture_smarthq_laundry_state.js')],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    subprocess.run([sys.executable, str(ROOT/'scripts/recover_smarthq_laundry.py')],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False)
    plugin = active_plugin()
    host = subprocess.check_output(['/usr/sbin/scutil', '--get', 'LocalHostName'], text=True).strip()
    print(json.dumps({'host': host, 'capture': load(ROOT/'data/latest_smarthq_laundry_state.json'),
                     'recovery': load(ROOT/'data/latest_smarthq_laundry_recovery.json'),
                     'plugin': inspect_plugin(plugin) if plugin else {'ok':False,'status':'running_plugin_unresolved'}}))


def main():
    if '--worker' in sys.argv:
        worker()
        return 0
    config = load(ROOT/'config/sources.json').get('laundry_host') or {}
    if config.get('enabled') is not True:
        raise RuntimeError('Remote laundry host is not enabled')
    data = ROOT/'data'
    states = {name: load(data/f'{name}_notifier_state.json') for name in ('washer','dryer','combo')}
    command = '/usr/bin/python3 ' + shlex.quote(config['runtime']+'/scripts/remote_laundry.py') + ' --worker'
    try:
        result = subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',
                                 config['host'],command], input=json.dumps(states),
                                text=True,capture_output=True,timeout=160,check=True)
        payload = validate(json.loads(result.stdout), config['identity'], datetime.now(timezone.utc))
        save(data/'latest_smarthq_laundry_state.json', payload['capture'])
        save(data/'latest_smarthq_laundry_recovery.json', {**payload['recovery'], 'executionHost':payload['host']})
        save(data/'latest_remote_laundry.json', {**payload, 'ok':True})
        print(json.dumps({'ok':True,'host':payload['host'],'captureAt':payload['capture']['capturedAt'],
                          'recovery':payload['recovery'].get('classification')}))
        return 0
    except Exception as exc:
        # Invalidate rather than silently using the previous host's capture.
        save(data/'latest_smarthq_laundry_state.json', {'ok':False,'error':'Remote laundry capture unavailable','devices':{}})
        save(data/'latest_remote_laundry.json', {'ok':False,'error':type(exc).__name__})
        print(json.dumps({'ok':False,'error':type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
