#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
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
            "smarthq_auth_event_limit": 20,
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


def latest_snapshot_with_smarthq() -> dict[str, Any]:
    latest = latest_snapshot()
    latest["homebridge"]["config"]["platforms"].append({"platform": "SmartHQ", "name": "SmartHQ"})
    return latest


def latest_snapshot_with_tahoma() -> dict[str, Any]:
    latest = latest_snapshot()
    latest["homebridge"]["config"]["platforms"].extend(
        [
            {"platform": "Tahoma", "name": "Primary"},
            {"platform": "Tahoma", "name": "Bedroom"},
        ]
    )
    return latest


def config_with_retained_auth() -> dict[str, Any]:
    config = base_config()
    config["alerts"]["integration_auth_event_limit"] = 20
    return config


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


def combined_energy_payload(source_status: list[dict[str, Any]] | None = None, alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "sourceStatus": source_status or [],
        "alerts": alerts or [],
    }


def latest_characteristics(value: int = 0) -> dict[str, Any]:
    return {
        "k": {
            "accessory": "Entry Door Contact",
            "service": "Entry Door Contact",
            "characteristic": "ContactSensorState",
            "value": value,
            "plugin": "homebridge-node-alarm-dot-com",
            "cacheFile": "cachedAccessories.alarm",
            "accessoryId": "accessory-1",
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


def sense_auth_warning_row(message: str | None = None) -> dict[str, Any]:
    message = message or "[Sense Energy Meter] Error event on sense: Unexpected server response: 401."
    raw = {
        "homebridge": {
            "logs": {"recentWarnings": [message]},
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


def unifi_api_warning_row() -> dict[str, Any]:
    raw = {
        "homebridge": {
            "logs": {
                "recentWarnings": [
                    '[homebridge-unifi-occupancy] ERROR: Failed to load clients: StatusCodeError: 502 - {"error":{"code":502,"message":"Bad Gateway"}}'
                ],
            },
        }
    }
    return {"raw_json": json.dumps(raw), "alarm_websocket": 1, "warning_count": 1}


def homekit_compatibility_warning_row() -> dict[str, Any]:
    raw = {
        "homebridge": {
            "logs": {
                "recentWarnings": [
                    "Characteristic not in required or optional characteristic section for service Switch"
                ],
            },
        }
    }
    return {"raw_json": json.dumps(raw), "alarm_websocket": 1, "warning_count": 1}


def component_event(captured_at: str, component: str, message: str) -> dict[str, Any]:
    return {
        "captured_at": captured_at,
        "event_type": "warning" if "failed" in message.lower() or "authentication" in message.lower() else "homebridge",
        "component": component,
        "message": message,
    }


def smarthq_event(captured_at: str, message: str) -> dict[str, Any]:
    return component_event(captured_at, "SmartHQ", message)


class GenerateAlertsTest(unittest.TestCase):
    def test_projection_stabilizer_escalates_immediately(self) -> None:
        previous = generate_alerts.next_projection_stabilization(
            {}, "clear", "sample-1", True, updated_at="2026-07-15T10:00:00-07:00"
        )

        state = generate_alerts.next_projection_stabilization(
            previous, "critical", "sample-2", True, updated_at="2026-07-15T10:05:00-07:00"
        )

        self.assertEqual(state["effectiveLevel"], "critical")
        self.assertEqual(state["reason"], "escalated immediately")
        self.assertEqual(state["events"][-1]["event"], "published escalation")

    def test_projection_stabilizer_requires_three_distinct_fresh_samples_to_clear(self) -> None:
        state = generate_alerts.next_projection_stabilization(
            {}, "critical", "sample-0", True, updated_at="2026-07-15T10:00:00-07:00"
        )
        state = generate_alerts.next_projection_stabilization(
            state, "clear", "sample-1", True, updated_at="2026-07-15T10:05:00-07:00"
        )
        self.assertEqual((state["effectiveLevel"], state["consecutiveFreshSamples"]), ("critical", 1))
        state = generate_alerts.next_projection_stabilization(
            state, "clear", "sample-2", True, updated_at="2026-07-15T10:10:00-07:00"
        )
        self.assertEqual((state["effectiveLevel"], state["consecutiveFreshSamples"]), ("critical", 2))
        state = generate_alerts.next_projection_stabilization(
            state, "clear", "sample-3", True, updated_at="2026-07-15T10:15:00-07:00"
        )

        self.assertEqual(state["effectiveLevel"], "clear")
        self.assertIsNone(state["pendingLevel"])
        self.assertEqual(state["events"][-1]["event"], "published clear")

    def test_projection_stabilizer_ignores_duplicate_and_stale_clear_samples(self) -> None:
        state = generate_alerts.next_projection_stabilization({}, "critical", "sample-0", True)
        state = generate_alerts.next_projection_stabilization(state, "clear", "sample-1", True)
        duplicate = generate_alerts.next_projection_stabilization(state, "clear", "sample-1", True)
        stale = generate_alerts.next_projection_stabilization(duplicate, "clear", "sample-2", False)

        self.assertEqual(duplicate["consecutiveFreshSamples"], 1)
        self.assertEqual(duplicate["reason"], "duplicate sample ignored")
        self.assertEqual(stale["effectiveLevel"], "critical")
        self.assertEqual(stale["consecutiveFreshSamples"], 0)
        self.assertEqual(stale["reason"], "held for fresh Alarm.com data")

    def test_projection_stabilizer_survives_serialized_restart_state(self) -> None:
        state = generate_alerts.next_projection_stabilization({}, "critical", "sample-0", True)
        state = generate_alerts.next_projection_stabilization(state, "clear", "sample-1", True)
        restored = json.loads(json.dumps(state))
        restored = generate_alerts.next_projection_stabilization(restored, "clear", "sample-2", True)

        self.assertEqual(restored["effectiveLevel"], "critical")
        self.assertEqual(restored["consecutiveFreshSamples"], 2)

    def test_energy_budget_virtual_sensor_uses_stabilized_effective_level(self) -> None:
        accessory = {
            "id": "smart_home_energy_budget_v2",
            "alert_titles": ["Energy projection is critical"],
        }

        self.assertTrue(
            generate_alerts.virtual_sensor_should_be_active(
                accessory, set(), set(), {"effectiveLevel": "critical"}
            )
        )
        self.assertFalse(
            generate_alerts.virtual_sensor_should_be_active(
                accessory, {"Energy projection is critical"}, set(), {"effectiveLevel": "clear"}
            )
        )

    def test_projection_stabilization_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "energy_alert_stabilization.json"
            self.patch_module(DATA_DIR=Path(tmp), ENERGY_ALERT_STABILIZATION_PATH=path)

            state = generate_alerts.update_projection_stabilization(
                [{"title": "Energy projection is critical"}],
                {
                    "generatedAt": "2026-07-15T10:00:00-07:00",
                    "sourceStatus": [{"source": "Alarm.com", "status": "fresh"}],
                },
                3,
            )

            self.assertEqual(state["effectiveLevel"], "critical")
            self.assertEqual(json.loads(path.read_text())["effectiveLevel"], "critical")

    def test_build_alerts_reports_wrong_homebridge_advertisement_ip(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["advertisements"] = {
            "status": "mismatch",
            "configuredIPv4": "192.168.0.69",
            "mismatches": [
                {
                    "name": "Homebridge Dummy",
                    "hostname": "0E_F8_15_C2_EC_AC.local",
                    "resolvedIPv4": ["192.168.0.172"],
                }
            ],
        }

        alerts = generate_alerts.build_alerts(base_config(), latest, [])

        alert = next(item for item in alerts if item["title"] == "Homebridge advertisements use the wrong IP")
        self.assertEqual(alert["severity"], "warning")
        self.assertIn("192.168.0.69", alert["detail"])
        self.assertIn("192.168.0.172", alert["detail"])
        self.assertIn("restart Homebridge once", generate_alerts.recommended_action(alert))

    def patch_module(self, **replacements: Any) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(generate_alerts, name)
            setattr(generate_alerts, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(generate_alerts, name, original)

    def test_load_alarm_com_reads_valid_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarm.json"
            path.write_text(json.dumps({"login": {"ok": True}}) + "\n")
            self.patch_module(ALARM_COM_PATH=path)

            payload = generate_alerts.load_alarm_com()

        self.assertTrue(payload["login"]["ok"])

    def test_restart_grace_suppresses_energy_stale(self) -> None:
        states = generate_alerts.active_state_titles(base_config(), latest_snapshot())
        self.assertNotIn("Energy data stale", states)

    def test_energy_stale_returns_after_restart_grace(self) -> None:
        latest = latest_snapshot()
        latest["captured_at"] = "2026-06-10T13:15:01-07:00"
        states = generate_alerts.active_state_titles(base_config(), latest)
        self.assertIn("Energy data stale", states)

    def test_battery_charging_state_uses_envoy_signal(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["latestMetrics"] = {
            "enphase_production_kw": 4.0,
            "enphase_consumption_net_kw": 0.0,
            "enphase_consumption_total_kw": 4.0,
            "enphase_battery_charging": True,
        }
        self.patch_module(load_combined_energy=lambda: {})

        states = generate_alerts.active_state_titles(base_config(), latest)

        self.assertIn("Battery charging", states)

    def test_battery_discharging_state_uses_envoy_signal(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["latestMetrics"] = {
            "enphase_production_kw": 2.2,
            "enphase_consumption_net_kw": 0.0,
            "enphase_consumption_total_kw": 4.8,
            "enphase_battery_charging": False,
            "enphase_battery_discharging": True,
        }
        self.patch_module(load_combined_energy=lambda: {})

        states = generate_alerts.active_state_titles(base_config(), latest)

        self.assertIn("Battery discharging", states)
        self.assertNotIn("Battery charging", states)

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

    def test_homekit_compatibility_warning_does_not_raise_volume_alert(self) -> None:
        latest = latest_snapshot()
        warning = "Characteristic not in required or optional characteristic section for service Switch"
        latest["homebridge"]["logs"]["warningCount"] = 1
        latest["homebridge"]["logs"]["recentWarnings"] = [warning]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )

        alerts = generate_alerts.build_alerts(
            base_config(),
            latest,
            [homekit_compatibility_warning_row(), homekit_compatibility_warning_row()],
        )
        titles = {item["title"] for item in alerts}

        self.assertEqual(generate_alerts.warning_category(warning), "HomeKit compatibility")
        self.assertEqual(
            generate_alerts.warning_category(
                "HAP-NodeJS WARNING: The accessory '🪫 Discharging' has an invalid 'Name' characteristic"
            ),
            "HomeKit compatibility",
        )
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

    def test_alarm_state_comparison_includes_cache_metadata_for_repair(self) -> None:
        self.patch_module(load_latest_characteristics=lambda: latest_characteristics(value=1))

        comparison = generate_alerts.compare_alarm_portal_to_homebridge(alarm_com_payload(activity_ok=True), latest_snapshot())

        stale = comparison["stale"][0]
        self.assertEqual(stale["homebridgeCacheFile"], "cachedAccessories.alarm")
        self.assertEqual(stale["homebridgeAccessoryId"], "accessory-1")

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

    def test_unifi_api_alert_suppresses_duplicate_high_warning_volume(self) -> None:
        latest = latest_snapshot()
        latest["homebridge"]["logs"]["warningCount"] = 1
        latest["homebridge"]["logs"]["recentWarnings"] = [
            '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 504 - {"error":{"code":504,"message":"Gateway Timeout"}}',
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )

        alerts = generate_alerts.build_alerts(
            base_config(),
            latest,
            [unifi_api_warning_row(), unifi_api_warning_row(), unifi_api_warning_row()],
        )
        titles = {item["title"] for item in alerts}

        self.assertIn("UniFi occupancy API is failing", titles)
        self.assertNotIn("Recent Homebridge warning volume is high", titles)

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

    def test_sce_stale_action_does_not_claim_auto_collection_by_default(self) -> None:
        action = generate_alerts.recommended_action({"title": "SCE interval data is stale", "detail": ""})

        self.assertIn("download already-available UtilityAPI intervals", action or "")
        self.assertIn("fresh SCE Green Button export", action or "")
        self.assertIn("paid UtilityAPI collection should stay off", action or "")
        self.assertNotIn("auto-triggers", action or "")

    def test_sce_stale_action_reports_utilityapi_payment_required(self) -> None:
        action = generate_alerts.recommended_action(
            {
                "title": "SCE interval data is stale",
                "detail": "UtilityAPI historical collection status: `utilityapi_payment_required`.",
            }
        )

        self.assertIn("Skip paid UtilityAPI collection", action or "")
        self.assertIn("fresh SCE Green Button export", action or "")

    def test_source_status_stale_sense_gets_dedicated_alert(self) -> None:
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: combined_energy_payload(
                [
                    {
                        "source": "Sense",
                        "status": "stale",
                        "ageHours": 94.6273,
                        "detail": "2026-07-08T18:30:27.552Z",
                    }
                ]
            ),
        )

        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        alert = next(item for item in alerts if item["title"] == "Sense data is stale")

        self.assertIn("`94.6` hours", alert["detail"])
        self.assertIn("Sense-derived trend", alert["detail"])
        self.assertIn("Fix the Sense auth/live websocket issue", generate_alerts.recommended_action(alert) or "")

    def test_source_status_sce_stale_uses_existing_sce_alert_only(self) -> None:
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: combined_energy_payload(
                [
                    {
                        "source": "SCE",
                        "status": "stale",
                        "ageHours": 250.1,
                        "detail": "2026-07-02T00:00:00-07:00",
                    }
                ],
                [
                    {
                        "title": "SCE interval data is stale",
                        "severity": "warning",
                        "detail": "Newest SCE Green Button interval ends `2026-07-02T00:00:00-07:00`.",
                    }
                ],
            ),
        )

        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        titles = {item["title"] for item in alerts}

        self.assertIn("SCE interval data is stale", titles)
        self.assertNotIn("SCE data is stale", titles)

    def test_source_status_fresh_source_does_not_alert(self) -> None:
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: combined_energy_payload(
                [
                    {
                        "source": "Sense",
                        "status": "fresh",
                        "ageHours": 0.5,
                        "detail": "2026-07-12T10:00:00-07:00",
                    }
                ]
            ),
        )

        alerts = generate_alerts.build_alerts(base_config(), latest_snapshot(), [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("Sense data is stale", titles)
        self.assertNotIn("Sense data is missing", titles)

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

    def test_repeated_same_sense_live_auth_warning_does_not_alert(self) -> None:
        message = "[6/17/2026, 2:26:31 PM] [Sense Energy Meter] Error event on sense: Unexpected server response: 401."
        current = latest_snapshot()
        current["homebridge"]["logs"]["recentWarnings"] = [message]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(
            base_config(),
            current,
            [sense_auth_warning_row(message), sense_auth_warning_row(message), sense_auth_warning_row(message)],
        )
        titles = {item["title"] for item in alerts}
        self.assertNotIn("Sense live websocket auth is noisy", titles)

    def test_distinct_current_sense_live_auth_warnings_alert(self) -> None:
        current = latest_snapshot()
        current["homebridge"]["logs"]["recentWarnings"] = [
            "[6/17/2026, 2:36:31 PM] [Sense Energy Meter] Error event on sense: Unexpected server response: 401.",
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
        )
        alerts = generate_alerts.build_alerts(
            base_config(),
            current,
            [
                sense_auth_warning_row(
                    "[6/17/2026, 2:26:31 PM] [Sense Energy Meter] Error event on sense: Unexpected server response: 401."
                ),
                sense_auth_warning_row(
                    "[6/17/2026, 2:31:31 PM] [Sense Energy Meter] Error event on sense: Unexpected server response: 401."
                ),
            ],
        )
        alert = next(item for item in alerts if item["title"] == "Sense live websocket auth is noisy")
        self.assertIn("`3` distinct recent Sense live-websocket auth warnings", alert["detail"])

    def test_smarthq_auth_failure_in_current_warning_gets_dedicated_alert(self) -> None:
        current = latest_snapshot_with_smarthq()
        current["homebridge"]["logs"]["warningCount"] = 1
        current["homebridge"]["logs"]["recentWarnings"] = [
            "[7/8/2026, 3:26:41 PM] [SmartHQ] discoverDevices, Failed to get Access Token, Error Message: Authentication failed: No authorization code received"
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_smarthq_home_events=lambda _limit=200: [],
        )

        alerts = generate_alerts.build_alerts(base_config(), current, [])
        alert = next(item for item in alerts if item["title"] == "SmartHQ authentication is failing")

        self.assertIn("No authorization code received", alert["detail"])
        self.assertIn("restart only the SmartHQ child bridge", generate_alerts.recommended_action(alert) or "")

    def test_retained_smarthq_auth_failure_stays_active_after_warning_window_clears(self) -> None:
        current = latest_snapshot_with_smarthq()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_smarthq_home_events=lambda _limit=200: [
                smarthq_event(
                    "2026-07-08T15:26:41-07:00",
                    "discoverDevices, Failed to get Access Token, Error Message: Authentication failed: No authorization code received, Submit Bugs Here: https://bit.ly/smarthq-bug-report",
                )
            ],
        )

        alerts = generate_alerts.build_alerts(base_config(), current, [])
        titles = {item["title"] for item in alerts}
        alert = next(item for item in alerts if item["title"] == "SmartHQ authentication is failing")

        self.assertIn("SmartHQ authentication is failing", titles)
        self.assertNotIn("Recent Homebridge warning volume is high", titles)
        self.assertIn("2026-07-08T15:26:41-07:00", alert["detail"])

    def test_later_smarthq_device_refresh_clears_prior_auth_failure(self) -> None:
        current = latest_snapshot_with_smarthq()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_smarthq_home_events=lambda _limit=200: [
                smarthq_event("2026-07-08T15:27:30-07:00", "Restoring existing accessory from cache: Washer"),
                smarthq_event(
                    "2026-07-08T15:26:41-07:00",
                    "discoverDevices, Failed to get Access Token, Error Message: Authentication failed: No authorization code received",
                ),
            ],
        )

        alerts = generate_alerts.build_alerts(base_config(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("SmartHQ authentication is failing", titles)

    def test_old_current_warning_does_not_override_later_smarthq_success(self) -> None:
        current = latest_snapshot_with_smarthq()
        current["captured_at"] = "2026-07-12T12:04:47-07:00"
        current["homebridge"]["logs"]["recentWarnings"] = [
            "[7/12/2026, 11:45:33 AM] [SmartHQ] discoverDevices, Failed to get Access Token, Error Message: Authentication failed: No authorization code received"
        ]
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_smarthq_home_events=lambda _limit=200: [
                smarthq_event("2026-07-12T12:03:55-07:00", "Restoring existing accessory from cache: Washer"),
            ],
        )

        alerts = generate_alerts.build_alerts(base_config(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("SmartHQ authentication is failing", titles)

    def test_retained_sense_auth_failure_stays_active_after_warning_window_clears(self) -> None:
        current = latest_snapshot()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_component_home_events=lambda components, _limit=200: [
                component_event(
                    "2026-07-12T05:43:43-07:00",
                    "Sense Energy Meter",
                    "Re-auth failed: Error: Authentication error: request to https://api.sense.com/apiservice/api/v1/authenticate failed",
                )
            ]
            if components == ["Sense Energy Meter"]
            else [],
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        alert = next(item for item in alerts if item["title"] == "Sense live websocket authentication is failing")

        self.assertIn("2026-07-12T05:43:43-07:00", alert["detail"])
        self.assertIn("Sense live watt readings may be cached", alert["detail"])

    def test_later_sense_websocket_open_clears_prior_auth_failure(self) -> None:
        current = latest_snapshot()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_component_home_events=lambda components, _limit=200: [
                component_event("2026-07-12T05:44:10-07:00", "Sense Energy Meter", "Sense WebSocket Opened"),
                component_event(
                    "2026-07-12T05:43:43-07:00",
                    "Sense Energy Meter",
                    "Re-auth failed: Error: Authentication error: request to https://api.sense.com/apiservice/api/v1/authenticate failed",
                ),
            ]
            if components == ["Sense Energy Meter"]
            else [],
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("Sense live websocket authentication is failing", titles)

    def test_later_sense_received_data_clears_prior_auth_failure(self) -> None:
        current = latest_snapshot()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            load_sense_trends=lambda: {},
            recent_component_home_events=lambda components, _limit=200: [
                component_event(
                    "2026-07-12T05:44:10-07:00",
                    "Sense Energy Meter",
                    "Received data. Watts: 368.99, Current: 7, Voltage: 122.64",
                ),
                component_event(
                    "2026-07-12T05:43:43-07:00",
                    "Sense Energy Meter",
                    "Re-auth failed: Error: Authentication error: request to https://api.sense.com/apiservice/api/v1/authenticate failed",
                ),
            ]
            if components == ["Sense Energy Meter"]
            else [],
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("Sense live websocket authentication is failing", titles)

    def test_later_sense_api_capture_clears_prior_auth_failure(self) -> None:
        current = latest_snapshot()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            load_sense_now=lambda: {},
            load_sense_trends=lambda: {
                "capturedAt": "2026-07-12T19:57:50.835Z",
                "daysCaptured": 14,
                "errors": [],
            },
            recent_component_home_events=lambda components, _limit=200: [
                component_event(
                    "2026-07-12T10:00:00-07:00",
                    "Sense Energy Meter",
                    "Re-auth failed: Error: Authentication error: request timed out",
                )
            ]
            if components == ["Sense Energy Meter"]
            else [],
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("Sense live websocket authentication is failing", titles)

    def test_sense_monitor_offline_is_separate_from_login(self) -> None:
        current = latest_snapshot()
        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            load_sense_now=lambda: {
                "capturedAt": "2026-07-12T19:58:00Z",
                "online": False,
                "connectionState": "OFFLINE",
            },
        )

        alerts = generate_alerts.build_alerts(base_config(), current, [])
        alert = next(item for item in alerts if item["title"] == "Sense monitor is offline")

        self.assertIn("Account authentication is working", alert["detail"])

    def test_tahoma_auth_failure_alerts_for_affected_child(self) -> None:
        current = latest_snapshot_with_tahoma()

        def retained_events(components: list[str], _limit: int = 200) -> list[dict[str, Any]]:
            if set(components) == {"Bedroom", "Primary"}:
                return [
                    component_event(
                        "2026-07-11T03:05:37-07:00",
                        "Bedroom",
                        "Registration error - Error 401 Not authenticated",
                    )
                ]
            return []

        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_component_home_events=retained_events,
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        alert = next(item for item in alerts if item["title"] == "TaHoma authentication is failing")

        self.assertIn("Bedroom", alert["detail"])
        self.assertIn("401 Not authenticated", alert["detail"])

    def test_later_tahoma_configure_device_clears_prior_auth_failure(self) -> None:
        current = latest_snapshot_with_tahoma()

        def retained_events(components: list[str], _limit: int = 200) -> list[dict[str, Any]]:
            if set(components) == {"Bedroom", "Primary"}:
                return [
                    component_event("2026-07-12T09:48:20-07:00", "Bedroom", "Configure device Bedroom Shade"),
                    component_event(
                        "2026-07-11T03:05:37-07:00",
                        "Bedroom",
                        "Registration error - Error 401 Not authenticated",
                    ),
                ]
            return []

        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_component_home_events=retained_events,
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("TaHoma authentication is failing", titles)

    def test_alarm_child_auth_failure_clears_after_received_devices(self) -> None:
        current = latest_snapshot()

        def retained_events(components: list[str], _limit: int = 200) -> list[dict[str, Any]]:
            if components == ["Security System"]:
                return [
                    component_event("2026-07-12T09:48:21-07:00", "Security System", "Received 19 sensors from Alarm.com"),
                    component_event(
                        "2026-07-05T22:23:52-07:00",
                        "Security System",
                        "Login failed: Error: request to https://www.alarm.com/login failed, reason: read ETIMEDOUT",
                    ),
                ]
            return []

        self.patch_module(
            load_alarm_com=lambda: alarm_com_payload(activity_ok=True),
            load_latest_characteristics=lambda: latest_characteristics(value=0),
            load_combined_energy=lambda: {},
            recent_component_home_events=retained_events,
        )

        alerts = generate_alerts.build_alerts(config_with_retained_auth(), current, [])
        titles = {item["title"] for item in alerts}

        self.assertNotIn("Alarm.com child bridge authentication is failing", titles)

    def test_inactive_sense_live_auth_category_can_be_filtered_from_trend(self) -> None:
        trend = generate_alerts.warning_trend(
            [sense_auth_warning_row(), sense_auth_warning_row()],
            excluded_categories={"Sense live websocket auth"},
        )
        self.assertEqual(trend["warningMentions"], 0)
        self.assertEqual(trend["leaders"], [])

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
            configured_homebridge_dummy_accessories=lambda _config: {},
            cached_homebridge_dummy_accessories=lambda: {},
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

    def test_homebridge_dummy_cache_audit_matches_stable_ids_after_rename(self) -> None:
        self.patch_module(
            load_json_file=lambda _path: {},
            load_latest_characteristics=lambda: {},
            disabled_enphase_service_names=lambda _config: set(),
            cached_enphase_service_names=lambda: set(),
            configured_homebridge_dummy_accessories=lambda _config: {"smart_home_smarthq_auth_failed": "SmartHQ Auth"},
            cached_homebridge_dummy_accessories=lambda: {"smart_home_smarthq_auth_failed": "SmartHQ"},
            homebridge_dummy_switch_cache=lambda _characteristics: {},
            unifi_multi_active_clients=lambda _characteristics: {},
        )

        audit = generate_alerts.audit_homekit_surface([])

        self.assertEqual(audit["homebridgeDummyCacheDrift"], {"missing": [], "stale": []})


if __name__ == "__main__":
    unittest.main()
