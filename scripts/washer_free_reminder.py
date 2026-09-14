"""One-shot request, consumed only by the existing confirmed washer finish event."""
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

PATH = Path.home() / "Library/Application Support/SmartHomeMonitor/data/washer_free_request.json"


def update(arm=False, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(PATH.read_text())
        except (OSError, ValueError):
            state = {}
        requested = state.get("requestedAt")
        valid = isinstance(requested, (float, int)) and not isinstance(requested, bool) and 0 <= now - requested <= 43200
        if arm:
            PATH.write_text(json.dumps({"requestedAt": now}))
            return True
        if state:
            PATH.write_text("{}")
        return valid
