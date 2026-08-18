import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from oathcast.adapters import OpenMeteoAdapter, OpenWeatherAdapter, WeatherApiAdapter
from oathcast.adapters.base import AdapterError, probability_from_percent
from oathcast.forecast import ForecastQuestion


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
ISSUED_AT = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion.from_dict(load_json("question.json"))

    def test_open_meteo_normalizes_documented_preceding_hour_probability(self):
        forecast = OpenMeteoAdapter().parse(
            load_json("open_meteo.json"),
            self.question,
            issued_at=ISSUED_AT,
        )
        self.assertEqual(forecast.provider, "open_meteo")
        self.assertAlmostEqual(forecast.probability, 0.70)
        self.assertEqual(forecast.event_equivalence, "documented_match")
        self.assertEqual(forecast.provider_model, "dev_fixture_open_meteo")

    def test_weatherapi_normalizes_local_time_to_utc(self):
        forecast = WeatherApiAdapter().parse(
            load_json("weatherapi.json"),
            self.question,
            issued_at=ISSUED_AT,
        )
        self.assertEqual(forecast.provider, "weatherapi")
        self.assertAlmostEqual(forecast.probability, 0.66)
        self.assertEqual(forecast.event_equivalence, "unverified")

    def test_openweather_normalizes_unix_time_to_utc(self):
        forecast = OpenWeatherAdapter().parse(
            load_json("openweather.json"),
            self.question,
            issued_at=ISSUED_AT,
        )
        self.assertEqual(forecast.provider, "openweather_onecall")
        self.assertAlmostEqual(forecast.probability, 0.72)
        self.assertEqual(forecast.event_equivalence, "unverified")

    def test_adapters_refuse_to_guess_when_exact_native_hour_is_missing(self):
        payload = load_json("openweather.json")
        payload["hourly"] = [payload["hourly"][1]]
        with self.assertRaises(AdapterError):
            OpenWeatherAdapter().parse(payload, self.question, issued_at=ISSUED_AT)

    def test_percentage_probability_rejects_boolean_values(self):
        for value in (True, False):
            with self.subTest(value=value), self.assertRaises(AdapterError):
                probability_from_percent(value, "fixture")

    def test_urls_keep_provider_credentials_at_adapter_boundary(self):
        self.assertIn("api.open-meteo.com", OpenMeteoAdapter().build_url(self.question))
        self.assertIn("key=weather-key", WeatherApiAdapter().build_url(self.question, "weather-key"))
        self.assertIn("appid=openweather-key", OpenWeatherAdapter().build_url(self.question, "openweather-key"))


if __name__ == "__main__":
    unittest.main()
