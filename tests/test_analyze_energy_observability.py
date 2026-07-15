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
                    "envoyStorageKwh": 2.0,
                    "envoySolarProductionKwh": 4.0,
                    "senseLoadKwh": 35.0,
                    "senseSolarProductionKwh": 3.8,
                }
            ]
        }
        alarm = {"dailyKwh": [{"date": "2026-07-14", "meter": "Energy Clamp", "kwh": 48.0}]}

        rows = analyzer.build_daily_comparison(combined, alarm)

        self.assertEqual(rows[0]["alarmClampKwh"], 48.0)
        self.assertEqual(rows[0]["sceNetImportKwh"], 44.0)
        self.assertEqual(rows[0]["alarmMinusSenseKwh"], 13.0)
        self.assertEqual(rows[0]["availableSourceCount"], 4)
        self.assertEqual(rows[0]["energyBalanceResidualKwh"], 4.0)
        self.assertEqual(rows[0]["solarParityPercent"], 5.0)

    def test_live_summary_adds_battery_flow_to_envoy_site_load_and_exposes_alarm_baselines(self) -> None:
        live = analyzer.live_summary(
            {"homebridge": {"logs": {"latestMetrics": {
                "enphase_consumption_total_kw": -2.0,
                "enphase_storage_kw": 3.0,
            }}}},
            {},
            {"dashboard": {
                "monthToDateKwh": 504,
                "samePointLastMonthKwh": 446,
                "energyClampProjectedKwh": 1391,
                "energyClampBudgetKwh": 680,
                "energyClampLastBillingKwh": 1232,
                "energyClampAverageBillingKwh": 1182,
            }},
        )

        self.assertEqual(live["envoyMeterTotalKw"], -2.0)
        self.assertEqual(live["envoySiteLoadKw"], 1.0)
        self.assertEqual(live["alarmSamePointLastMonthKwh"], 446.0)
        self.assertEqual(live["alarmLastBillingKwh"], 1232.0)

    def test_daily_comparison_does_not_count_partial_monitor_day_as_complete(self) -> None:
        rows = analyzer.build_daily_comparison(
            {
                "dailySummary": [
                    {
                        "date": "2026-07-14",
                        "sceDeliveredKwh": 52.0,
                        "sceComplete": True,
                        "envoySiteLoadKwh": 12.0,
                        "envoyComplete": False,
                        "senseLoadKwh": 10.0,
                        "senseComplete": True,
                    }
                ]
            },
            {},
        )

        self.assertEqual(rows[0]["availableSourceCount"], 2)
        self.assertIn("Envoy", rows[0]["partialSources"])

    def test_peak_events_converts_quarter_hour_energy_to_average_power(self) -> None:
        payload = {
            "overlapPairs": [
                {
                    "start": "2026-07-14T22:00:00-07:00",
                    "sceDeliveredKwh": 3.0,
                    "sceReceivedKwh": 0.25,
                    "envoySiteLoadKwhEstimate": 2.75,
                    "senseKwhEstimate": 2.0,
                }
            ]
        }

        event = analyzer.peak_events(payload)[0]

        self.assertEqual(event["sceImportKw"], 12.0)
        self.assertEqual(event["sceExportKw"], 1.0)
        self.assertEqual(event["envoySiteLoadKw"], 11.0)
        self.assertEqual(event["senseLoadKw"], 8.0)

    def test_peak_events_preserves_missing_monitor_values(self) -> None:
        event = analyzer.peak_events(
            {"overlapPairs": [{"start": "2026-07-14T22:00:00-07:00", "sceDeliveredKwh": 3.0}]}
        )[0]

        self.assertIsNone(event["sceExportKw"])
        self.assertIsNone(event["envoySiteLoadKw"])
        self.assertIsNone(event["senseLoadKw"])

    def test_peak_events_retains_all_candidates_for_range_filtering(self) -> None:
        rows = [
            {"start": f"2026-07-14T{hour:02d}:00:00-07:00", "sceDeliveredKwh": float(hour)}
            for hour in range(15)
        ]

        events = analyzer.peak_events({"overlapPairs": rows})

        self.assertEqual(len(events), 15)
        self.assertGreater(events[0]["sceImportKw"], events[-1]["sceImportKw"])

    def test_invalid_envoy_counts_degrade_quality(self) -> None:
        quality = analyzer.source_quality(
            {},
            {"overlapPairCount": 20, "invalidReadingCounts": {"envoySiteLoadKwhEstimate": 3}},
            [{"availableSourceCount": 3}],
        )

        self.assertEqual(quality["status"], "degraded")
        self.assertIn("Invalid Envoy gross-load intervals", [item["title"] for item in quality["issues"]])


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

    def test_sparse_historical_coverage_degrades_quality(self) -> None:
        daily = [{"availableSourceCount": 3}] + [{"availableSourceCount": 1} for _ in range(9)]

        quality = analyzer.source_quality({}, {"overlapPairCount": 20}, daily, 90)

        self.assertEqual(quality["status"], "degraded")
        self.assertIn("Historical comparison coverage is limited", [item["title"] for item in quality["issues"]])
        self.assertEqual(quality["historyWindowDays"], 90)
        self.assertEqual(quality["historyDayCount"], 10)
        self.assertIn("1 of 10 days in the 90-day quality window", quality["issues"][0]["detail"])

    def test_quality_window_excludes_older_utility_archive_rows(self) -> None:
        daily = [
            {"date": "2026-04-16", "availableSourceCount": 1},
            {"date": "2026-04-17", "availableSourceCount": 1},
            {"date": "2026-07-14", "availableSourceCount": 3},
            {"date": "2026-07-15", "availableSourceCount": 3},
        ]

        rows = analyzer.daily_rows_for_quality_window(daily, "2026-07-15T15:00:00-07:00", 90)

        self.assertEqual([row["date"] for row in rows], ["2026-04-17", "2026-07-14", "2026-07-15"])

    def test_quality_reports_comparable_coverage_dates(self) -> None:
        quality = analyzer.source_quality(
            {},
            {"overlapPairCount": 20},
            [
                {"date": "2026-07-06", "availableSourceCount": 3},
                {"date": "2026-07-07", "availableSourceCount": 1},
                {"date": "2026-07-08", "availableSourceCount": 3},
            ],
            90,
        )

        self.assertEqual(quality["comparisonCoverageStart"], "2026-07-06")
        self.assertEqual(quality["comparisonCoverageEnd"], "2026-07-08")


if __name__ == "__main__":
    unittest.main()
