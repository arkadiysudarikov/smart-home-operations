#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "analyze_energy_observability", ROOT / "scripts" / "analyze_energy_observability.py"
)
assert SPEC and SPEC.loader
analyzer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzer)


class AnalyzeEnergyObservabilityTest(unittest.TestCase):
    def test_sce_daily_rows_aggregates_retained_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sce_usage_intervals.csv"
            path.write_text(
                "start,end,delivered_kwh,received_kwh,net_import_kwh\n"
                "2026-06-01T00:00:00-07:00,2026-06-01T00:15:00-07:00,1.0,0.2,0.8\n"
                "2026-06-01T00:15:00-07:00,2026-06-01T00:30:00-07:00,2.0,0.1,1.9\n"
            )

            rows = analyzer.sce_daily_rows(path)

            self.assertEqual(rows["2026-06-01"]["delivered"], 3.0)
            self.assertAlmostEqual(rows["2026-06-01"]["received"], 0.3)
            self.assertEqual(rows["2026-06-01"]["net"], 2.7)

    def test_daily_comparison_preserves_distinct_meter_semantics(self) -> None:
        combined = {
            "dailySummary": [
                {
                    "date": "2026-07-14",
                    "sceDeliveredKwh": 52.0,
                    "sceReceivedKwh": 8.0,
                    "sceNetImportKwh": 44.0,
                    "envoySiteLoadKwh": 46.0,
                    "senseLoadKwh": 35.0,
                }
            ]
        }
        alarm = {"dailyKwh": [{"date": "2026-07-14", "meter": "Energy Clamp", "kwh": 48.0}]}

        rows = analyzer.build_daily_comparison(combined, alarm)

        self.assertEqual(rows[0]["alarmClampKwh"], 48.0)
        self.assertEqual(rows[0]["sceNetImportKwh"], 44.0)
        self.assertEqual(rows[0]["alarmMinusSenseKwh"], 13.0)
        self.assertEqual(rows[0]["availableSourceCount"], 4)

    def test_peak_events_converts_quarter_hour_energy_to_average_power(self) -> None:
        payload = {
            "overlapPairs": [
                {
                    "start": "2026-07-14T22:00:00-07:00",
                    "sceDeliveredKwh": 3.0,
                    "sceReceivedKwh": 0.25,
                    "envoyConsumptionTotalKwhEstimate": 2.75,
                    "senseKwhEstimate": 2.0,
                }
            ]
        }

        event = analyzer.peak_events(payload)[0]

        self.assertEqual(event["sceImportKw"], 12.0)
        self.assertEqual(event["sceExportKw"], 1.0)
        self.assertEqual(event["envoySiteLoadKw"], 11.0)
        self.assertEqual(event["senseLoadKw"], 8.0)

    def test_persist_observation_creates_queryable_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = analyzer.DATA_DIR
            original_db_path = analyzer.DB_PATH
            analyzer.DATA_DIR = Path(tmp)
            analyzer.DB_PATH = Path(tmp) / "smart_home.sqlite"
            try:
                analyzer.persist_observation(
                    "2026-07-15T10:00:00-07:00",
                    {
                        "envoyProductionKw": 3.2,
                        "envoySiteLoadKw": 3.1,
                        "batteryCharging": True,
                        "batteryDischarging": False,
                    },
                    {"alerts": [{"title": "test"}], "states": ["battery_charging"]},
                )

                with sqlite3.connect(analyzer.DB_PATH) as db:
                    row = db.execute(
                        "select envoy_production_kw, battery_charging, energy_alert_count, active_states_json "
                        "from energy_observations"
                    ).fetchone()
                self.assertEqual(row[:3], (3.2, 1, 1))
                self.assertEqual(json.loads(row[3]), ["battery_charging"])
            finally:
                analyzer.DATA_DIR = original_data_dir
                analyzer.DB_PATH = original_db_path

    def test_derived_cost_report_does_not_degrade_meter_quality(self) -> None:
        quality = analyzer.source_quality(
            {"sourceStatus": [{"source": "Energy costs", "status": "stale"}]},
            {"overlapPairCount": 20},
            [{"availableSourceCount": 3}],
        )

        self.assertEqual(quality["status"], "ready")
        self.assertEqual(quality["issues"], [])

    def test_monitor_history_lag_is_reported_separately_from_live_freshness(self) -> None:
        quality = analyzer.source_quality(
            {
                "sourceStatus": [{"source": "Envoy", "status": "fresh"}],
                "sources": {
                    "sce": {"coverageEnd": "2026-07-15T00:00:00-07:00"},
                    "envoy": {"end": "2026-07-13T23:45:00-07:00"},
                    "sense": {"end": "2026-07-13T23:44:00-07:00"},
                },
            },
            {"overlapPairCount": 20},
            [{"availableSourceCount": 3}],
        )

        self.assertEqual(quality["status"], "degraded")
        self.assertEqual(quality["issues"][0]["title"], "Monitor history trails utility coverage")


if __name__ == "__main__":
    unittest.main()
