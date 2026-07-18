#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("display_awake_policy_guard", ROOT / "scripts" / "display_awake_policy_guard.py")
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def valid_config() -> dict:
    targets = []
    for target_id, fields in guard.REQUIRED_TARGETS.items():
        targets.append({"id": target_id, **fields})
    return {
        "display_awake": {
            "default_mode": "shadow",
            "unifi_observation_cache_seconds": 120,
            "access_point_rooms": dict(guard.REQUIRED_AP_ZONES),
            "targets": targets,
        }
    }


class DisplayAwakePolicyGuardTest(unittest.TestCase):
    def test_valid_policy_has_no_violations(self) -> None:
        self.assertEqual(guard.policy_violations(valid_config()), [])

    def test_missing_mapping_and_macbook_policy_are_detected(self) -> None:
        config = valid_config()
        del config["display_awake"]["access_point_rooms"]["Express"]
        macbook = next(item for item in config["display_awake"]["targets"] if item["id"] == "m2-macbook-pro")
        del macbook["dynamic_zone_source"]

        violations = guard.policy_violations(config)

        self.assertIn("ap_mapping:Express", violations)
        self.assertIn("target_policy:m2-macbook-pro:dynamic_zone_source", violations)

    def test_baseline_and_runtime_check_detect_code_and_dashboard_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            runtime = Path(tmp) / "runtime"
            for base in (root, runtime):
                (base / "config").mkdir(parents=True)
                (base / "scripts").mkdir(parents=True)
                (base / "config" / "sources.json").write_text(json.dumps(valid_config()))
                (base / "scripts" / "display_awake_manager.py").write_text("manager\n")
                (base / "scripts" / "action_server.py").write_text("\n".join(guard.ACTION_SERVER_MARKERS))
            baseline = guard.establish_baseline(root, runtime)
            self.assertTrue(baseline["ok"])
            self.assertTrue(guard.check_runtime(runtime)["ok"])

            (runtime / "scripts" / "display_awake_manager.py").write_text("reverted\n")
            (runtime / "scripts" / "action_server.py").write_text("old dashboard\n")
            result = guard.check_runtime(runtime)

        self.assertFalse(result["ok"])
        self.assertIn("manager_code_drift", result["violations"])
        self.assertTrue(any(value.startswith("dashboard_marker:") for value in result["violations"]))


if __name__ == "__main__":
    unittest.main()
