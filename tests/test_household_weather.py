import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from household_weather import check, rain_expected, stamp

class WeatherTests(unittest.TestCase):
    def test_forecast_and_opening_both_required(self):
        now = stamp("2026-09-10T18:00:00Z")
        forecast = {"updateTime": "2026-09-10T18:00:00Z", "periods": [{"startTime": "2026-09-10T18:00:00Z", "endTime": "2026-09-10T19:00:00Z", "shortForecast": "Rain", "probabilityOfPrecipitation": {"value": 70}}]}
        state = {"forecast": forecast, "fetchedAt": now}
        self.assertIsNone(check(state, {}, now)[1])
        opened = {"x": {"name": "Window", "group": "sensors", "state": "open"}}
        state, message = check(state, opened, now)
        self.assertIn("Window", message)
        self.assertIsNone(check(state, opened, now)[1])
        self.assertIsNone(rain_expected(forecast, now+22000))
