"""Forced-command SSH entry point. Never evaluates a client-provided shell command."""
import os
import sys
import urllib.request

MODES = frozenset(("status", "energy", "complications", "discharge", "night", "changes", "explain", "hold"))


def dispatch(command):
    if command not in MODES:
        raise ValueError("Only a Dr. House command name is permitted")
    request = urllib.request.Request("http://127.0.0.1:18765/action/dr-house-" + command, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=125) as response:
        return response.read(16384).decode()


if __name__ == "__main__":
    try:
        print(dispatch(os.environ.get("SSH_ORIGINAL_COMMAND", "")))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
