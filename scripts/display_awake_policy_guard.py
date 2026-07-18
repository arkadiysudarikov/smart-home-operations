#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
GUARD_DIR_NAME = "display_awake_guard"
STATUS_NAME = "latest_display_awake_guard.json"

REQUIRED_AP_ZONES = {
    "1588EThompson": "level_1",
    "Level 1": "level_1",
    "Level 2": "level_2",
    "Level 3": "level_3",
    "Express": "level_3",
    "Extender": "level_2",
}
REQUIRED_TARGETS = {
    "m2-office-mini": {"zone": "level_2", "local": True},
    "m2-garage-mini": {"zone": "level_1", "host": "m2-garage-mini.localdomain"},
    "m4-bar-mini": {"zone": "level_3"},
    "m4-office-mini": {"zone": "level_2", "host": "m4-office-mini.localdomain"},
    "m2-macbook-pro": {
        "dynamic_room": True,
        "dynamic_zone_source": "presence",
        "optional": True,
        "ac_only": True,
        "require_lid_open": True,
    },
}
ACTION_SERVER_MARKERS = (
    "Today’s event history",
    "def display_history(",
    "follows your presence",
    "Arkadiy's iPhone",
)


def local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def policy_projection(config: dict[str, Any]) -> dict[str, Any]:
    display = config.get("display_awake") if isinstance(config.get("display_awake"), dict) else {}
    targets = {
        str(item.get("id")): item
        for item in display.get("targets", [])
        if isinstance(item, dict) and item.get("id")
    }
    projected_targets: dict[str, dict[str, Any]] = {}
    for target_id, required in REQUIRED_TARGETS.items():
        item = targets.get(target_id) or {}
        projected_targets[target_id] = {key: item.get(key) for key in required}
    mappings = display.get("access_point_rooms") if isinstance(display.get("access_point_rooms"), dict) else {}
    return {
        "default_mode": display.get("default_mode"),
        "unifi_observation_cache_seconds": display.get("unifi_observation_cache_seconds"),
        "access_point_rooms": {key: mappings.get(key) for key in REQUIRED_AP_ZONES},
        "targets": projected_targets,
    }


def policy_violations(config: dict[str, Any]) -> list[str]:
    projection = policy_projection(config)
    violations: list[str] = []
    if projection.get("default_mode") != "shadow":
        violations.append("default_mode_not_shadow")
    if projection.get("unifi_observation_cache_seconds") != 120:
        violations.append("unifi_cache_missing")
    for alias, expected in REQUIRED_AP_ZONES.items():
        if (projection.get("access_point_rooms") or {}).get(alias) != expected:
            violations.append(f"ap_mapping:{alias}")
    for target_id, expected_fields in REQUIRED_TARGETS.items():
        actual = (projection.get("targets") or {}).get(target_id) or {}
        for key, expected in expected_fields.items():
            if actual.get(key) != expected:
                violations.append(f"target_policy:{target_id}:{key}")
    return violations


def source_violations(root: Path) -> list[str]:
    violations = policy_violations(read_json(root / "config" / "sources.json"))
    manager = root / "scripts" / "display_awake_manager.py"
    action_server = root / "scripts" / "action_server.py"
    if not manager.exists():
        violations.append("manager_missing")
    try:
        action_text = action_server.read_text()
    except OSError:
        action_text = ""
    for marker in ACTION_SERVER_MARKERS:
        if marker not in action_text:
            violations.append(f"dashboard_marker:{marker}")
    return violations


def establish_baseline(source_root: Path, runtime_root: Path) -> dict[str, Any]:
    violations = source_violations(source_root)
    if violations:
        return {"ok": False, "status": "invalid_source", "violations": violations}
    guard_dir = runtime_root / "data" / GUARD_DIR_NAME
    guard_dir.mkdir(parents=True, exist_ok=True)
    manager = source_root / "scripts" / "display_awake_manager.py"
    protected_manager = guard_dir / "display_awake_manager.py"
    shutil.copy2(manager, protected_manager)
    os.chmod(protected_manager, 0o600)
    baseline = {
        "version": 1,
        "createdAt": local_iso(),
        "policy": policy_projection(read_json(source_root / "config" / "sources.json")),
        "managerSha256": file_sha256(manager),
        "actionServerMarkers": list(ACTION_SERVER_MARKERS),
    }
    write_private_json(guard_dir / "baseline.json", baseline)
    return {"ok": True, "status": "baseline_created", "violations": []}


def check_runtime(runtime_root: Path) -> dict[str, Any]:
    data_dir = runtime_root / "data"
    baseline = read_json(data_dir / GUARD_DIR_NAME / "baseline.json")
    violations: list[str] = []
    if not baseline:
        violations.append("baseline_missing")
    violations.extend(policy_violations(read_json(runtime_root / "config" / "sources.json")))
    expected_hash = baseline.get("managerSha256")
    if expected_hash and file_sha256(runtime_root / "scripts" / "display_awake_manager.py") != expected_hash:
        violations.append("manager_code_drift")
    try:
        action_text = (runtime_root / "scripts" / "action_server.py").read_text()
    except OSError:
        action_text = ""
    for marker in baseline.get("actionServerMarkers") or ACTION_SERVER_MARKERS:
        if marker not in action_text:
            violations.append(f"dashboard_marker:{marker}")
    result = {
        "ok": not violations,
        "status": "healthy" if not violations else "drift_detected",
        "generatedAt": local_iso(),
        "violations": sorted(set(violations)),
    }
    write_private_json(data_dir / STATUS_NAME, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect display-awake policy or runtime drift.")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--validate-source", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    if args.validate_source:
        violations = source_violations(args.source_root)
        result = {"ok": not violations, "status": "valid" if not violations else "invalid", "violations": violations}
    elif args.baseline:
        result = establish_baseline(args.source_root, args.runtime_root)
    else:
        result = check_runtime(args.runtime_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
