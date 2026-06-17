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


def sense_auth_warning_row() -> dict[str, Any]:
    raw = {
        "homebridge": {
            "logs": {"recentWarnings": ["[Sense Energy Meter] Error event on sense: Unexpected server response: 401."]},
        }
    }
    return {"raw_json": json.dumps(raw), "alarm_websocket": 1, "warning_count": 1}


def envoy_warning_row() -> dict[str, Any]:
    raw = {
        "homebridge": {
            "logs": {
                "recentWarnings": [
                    "[Enphase Envoy] Collapsed 37 Envoy warning lines (ECONNREFUSED, timeout; operations: home, inventory). Example: [6/17/2026, 9:50:06 AM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Impulse generator: Error: Update meters error: Error: connect ECONNREFUSED 192.168.1.71:443"
                ],
            },
        }
    }
    return {"raw_json": json.dumps(raw), "alarm_websocket": 1, "warning_count": 1}


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

    def test_unifi_auth_alert_requires_unifi_401_line(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["recentWarnings"] = [
            '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 502 - {"error":{"code":502,"message":"Bad Gateway"}}',
            "[Sense Energy Meter] Error event on sense: Unexpected server response: 401.",
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest, [])
        titles = {item["title"] for item in alerts}
        self.assertNotIn("UniFi occupancy authentication is failing", titles)

    def test_unifi_api_alert_handles_gateway_timeout_without_auth_label(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["recentWarnings"] = [
            '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 504 - {"error":{"code":504,"message":"Gateway Timeout"}}',
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest, [])
        titles = {item["title"] for item in alerts}
        self.assertIn("UniFi occupancy API is failing", titles)
        self.assertNotIn("UniFi occupancy authentication is failing", titles)

    def test_unifi_auth_alert_takes_precedence_over_api_timeout(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["recentWarnings"] = [
            "[homebridge-unifi-occupancy] ERROR: Failed to load clients: StatusCodeError: 401 - Unauthorized",
            '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 504 - {"error":{"code":504,"message":"Gateway Timeout"}}',
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest, [])
        titles = {item["title"] for item in alerts}
        self.assertIn("UniFi occupancy authentication is failing", titles)
        self.assertNotIn("UniFi occupancy API is failing", titles)

    def test_envoy_local_comm_gets_dedicated_alert_without_high_warning_flood(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["warningCount"] = 1
        latest["homebridge"]["logs"]["recentWarnings"] = [
            "[Enphase Envoy] Collapsed 37 Envoy warning lines (ECONNREFUSED, timeout; operations: home, inventory). Example: [6/17/2026, 9:50:06 AM] [Enphase Envoy] Device: 192.168.1.71 Envoy, Impulse generator: Error: Update meters error: Error: connect ECONNREFUSED 192.168.1.71:443",
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )

        alerts = generate_alerts.build_alerts(base_config(), latest, [envoy_warning_row(), envoy_warning_row(), envoy_warning_row()])
        titles = {item["title"] for item in alerts}
        envoy_alert = next(item for item in alerts if item["title"] == "Enphase Envoy local communication is degraded")

        self.assertIn("Enphase Envoy local communication is degraded", titles)
        self.assertNotIn("Recent Homebridge warning volume is high", titles)
        self.assertIn("`37` Envoy local update failures", envoy_alert["detail"])

    def test_sideyard_gate_media_validation_alert_names_open_edge(self) -> None:
        alarm = alarm_com_payload(activity_ok=True)
        alarm["gateValidation"] = {
            "status": "trip_seen_no_sideyard_media_seen",
            "latestSideyardTripAt": "2026-06-10 14:53:30",
            "device": {"state": "Open"},
            "videoRule": {"action": "Record video from 2 cameras"},
            "diagnosis": (
                "Sideyard Gate is currently Open; another open test will not create "
                "a fresh Alarm.com open event until Alarm.com first sees it Closed."
            ),
        }
        self.patch_module(
            load_alarm_com=lambda: alarm,
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        alert = next(item for item in alerts if item["title"] == "Alarm.com Sideyard Gate media validation failed")
        self.assertIn("current portal state: `Open`", alert["detail"])
        self.assertIn("Close the Sideyard Gate", generate_alerts.recommended_action(alert) or "")

    def test_alarm_com_trouble_conditions_alert_names_camera_trouble(self) -> None:
        alarm = alarm_com_payload(activity_ok=True)
        alarm["troubleConditions"] = {
            "ok": True,
            "rows": [
                {
                    "description": "Video Device - Not Responding (Sideyard)",
                    "emberDeviceId": "104430779-2052",
                    "macAddress": "504074958A80",
                }
            ],
        }
        self.patch_module(
            load_alarm_com=lambda: alarm,
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        alert = next(item for item in alerts if item["title"] == "Alarm.com trouble conditions active")
        self.assertIn("Video Device - Not Responding (Sideyard)", alert["detail"])
        self.assertIn("mac=504074958A80", alert["detail"])
        self.assertIn("Power-cycle", generate_alerts.recommended_action(alert) or "")

    def test_old_sense_live_auth_warnings_do_not_alert_when_current_snapshot_is_clean(self) -> None:
        current = latest_snapshot()
        current["homebridge"]["logs"]["recentWarnings"] = [
            "[Security System] WebSocket token fetch returned 403, forcing re-authentication...",
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(
            base_config(),
            current,
            [sense_auth_warning_row(), sense_auth_warning_row(), sense_auth_warning_row()],
        )
        titles = {item["title"] for item in alerts}
        self.assertNotIn("Sense live websocket auth is noisy", titles)

    def test_age_label_handles_alarm_com_utc_timestamps(self) -> None:
        self.assertEqual(
            generate_alerts.age_label("2026-06-10T20:00:00.000Z", "2026-06-10T13:05:30-07:00"),
            "5m",
        )

    def test_virtual_cache_pending_refresh_is_not_stale_mismatch(self) -> None:
        self.patch_module(
            load_json_file=lambda _path: {},
            load_latest_characteristics=lambda: {},
            disabled_enphase_service_names=lambda _config: set(),
            cached_enphase_service_names=lambda: set(),
            configured_homebridge_dummy_names=lambda _config: set(),
            cached_homebridge_dummy_names=lambda: set(),
            homebridge_dummy_switch_cache=lambda _characteristics: {"Grid Out": True},
            unifi_multi_active_clients=lambda _characteristics: {},
        )

        audit = generate_alerts.audit_homekit_surface(
            [
                {
                    "name": "Grid Out",
                    "active": False,
                    "readback": False,
                    "ok": True,
                }
            ]
        )

        self.assertEqual(audit["virtualCacheMismatches"], [])
        self.assertEqual(audit["virtualCachePendingRefresh"][0]["name"], "Grid Out")


if __name__ == "__main__":
    unittest.main()
