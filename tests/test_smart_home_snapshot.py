#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("smart_home_snapshot", ROOT / "scripts" / "smart_home_snapshot.py")
assert SPEC and SPEC.loader
smart_home_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smart_home_snapshot)


class SmartHomeSnapshotTest(unittest.TestCase):
    def test_runtime_drift_checks_cover_synced_runtime_scripts(self) -> None:
        runtime_scripts = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor" / "scripts"
        if not runtime_scripts.exists():
            self.skipTest("runtime scripts directory is not present")
        missing = []
        for path in (ROOT / "scripts").iterdir():
            if path.suffix not in {".js", ".py", ".sh"}:
                continue
            if (runtime_scripts / path.name).exists() and f"scripts/{path.name}" not in smart_home_snapshot.DRIFT_CHECK_FILES:
                missing.append(f"scripts/{path.name}")
        self.assertEqual(missing, [])

    def test_collect_log_signals_tracks_unifi_updated_and_unchanged_statuses(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            [
                '[6/13/2026, 10:13:06 AM] [homebridge-unifi-occupancy] Updated accessory status: "Level 2 m2-office-mini" active',
                '[6/13/2026, 10:13:06 AM] [homebridge-unifi-occupancy] Updated accessory status: "Level 1 Nintendo Switch" inactive',
                '[6/13/2026, 10:14:06 AM] [homebridge-unifi-occupancy] Accessory status unchanged: "Level 1 m2-garage-mini" active',
            ]
        )

        occupancy = signals["unifiOccupancy"]
        self.assertEqual(occupancy["trackedAccessories"], 3)
        self.assertEqual(occupancy["active"], ["Level 1 m2-garage-mini", "Level 2 m2-office-mini"])
        self.assertEqual(occupancy["inactiveCount"], 1)


if __name__ == "__main__":
    unittest.main()
