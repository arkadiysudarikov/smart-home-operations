"""Forced-command SSH entry point. Never evaluates a client-provided shell command."""
import os
import json
import sys
import urllib.request

MODES = frozenset(("status", "energy", "complications", "discharge", "night", "changes", "explain", "hold", "leave", "unusual", "departure", "quiet", "morning", "running", "openings", "resume", "washerfree", "more", "repeat", "snooze", "why", "acknowledge", "attention", "savings", "diagnostics", "routine"))


MODES = MODES | {'wait', 'laundrytime', 'recap'}


def spoken_response(raw):
    """Return the briefing text to Siri instead of transport metadata."""
    payload = json.loads(raw)
    if payload.get("ok") is not True:
        raise ValueError("The house check failed")
    if "acknowledged" in payload:
        return "Announcements acknowledged."
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
    request = urllib.request.Request("http://127.0.0.1:18765/action/dr-house-" + command, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=125) as response:
        return spoken_response(response.read(16384).decode())


if __name__ == "__main__":
    try:
        print(dispatch(os.environ.get("SSH_ORIGINAL_COMMAND", "")))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
