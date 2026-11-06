"""Compatibility entry point for the Mac's existing restricted Shortcuts key.

Preserve acknowledgment and add only the explicitly requested Codex handoff.
Other Dr. House commands and arbitrary shell programs remain disallowed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


def dispatch(command):
    if command == 'codex-status' or command.startswith('codex-request '):
        script = Path.home()/'Library/Application Support/SmartHomeMonitor/scripts/dr_house_ssh.py'
        result = subprocess.run([sys.executable, '-I', str(script)],
                                env={**os.environ, 'SSH_ORIGINAL_COMMAND':command},
                                input='', capture_output=True, text=True, timeout=20, check=False)
        if result.returncode or not result.stdout.strip():
            raise ValueError('The Codex handoff failed. Check the request and the Mac.')
        return result.stdout.strip()[:2500]
    if command != 'acknowledge':
        raise ValueError('Only acknowledgment or a Codex handoff is permitted')
    request = urllib.request.Request('http://127.0.0.1:18765/action/dr-house-acknowledge',data=b'',method='POST')
    with urllib.request.urlopen(request,timeout=15) as response:
        result = json.loads(response.read(16384).decode())
    if result.get('ok') is not True:
        raise ValueError('Could not clear announcements')
    return 'Announcements cleared. Repeats stopped.'


if __name__ == '__main__':
    try:
        print(dispatch(os.environ.get('SSH_ORIGINAL_COMMAND','')))
    except Exception as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
