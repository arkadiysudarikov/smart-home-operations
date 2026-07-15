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
    def test_homebridge_advertisements_detect_wrong_resolved_ip(self) -> None:
        config = {
            "bridge": {"name": "Main", "username": "0E:DE:C1:97:D1:CA", "port": 51179},
            "mdns": {"interface": "192.168.0.69"},
            "platforms": [
                {
                    "name": "Dummy",
                    "_bridge": {"name": "Dummy", "username": "0E:F8:15:C2:EC:AC", "port": 42864},
                }
            ],
        }

        result = smart_home_snapshot.collect_homebridge_advertisements(
            config,
            active_addresses=["192.168.0.69"],
            resolver=lambda hostname: ["192.168.0.172"],
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["mismatchCount"], 2)
        self.assertEqual(result["bridges"][0]["hostname"], "0E_DE_C1_97_D1_CA.local")

    def test_homebridge_advertisements_detect_inactive_configured_ip(self) -> None:
        config = {
            "bridge": {"name": "Main", "username": "0E:DE:C1:97:D1:CA", "port": 51179},
            "mdns": {"interface": "192.168.0.69"},
            "platforms": [],
        }

        result = smart_home_snapshot.collect_homebridge_advertisements(
            config,
            active_addresses=["192.168.0.70"],
            resolver=lambda hostname: ["192.168.0.69"],
        )

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["mismatchCount"], 1)
        self.assertEqual(result["mismatches"][0]["reason"], "configured IP is not active on this Mac")

    def test_homebridge_advertisements_detect_mixed_current_and_stale_ips(self) -> None:
        config = {
            "bridge": {"name": "Main", "username": "0E:DE:C1:97:D1:CA", "port": 51179},
            "mdns": {"interface": "192.168.0.69"},
            "platforms": [],
        }

        result = smart_home_snapshot.collect_homebridge_advertisements(
            config,
            active_addresses=["192.168.0.69"],
            resolver=lambda hostname: ["192.168.0.69", "192.168.0.172"],
        )

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["mismatchCount"], 1)

    def test_unresolved_homebridge_advertisement_is_not_reported_as_wrong_ip(self) -> None:
        config = {
            "bridge": {"name": "Main", "username": "0E:DE:C1:97:D1:CA", "port": 51179},
            "mdns": {"interface": "192.168.0.69"},
            "platforms": [],
        }

        result = smart_home_snapshot.collect_homebridge_advertisements(
            config,
            active_addresses=["192.168.0.69"],
            resolver=lambda hostname: [],
        )

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["mismatchCount"], 0)

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

    def test_collect_home_events_ignores_envoy_seven_day_energy_counters(self) -> None:
        events = smart_home_snapshot.collect_home_events(
            [
                "[6/17/2026, 3:11:58 PM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Power And Energy, Consumption Total, energy last seven days: 25684.457725 kWh",
                "[6/17/2026, 3:11:58 PM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Power And Energy, Consumption Total, energy lifetime: 25684.457109000003 kWh",
            ],
            limit=10,
        )

        self.assertEqual(len(events), 1)
        self.assertIn("energy lifetime", events[0]["message"])

    def test_collect_log_signals_collapses_envoy_warning_bursts(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            [
                "[6/17/2026, 9:57:00 AM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Impulse generator: Error: Update inventory error: AxiosError: timeout of 60000ms exceeded",
                "[6/17/2026, 9:57:01 AM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Impulse generator: Error: Update detailed devices data error: Error: connect ECONNREFUSED 192.168.1.71:443",
                "[6/17/2026, 9:57:02 AM] [Sense Energy Meter] Error event on sense: Unexpected server response: 401.",
            ]
        )

        warnings = signals["recentWarnings"]
        self.assertEqual(signals["rawWarningCount"], 3)
        self.assertEqual(signals["warningCount"], 2)
        self.assertTrue(any("Collapsed 2 Envoy warning lines" in item for item in warnings))

    def test_collect_log_signals_detects_battery_charging(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            [
                "[7/12/2026, 11:37:53 AM] [Enphase Envoy] Live Data, Encharge, backup energy: 1.25 kW",
                "[7/12/2026, 11:38:26 AM] [Enphase Envoy] Live Data, Encharge, backup energy: 1.30 kW",
                "[7/12/2026, 11:38:55 AM] [Enphase Envoy] Live Data, Encharge, backup energy: 1.35 kW",
            ]
        )

        self.assertTrue(signals["latestMetrics"]["enphase_battery_charging"])
        self.assertFalse(signals["latestMetrics"]["enphase_battery_discharging"])

    def test_collect_log_signals_detects_battery_discharging(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            [
                "[7/12/2026, 1:22:07 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.65 kW",
                "[7/12/2026, 1:26:49 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.45 kW",
                "[7/12/2026, 1:27:19 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.40 kW",
            ]
        )

        self.assertFalse(signals["latestMetrics"]["enphase_battery_charging"])
        self.assertTrue(signals["latestMetrics"]["enphase_battery_discharging"])

    def test_storage_power_provides_immediate_battery_direction(self) -> None:
        charging = smart_home_snapshot.collect_log_signals(
            [
                "[7/12/2026, 1:38:04 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.10 kW",
                "[7/12/2026, 1:38:35 PM] [Enphase Envoy] Meter: Storage, power: -0.592834 kW",
                "[7/12/2026, 1:39:04 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.10 kW",
            ]
        )
        discharging = smart_home_snapshot.collect_log_signals(
            [
                "[7/12/2026, 1:26:49 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.45 kW",
                "[7/12/2026, 1:27:20 PM] [Enphase Envoy] Meter: Storage, power: 2.822243 kW",
                "[7/12/2026, 1:27:19 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.40 kW",
            ]
        )

        self.assertTrue(charging["latestMetrics"]["enphase_battery_charging"])
        self.assertFalse(charging["latestMetrics"]["enphase_battery_discharging"])
        self.assertFalse(discharging["latestMetrics"]["enphase_battery_charging"])
        self.assertTrue(discharging["latestMetrics"]["enphase_battery_discharging"])

    def test_storage_power_deadband_treats_idle_noise_as_neither_direction(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            ["[7/12/2026, 1:37:32 PM] [Enphase Envoy] Meter: Storage, power: -0.022722 kW"]
        )

        self.assertFalse(signals["latestMetrics"]["enphase_battery_charging"])
        self.assertFalse(signals["latestMetrics"]["enphase_battery_discharging"])

    def test_collect_log_signals_rejects_mixed_battery_direction(self) -> None:
        signals = smart_home_snapshot.collect_log_signals(
            [
                "[7/12/2026, 1:22:07 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.40 kW",
                "[7/12/2026, 1:22:37 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.45 kW",
                "[7/12/2026, 1:23:07 PM] [Enphase Envoy] Live Data, Encharge, backup energy: 3.40 kW",
            ]
        )

        self.assertFalse(signals["latestMetrics"]["enphase_battery_charging"])
        self.assertFalse(signals["latestMetrics"]["enphase_battery_discharging"])


if __name__ == "__main__":
    unittest.main()
