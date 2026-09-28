"""Shared bounded Jev transport; CLI runs one synthetic laundry diagnostic.

Uses the approved Keychain credential. Reserves one cent per bounded request from
the shared $1/month local allocation before transmission; never retries a request.
No names, household timestamps, device IDs, mail, or calendar content are sent.
"""
import fcntl
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path.home() / 'Library/Application Support/SmartHomeMonitor/data'
REQUEST = {
    'model': 'jev-1.13.0',
    'state': {'synthetic_test': True, 'cycle_active_signal': False,
              'remaining_seconds': 2002, 'countdown_is_decreasing': True},
    'questions': {'interpretation': {'type': 'choice',
        'instructions': 'Interpret this synthetic washer telemetry. A false cycle-active signal conflicts with a positive decreasing countdown. Never infer completion while time remains. Choose review if uncertain. This response is advisory only.',
        'criteria': {'still_running': 'Positive decreasing time remains; completion is not established.',
                     'finished': 'Confirmed completed wash with zero remaining time.',
                     'review': 'Evidence insufficient or conflicting; inspect it, do not announce completion.'}}},
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Redirect refused')


def call_jev(payload):
    body = json.dumps(payload, allow_nan=False).encode()
    # Up to seven structured decisions share one request and one reservation.
    if len(body) > 8192:
        raise RuntimeError('Request size exceeds approved diagnostic bound')
    key = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a', 'smart-home',
                          '-s', 'com.arkadiy.smart-home.typesafe', '-w'], capture_output=True, check=True, timeout=5).stdout.decode().strip()
    with (DATA/'jev_budget.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = DATA/'jev_budget.json'
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        ledger = json.loads(path.read_text()) if path.exists() else {}
        # Retain all month totals so clock rollback cannot reset an allocation.
        used = ledger.get(month, 0)
        if type(used) is not int or used < 0 or used+10000 > 1000000:
            raise RuntimeError('Monthly allocation exhausted or invalid')
        ledger[month] = used+10000
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(ledger))
        temporary.chmod(0o600)
        temporary.replace(path)
        request = urllib.request.Request('https://api.typesafe.ai/v1/systemone', data=body,
                   headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise RuntimeError('Oversized response')
            result = json.loads(raw)
            if result.get('model') != payload['model']:
                raise RuntimeError('Unexpected model')
            return result
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Jev HTTP {error.code}; no retry') from None


def main():
    result = call_jev(REQUEST)
    answer = result.get('answers', {}).get('interpretation', {})
    ok = answer.get('type') == 'choice' and answer.get('choice') in ('still_running', 'review')
    print(json.dumps({'ok': ok, 'syntheticOnly': True, 'model': result.get('model'),
                      'answer': answer, 'usage': result.get('usage'),
                      'controlsOrAnnouncementsExecuted': False}))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
