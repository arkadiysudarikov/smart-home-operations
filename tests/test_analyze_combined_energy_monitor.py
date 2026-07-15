#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "analyze_combined_energy_monitor",
    ROOT / "scripts" / "analyze_combined_energy_monitor.py",
)
assert SPEC and SPEC.loader
combined = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(combined)


class AnalyzeCombinedEnergyMonitorTest(unittest.TestCase):
    def test_sense_daily_completeness_follows_plotted_cloud_trend(self) -> None:
        today = datetime.now().astimezone().date()
        yesterday = today.fromordinal(today.toordinal() - 1).isoformat()

        row = combined.build_daily_summary(
            {}, {}, {}, {}, {"trends": {yesterday: {"consumption": {"total": 12.3}}}}
        )[0]

        self.assertEqual(row["senseLoadKwh"], 12.3)
        self.assertTrue(row["senseComplete"])
        self.assertFalse(row["senseIntervalComplete"])

    def test_daily_summary_retains_more_than_ten_days(self) -> None:
        trends = {
            f"2026-06-{day:02d}": {"consumption": {"total": float(day)}}
            for day in range(1, 13)
        }

        rows = combined.build_daily_summary({}, {}, {}, {}, {"trends": trends})

        self.assertEqual(len(rows), 12)

    def test_energy_cost_freshness_uses_latest_closed_bill_date(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.patch_module(live_envoy_source=lambda: {}, live_sense_source=lambda: {})

        rows = combined.build_source_status(
            now,
            {"source_status_stale_hours": 24},
            {},
            {},
            {},
            {},
            {},
            {
                "generatedAt": "2026-07-15T11:59:00-07:00",
                "model": {"latestClosedBill": {"periodEnd": "2026-05-07"}},
            },
        )

        costs = next(row for row in rows if row["source"] == "Energy costs")
        self.assertEqual(costs["status"], "stale")
        self.assertIn("2026-05-07", costs["detail"])
    def patch_module(self, **replacements: object) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(combined, name)
            setattr(combined, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(combined, name, original)

    def test_sce_monitor_coverage_uses_interval_dates(self) -> None:
        coverage = combined.sce_monitor_coverage(
            {"coverageEnd": "2026-07-02T00:00:00-07:00"},
            {
                "smartHomeMonitor": {
                    "envoy:Consumption Total": {"start": "2026-07-06T12:00:00-07:00"},
                    "sense": {"start": "2026-07-06T13:00:00-07:00"},
                }
            },
        )

        self.assertFalse(coverage["overlaps"])
        self.assertEqual(coverage["monitorStart"], "2026-07-06T12:00:00-07:00")
        self.assertAlmostEqual(coverage["gapDays"], 4.5)

    def test_sce_monitor_coverage_detects_interval_overlap(self) -> None:
        coverage = combined.sce_monitor_coverage(
            {"coverageEnd": "2026-07-07T00:00:00-07:00"},
            {"smartHomeMonitor": {"sense": {"start": "2026-07-06T13:00:00-07:00"}}},
        )

        self.assertTrue(coverage["overlaps"])
        self.assertLess(coverage["gapDays"], 0)

    def test_live_sense_source_preserves_offline_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "sense_now_latest.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "capturedAt": "2026-07-12T19:58:00Z",
                        "online": False,
                        "connectionState": "OFFLINE",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir)

            source = combined.live_sense_source()

        self.assertEqual(source["status"], "offline")
        self.assertEqual(source["detail"], "OFFLINE")

    def test_live_sense_ev_watts_requires_fresh_online_category(self) -> None:
        now = datetime(2026, 7, 12, 21, 22, tzinfo=ZoneInfo("America/Los_Angeles"))
        sense_now = {
            "ok": True,
            "online": True,
            "capturedAt": "2026-07-13T04:21:30Z",
            "devices": [
                {"id": "always_on", "watts": 200},
                {"id": "category-ev", "watts": 7280.0},
            ],
        }

        self.assertEqual(combined.live_sense_ev_watts(sense_now, now), 7280.0)
        sense_now["capturedAt"] = "2026-07-13T04:10:00Z"
        self.assertIsNone(combined.live_sense_ev_watts(sense_now, now))
        sense_now["capturedAt"] = "2026-07-13T04:21:30Z"
        sense_now["online"] = False
        self.assertIsNone(combined.live_sense_ev_watts(sense_now, now))

    def test_daily_summary_uses_standalone_sce_interval_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "sce_usage_intervals.csv").write_text(
                "\n".join(
                    [
                        "start,end,delivered_kwh,received_kwh,net_import_kwh,qualities,source_count",
                        "2026-06-13T00:00:00-07:00,2026-06-13T00:15:00-07:00,1.5,0.2,1.3,,1",
                        "2026-06-13T00:15:00-07:00,2026-06-13T00:30:00-07:00,2.5,0.3,2.2,,1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir)

            rows = combined.build_daily_summary(
                {},
                {},
                {},
                {},
                {"trends": {"2026-06-13": {"consumption": {"total": 10}, "production": {"total": 5}}}},
            )

        row = next(item for item in rows if item["date"] == "2026-06-13")
        self.assertAlmostEqual(row["sceDeliveredKwh"], 4.0)
        self.assertAlmostEqual(row["sceReceivedKwh"], 0.5)
        self.assertAlmostEqual(row["sceNetImportKwh"], 3.5)
        self.assertNotIn("SCE interval", row["unresolvedGaps"])

    def test_standalone_sce_daily_totals_do_not_double_count_overlap_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "sce_usage_intervals.csv").write_text(
                "\n".join(
                    [
                        "start,end,delivered_kwh,received_kwh,net_import_kwh,qualities,source_count",
                        "2026-06-17T00:00:00-07:00,2026-06-17T00:15:00-07:00,10,1,9,,1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir)

            rows = combined.build_daily_summary(
                {
                    "overlapPairs": [
                        {
                            "start": "2026-06-17T00:00:00-07:00",
                            "sceDeliveredKwh": 10,
                            "sceReceivedKwh": 1,
                            "sceNetImportKwh": 9,
                            "envoyConsumptionTotalKwhEstimate": 3,
                        }
                    ]
                },
                {},
                {},
                {},
                {},
            )

        row = next(item for item in rows if item["date"] == "2026-06-17")
        self.assertEqual(row["sceDeliveredKwh"], 10)
        self.assertEqual(row["sceReceivedKwh"], 1)
        self.assertEqual(row["sceNetImportKwh"], 9)
        self.assertEqual(row["envoySiteLoadKwh"], 3)

    def test_fresh_manual_export_with_normal_utility_lag_is_lagging_not_stale(self) -> None:
        now = datetime(2026, 6, 22, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        all_energy = {
            "sceGreenButton": {
                "files": [
                    {
                        "modified": "2026-06-22T11:30:00-07:00",
                        "path": "/Users/example/Downloads/SCE_Usage_8014468177_06-01-25_to_06-22-26.csv",
                    }
                ]
            }
        }

        status = combined.sce_status_label(
            now,
            all_energy,
            36.5,
            {"sce_interval_stale_hours": 36, "sce_interval_normal_lag_hours": 48, "sce_fresh_export_grace_hours": 24},
        )

        self.assertEqual(status, "lagging")

    def test_old_export_past_stale_threshold_is_stale(self) -> None:
        now = datetime(2026, 6, 22, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        all_energy = {"sceGreenButton": {"files": [{"modified": "2026-06-19T11:30:00-07:00"}]}}

        status = combined.sce_status_label(
            now,
            all_energy,
            36.5,
            {"sce_interval_stale_hours": 36, "sce_interval_normal_lag_hours": 48, "sce_fresh_export_grace_hours": 24},
        )

        self.assertEqual(status, "stale")

    def test_stale_alarm_energy_downgrades_when_cache_comparison_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config_dir = Path(tmp) / "config"
            data_dir.mkdir()
            config_dir.mkdir()
            config_path = config_dir / "sources.json"
            config_path.write_text(
                json.dumps(
                    {
                        "alerts": {
                            "alarm_daily_dashboard_mismatch_kwh": 25,
                            "alarm_energy_capture_stale_hours": 24,
                            "source_status_stale_hours": 24,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_bill_home_pairing.json").write_text(
                json.dumps(
                    {
                        "alarm": {
                            "capturedAtLocal": "2020-01-01T00:00:00-08:00",
                            "dailyTotalMinusDashboardMtdKwh": 0,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_homebridge_state.json").write_text(
                json.dumps(
                    {
                        "generatedAt": datetime.now(tz=ZoneInfo("America/Los_Angeles")).isoformat(timespec="seconds"),
                        "staleCount": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir, CONFIG_PATH=config_path)

            payload = combined.build_payload()

        titles = {item["title"]: item["severity"] for item in payload["alerts"]}
        self.assertNotIn("Alarm.com energy stale", payload["states"])
        self.assertNotIn("Alarm.com energy is stale", titles)
        self.assertEqual(titles["Alarm.com energy capture is stale but cache is clean"], "info")
        self.assertTrue(payload["alarmEnergyStatus"]["stalenessDowngraded"])
        self.assertEqual(payload["alarmEnergyStatus"]["cacheComparison"]["staleCount"], 0)

    def test_nested_alarm_energy_capture_overrides_stale_pairing_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config_dir = Path(tmp) / "config"
            data_dir.mkdir()
            config_dir.mkdir()
            config_path = config_dir / "sources.json"
            fresh_capture = datetime.now(tz=ZoneInfo("America/Los_Angeles")).isoformat(timespec="seconds")
            config_path.write_text(
                json.dumps(
                    {
                        "alerts": {
                            "alarm_daily_dashboard_mismatch_kwh": 25,
                            "alarm_energy_capture_stale_hours": 24,
                            "source_status_stale_hours": 24,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_bill_home_pairing.json").write_text(
                json.dumps(
                    {
                        "alarm": {
                            "capturedAtLocal": "2026-06-23T10:55:15-07:00",
                            "dailyTotalMinusDashboardMtdKwh": 0,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_com.json").write_text(
                json.dumps(
                    {
                        "generatedAt": fresh_capture,
                        "energy": {
                            "capturedAtLocal": fresh_capture,
                            "dashboard": {"monthToDateKwh": 836},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_homebridge_state.json").write_text(
                json.dumps({"generatedAt": fresh_capture, "staleCount": 0}) + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir, CONFIG_PATH=config_path)

            payload = combined.build_payload()

        titles = {item["title"]: item["severity"] for item in payload["alerts"]}
        alarm_row = next(item for item in payload["sourceStatus"] if item["source"] == "Alarm.com")
        self.assertEqual(payload["alarmEnergyStatus"]["capturedAtLocal"], fresh_capture)
        self.assertFalse(payload["alarmEnergyStatus"]["isStale"])
        self.assertFalse(payload["alarmEnergyStatus"]["needsRecapture"])
        self.assertNotIn("Alarm.com energy stale", payload["states"])
        self.assertNotIn("Alarm.com energy is stale", titles)
        self.assertEqual(alarm_row["status"], "fresh")
        self.assertEqual(alarm_row["detail"], fresh_capture)

    def test_partial_alarm_21d_window_does_not_require_recapture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config_dir = Path(tmp) / "config"
            data_dir.mkdir()
            config_dir.mkdir()
            config_path = config_dir / "sources.json"
            fresh_capture = datetime.now(tz=ZoneInfo("America/Los_Angeles")).isoformat(timespec="seconds")
            config_path.write_text(
                json.dumps(
                    {
                        "alerts": {
                            "alarm_daily_dashboard_mismatch_kwh": 25,
                            "alarm_energy_capture_stale_hours": 24,
                            "source_status_stale_hours": 24,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_bill_home_pairing.json").write_text(
                json.dumps(
                    {
                        "overlap": {"closedBillDirectlyOverlapsEnvoySense": True},
                        "alarm": {
                            "capturedAtLocal": fresh_capture,
                            "dashboard": {"monthToDateKwh": 1140},
                            "dailyRows": [
                                {"date": "2026-06-12", "meter": "Energy Clamp", "kwh": 45.029},
                                {"date": "2026-07-02", "meter": "Energy Clamp", "kwh": 7.608},
                            ],
                            "dailyTotalKwh": 835.252,
                            "dailyTotalMinusDashboardMtdKwh": -304.748,
                            "periodKwh": {"21d": 835.252},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_com.json").write_text(
                json.dumps(
                    {
                        "generatedAt": fresh_capture,
                        "energy": {
                            "capturedAtLocal": fresh_capture,
                            "dashboard": {"monthToDateKwh": 1140},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_homebridge_state.json").write_text(
                json.dumps({"generatedAt": fresh_capture, "staleCount": 0}) + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir, CONFIG_PATH=config_path)

            payload = combined.build_payload()

        titles = {item["title"]: item["severity"] for item in payload["alerts"]}
        status = payload["alarmEnergyStatus"]
        self.assertNotIn("Alarm.com energy totals disagree", titles)
        self.assertNotIn("Alarm.com energy inconsistent", payload["states"])
        self.assertTrue(status["dashboardComparison"]["partialCoverage"])
        self.assertEqual(status["dashboardComparison"]["status"], "partial_21d_window")
        self.assertIsNone(status["dailyTotalMinusDashboardMtdKwh"])
        self.assertAlmostEqual(status["rawDailyTotalMinusDashboardMtdKwh"], -304.748)
        self.assertFalse(status["isInconsistent"])
        self.assertFalse(status["needsRecapture"])

    def test_stale_alarm_energy_stays_warning_when_cache_comparison_is_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config_dir = Path(tmp) / "config"
            data_dir.mkdir()
            config_dir.mkdir()
            config_path = config_dir / "sources.json"
            config_path.write_text(
                json.dumps(
                    {
                        "alerts": {
                            "alarm_daily_dashboard_mismatch_kwh": 25,
                            "alarm_energy_capture_stale_hours": 24,
                            "source_status_stale_hours": 24,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_bill_home_pairing.json").write_text(
                json.dumps(
                    {
                        "alarm": {
                            "capturedAtLocal": "2020-01-01T00:00:00-08:00",
                            "dailyTotalMinusDashboardMtdKwh": 0,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data_dir / "latest_alarm_homebridge_state.json").write_text(
                json.dumps({"generatedAt": "2020-01-01T00:00:00-08:00", "staleCount": 0}) + "\n",
                encoding="utf-8",
            )
            self.patch_module(DATA_DIR=data_dir, CONFIG_PATH=config_path)

            payload = combined.build_payload()

        titles = {item["title"]: item["severity"] for item in payload["alerts"]}
        self.assertIn("Alarm.com energy stale", payload["states"])
        self.assertEqual(titles["Alarm.com energy is stale"], "warning")
        self.assertFalse(payload["alarmEnergyStatus"]["stalenessDowngraded"])


if __name__ == "__main__":
    unittest.main()
