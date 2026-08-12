#!/usr/bin/env python3
"""Synchronize only the approved display-awake policy into the runtime."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid object in {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    temporary.replace(path)


def sync_policy(source_root: Path, runtime_root: Path) -> None:
    source_config = read_json(source_root / "config" / "sources.json")
    display_policy = source_config.get("display_awake")
    if not isinstance(display_policy, dict):
        raise RuntimeError("source display_awake policy is missing")
    runtime_config_path = runtime_root / "config" / "sources.json"
    runtime_config = read_json(runtime_config_path)
    runtime_config["display_awake"] = display_policy
    write_json(runtime_config_path, runtime_config)

    source_guard = source_root / "scripts" / "display_awake_policy_guard.py"
    if not source_guard.exists():
        raise RuntimeError("source display policy guard is missing")
    for destination in (
        runtime_root / "scripts" / "display_awake_policy_guard.py",
        runtime_root / "data" / "display_awake_policy_guard.py",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_guard, destination)
        os.chmod(destination, 0o700)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize display-awake policy without replacing other runtime config.")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-root", type=Path, default=Path.home() / "Library" / "Application Support" / "SmartHomeMonitor")
    args = parser.parse_args()
    sync_policy(args.source_root, args.runtime_root)
    print(json.dumps({"ok": True, "status": "display_policy_synced"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
