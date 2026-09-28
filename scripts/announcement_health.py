#!/usr/bin/env python3
"""Read-only announcement diagnostics. Never sends mail or plays audio."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHED_081 = "87f83a7c7aaa8864fe3f8475c6a3619e4bdcc9916cc9e7895201f712d40f7bfb"
ORIGINAL_081 = "f3418372bc2cbda76b36ae3dabc568669ff5d8cabe94b3796a5e8c2adc89e335"


def load(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def age(value, now):
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (now-at).total_seconds() if at.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def inspect_plugin(root):
    try:
        version = load(root / "package.json").get("version")
        digest = hashlib.sha256((root / "dist/devices/device.js").read_bytes()).hexdigest()
    except OSError:
        return {"status": "unavailable", "ok": False}
    known = version == "0.8.1" and digest == PATCHED_081
    status = "verified" if known else "heartbeat_patch_missing" if version == "0.8.1" and digest == ORIGINAL_081 else "plugin_changed_review_required"
    return {"status": status, "ok": known, "version": version, "sha256": digest}


def active_plugin(port=40893):
    """Resolve the running child, not the newest Node installation on disk."""
    try:
        pids = subprocess.run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True, timeout=5).stdout.split()
        if len(pids) != 1 or not pids[0].isdigit():
            return None
        command = subprocess.run(["/bin/ps", "-p", pids[0], "-o", "command="], capture_output=True, text=True, timeout=5).stdout
        if "homebridge-smarthq" not in command:
            return None
        files = subprocess.run(["/usr/sbin/lsof", "-a", "-p", pids[0], "-d", "txt", "-Fn"], capture_output=True, text=True, timeout=5).stdout.splitlines()
        nodes = [Path(line[1:]) for line in files if line.startswith("n/") and line.endswith("/bin/node")]
        if len(nodes) == 1:
            return nodes[0].parent.parent / "lib/node_modules/@homebridge-plugins/homebridge-smarthq"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def build(data, plugin, now):
    laundry = load(data / "latest_smarthq_laundry_state.json")
    captured_age = age(laundry.get("capturedAt"), now)
    sources = {}
    for name in ("washer", "dryer", "combo"):
        device = (laundry.get("devices") or {}).get(name) or {}
        seconds = age(device.get("apiLastSuccessAt"), now)
        fresh = laundry.get("ok") is True and captured_age is not None and 0 <= captured_age <= 600 and seconds is not None and 0 <= seconds <= 300
        active = bool(device.get("inUse") or device.get("cycleActive"))
        sources[name] = {"status": "fresh" if fresh else "stale_or_missing", "active": active, "heartbeatAgeSeconds": seconds}
    latest = None
    malformed = False
    path = data / "homepod_announcement_events.jsonl"
    try:
        # Bound read cost. Discard a partial first record when reading a tail.
        with path.open("rb") as handle:
            handle.seek(0, 2)
            start = max(0, handle.tell()-65536)
            handle.seek(start)
            if start: handle.readline()
            lines = handle.read().decode("utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip(): continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict): raise ValueError()
                latest = value
                break
            except ValueError:
                malformed = True
                break  # Do not hide a corrupt latest record behind an older success.
    except (OSError, UnicodeError):
        pass
    relay = {"status": "invalid_log" if malformed else "no_attempt_recorded"}
    if latest:
        relay = {"status": "skipped" if latest.get("skipped") else "accepted_not_delivered" if latest.get("ok") is True else "failed",
                 "at": latest.get("at"), "announcementId": latest.get("announcementId"),
                 "ageSeconds": age(latest.get("at"), now)}
    faults = []
    if laundry.get("ok") is not True or captured_age is None or not 0 <= captured_age <= 600:
        faults.append("Laundry capture stale or missing")
    if plugin.get("ok") is not True: faults.append("SmartHQ instrumentation needs review")
    if any(s["active"] and s["status"] != "fresh" for s in sources.values()): faults.append("Active laundry source stale")
    if relay["status"] in ("failed", "invalid_log"): faults.append("Announcement relay failed or log invalid")
    return {"generatedAt": now.isoformat(), "ok": not faults, "faults": faults,
            "smartHQPlugin": plugin, "laundrySources": sources, "relay": relay,
            "audibleDelivery": {"status": "unverified", "reason": "No automatic HomePod playback receipt; relay acceptance is not audible proof."}}


def main():
    config = load(ROOT / "config/sources.json")
    root = active_plugin(int((config.get("smarthq_laundry_recovery") or {}).get("child_bridge_port", 40893)))
    plugin = inspect_plugin(root) if root else {"status": "running_plugin_unresolved", "ok": False}
    payload = build(ROOT / "data", plugin, datetime.now(timezone.utc))
    target = ROOT / "data/latest_announcement_health.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2)+"\n")
    tmp.replace(target)
    report = ROOT / "reports/announcement_health.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Announcement health\n\n```json\n"+json.dumps(payload, indent=2)+"\n```\n")
    print(json.dumps(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
