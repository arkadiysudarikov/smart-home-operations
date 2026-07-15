#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("analyze_all_energy_readings", ROOT / "scripts" / "analyze_all_energy_readings.py")
assert SPEC and SPEC.loader
analyze_all_energy_readings = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyze_all_energy_readings
SPEC.loader.exec_module(analyze_all_energy_readings)


class AnalyzeAllEnergyReadingsTest(unittest.TestCase):
    def test_interval_estimate_uses_time_weighted_integration(self) -> None:
        start = analyze_all_energy_readings.parse_iso("2026-07-15T10:00:00-07:00")
        end = analyze_all_energy_readings.parse_iso("2026-07-15T10:15:00-07:00")
        index = analyze_all_energy_readings.build_sample_index(
            [
                {"capturedAt": start, "kw": 0.0},
                {"capturedAt": end, "kw": 4.0},
            ]
        )

        self.assertAlmostEqual(analyze_all_energy_readings.estimate_interval_kwh(index, start, end), 0.5)

    def test_discover_sce_files_skips_external_roots_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            downloads = root / "Downloads"
            data_dir.mkdir()
            downloads.mkdir()
            (data_dir / "SCE_Usage_UtilityAPI_data.csv").write_text("data\n")
            (downloads / "SCE_Usage_GBC_download.csv").write_text("external\n")

            with (
                mock.patch.object(analyze_all_energy_readings, "DATA_DIR", data_dir),
                mock.patch.object(analyze_all_energy_readings.Path, "home", return_value=root),
            ):
                found = analyze_all_energy_readings.discover_sce_files([])

            self.assertEqual([path.name for path in found], ["SCE_Usage_UtilityAPI_data.csv"])

    def test_discover_sce_files_scans_external_roots_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            downloads = root / "Downloads"
            data_dir.mkdir()
            downloads.mkdir()
            (data_dir / "SCE_Usage_UtilityAPI_data.csv").write_text("data\n")
            (downloads / "SCE_Usage_GBC_download.csv").write_text("external\n")

            with (
                mock.patch.object(analyze_all_energy_readings, "DATA_DIR", data_dir),
                mock.patch.object(analyze_all_energy_readings.Path, "home", return_value=root),
            ):
                found = analyze_all_energy_readings.discover_sce_files([], scan_external=True)

            self.assertEqual([path.name for path in found], ["SCE_Usage_GBC_download.csv", "SCE_Usage_UtilityAPI_data.csv"])

    def test_load_sce_intervals_preserves_existing_interval_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            existing = data_dir / "sce_usage_intervals.csv"
            existing.write_text(
                "\n".join(
                    [
                        "start,end,delivered_kwh,received_kwh,net_import_kwh,qualities,source_count",
                        "2026-06-20T23:45:00-07:00,2026-06-21T00:00:00-07:00,0.7,0.0,0.7,,1",
                    ]
                )
                + "\n"
            )
            stale_api = data_dir / "SCE_Usage_UtilityAPI_stale.csv"
            stale_api.write_text(
                "\n".join(
                    [
                        "Energy Consumption Time Period Start,Energy Consumption Time Period End,Delivered,Received",
                        "2026-06-15 23:45:00,2026-06-16 00:00:00,0.29,0.0",
                    ]
                )
                + "\n"
            )

            with mock.patch.object(analyze_all_energy_readings, "DATA_DIR", data_dir):
                intervals, file_stats = analyze_all_energy_readings.load_sce_intervals([stale_api])

            summary = analyze_all_energy_readings.summarize_intervals(intervals)
            self.assertEqual(summary["coverageEnd"], "2026-06-21T00:00:00-07:00")
            self.assertTrue(file_stats[0]["preserved"])


if __name__ == "__main__":
    unittest.main()
