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


if __name__ == "__main__":
    unittest.main()
