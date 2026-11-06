"""Forced-command SSH entry point. Never evaluates a client-provided shell command."""
import os
import json
import sys
import subprocess
from pathlib import Path
import urllib.request
import urllib.error

MODES = frozenset(("status", "energy", "complications", "discharge", "night", "changes", "explain", "hold", "leave", "unusual", "departure", "quiet", "morning", "running", "openings", "resume", "washerfree", "more", "repeat", "snooze", "why", "acknowledge", "attention", "savings", "diagnostics", "routine"))


MODES = MODES | {'wait', 'laundrytime', 'recap', 'washer-unloaded', 'dryer-unloaded', 'combo-unloaded'}


def spoken_response(raw):
    """Return the briefing text to Siri instead of transport metadata."""
    payload = json.loads(raw)
    if payload.get("ok") is not True:
        message = payload.get("message")
        raise ValueError(message.strip() if isinstance(message, str) and message.strip() else "The house check failed")
    if "acknowledged" in payload:
        return "Announcements acknowledged."
    if isinstance(payload.get("message"), str) and payload["message"].strip():
        return payload["message"].strip()
    result = payload.get("stdout", "")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            raise ValueError("The house check returned no spoken reply")
    message = result.get("message") if isinstance(result, dict) else None
    if not isinstance(message, str) or not message.strip():
        raise ValueError("The house check returned no spoken reply")
    return message.strip()


def dispatch(command):
    if command not in MODES:
        raise ValueError("Only a Dr. House command name is permitted")
    if command == "diagnostics":
        # Siri owns this reply. A separate HomePod relay outage must not turn
        # a successful read of laundry status into an HTTP 500 on the phone.
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("house_briefing.py")),
             "--mode", "diagnostics"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode or not result.stdout.strip():
            raise ValueError("The laundry status check could not produce a reply")
        return result.stdout.strip()
    endpoint = ("laundry-unloaded?appliance=" + command.removesuffix("-unloaded")
                if command.endswith("-unloaded") else "dr-house-" + command)
    request = urllib.request.Request("http://127.0.0.1:18765/action/" + endpoint, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=125) as response:
            return spoken_response(response.read(16384).decode())
    except urllib.error.HTTPError as error:
        if command.endswith('-unloaded'):
            # The state may be saved even when the Home update failed. Preserve
            # that distinction in Siri's error instead of losing it to HTTP 503.
            return spoken_response(error.read(16384).decode())
        raise


if __name__ == "__main__":
    try:
        print(dispatch(os.environ.get("SSH_ORIGINAL_COMMAND", "")))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
