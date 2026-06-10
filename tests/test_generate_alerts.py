#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generate_alerts", ROOT / "scripts" / "generate_alerts.py")
assert SPEC and SPEC.loader
generate_alerts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_alerts)


def base_config() -> dict[str, Any]:
    return {
        "alerts": {
            "alarm_websocket_recent_window": 3,
            "alarm_websocket_min_successes": 2,
            "battery_recharge_check_start_hour": 14,
            "battery_recharge_check_end_hour": 16,
            "battery_alert_mode": "solar_peak_cycle",
            "battery_critical_percent": 10,
            "battery_low_percent": 20,
            "energy_stale_restart_grace_minutes": 10,
            "high_load_kw": 8,
            "warning_recent_window": 3,
            "warning_high_count": 2,
            "sense_live_401_warning_min": 3,
            "alarm_media_sensor_trip_min_events": 10,
            "grid_import_kw": 0.05,
            "grid_export_kw": -0.05,
            "solar_surplus_margin_kw": 0.2,
        },
        "network": {"known_tahoma_office": "192.168.0.164:8443"},
    }


def latest_snapshot() -> dict[str, Any]:
    return {
        "captured_at": "2026-06-10T13:05:00-07:00",
        "homebridge": {
            "launchd": {"state": "running"},
            "logs": {
                "runStartedAt": "2026-06-10T13:00:00-07:00",
                "latestMetrics": {},
                "recentWarnings": [],
                "warningCount": 0,
            },
            "config": {
                "platforms": [
                    {
                        "platform": "Alarmdotcom",
                        "shouldUseWebSockets": True,
                        "deviceAliases": [{"id": "sys-1", "name": "Entry Door Contact"}],
                    }
                ]
            },
            "security": {"homebridgePermissions": {"insecurePaths": []}},
        },
    }


def alarm_com_payload(activity_ok: bool = False) -> dict[str, Any]:
    return {
        "login": {"ok": True},
        "energy": {"ok": True},
        "activity": {"ok": activity_ok, "refreshOk": activity_ok, "status": 500 if not activity_ok else 200},
        "websocketToken": {"ok": True},
        "alarmState": {
            "ok": True,
            "issues": [],
            "systems": [
                {
                    "components": {
                        "sensors": [
                            {"id": "sys-1", "description": "Entry Door", "stateText": "Closed"},
                        ]
                    }
                }
            ],
        },
    }


def latest_characteristics(value: int = 0) -> dict[str, Any]:
    return {
        "k": {
            "accessory": "Entry Door Contact",
            "service": "Entry Door Contact",
            "characteristic": "ContactSensorState",
            "value": value,
            "plugin": "homebridge-node-alarm-dot-com",
        }
    }


def alarm_warning_row() -> dict[str, Any]:
    raw = {
        "homebridge": {
            "config": {"platforms": [{"platform": "Alarmdotcom", "shouldUseWebSockets": True}]},
            "logs": {"recentWarnings": ["[Security System] WebSocket token fetch returned 403, forcing re-authentication..."]},
        }
    }
    return {"raw_json": json.dumps(raw), "alarm_websocket": 0, "warning_count": 1}


class GenerateAlertsTest(unittest.TestCase):
    def patch_module(self, **replacements: Any) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(generate_alerts, name)
            setattr(generate_alerts, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(generate_alerts, name, original)

    def test_restart_grace_suppresses_energy_stale(self) -> None:
        states = generate_alerts.active_state_titles(base_config(), latest_snapshot())
        self.assertNotIn("Energy data stale", states)

    def test_energy_stale_returns_after_restart_grace(self) -> None:
        latest = latest_snapshot()
        latest["captured_at"] = "2026-06-10T13:15:01-07:00"
        states = generate_alerts.active_state_titles(base_config(), latest)
        self.assertIn("Energy data stale", states)

    def test_activity_degraded_uses_activity_tile_not_alarm_tile(self) -> None:
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=False),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        titles = {item["title"] for item in alerts}
        self.assertIn("Alarm.com activity history is degraded", titles)
        self.assertNotIn("Alarm.com Homebridge cache is stale", titles)
        self.assertNotIn("Alarm.com websocket is unreliable", titles)

    def test_clean_portal_state_demotes_alarm_websocket_warning_volume(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["warningCount"] = 3
        rows = [alarm_warning_row(), alarm_warning_row(), alarm_warning_row()]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest, rows)
        titles = {item["title"] for item in alerts}
        self.assertNotIn("Alarm.com websocket is unreliable", titles)
        self.assertNotIn("Recent Homebridge warning volume is high", titles)

    def test_cache_drift_gets_separate_alert(self) -> None:
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=1),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        titles = {item["title"] for item in alerts}
        self.assertIn("Alarm.com Homebridge cache is stale", titles)


if __name__ == "__main__":
    unittest.main()
