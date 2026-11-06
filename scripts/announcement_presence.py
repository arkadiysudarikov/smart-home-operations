"""Only speak at home with a current, explicit owner-presence reading."""
import json
from datetime import datetime, timezone


def allowed(directory, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    try:
        status = json.loads((directory / 'latest_display_awake.json').read_text())
        checked = datetime.fromisoformat(status['generatedAt'].replace('Z', '+00:00')).timestamp()
        return (0 <= now - checked <= 180
                and status['presence']['homePresent'] is True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
