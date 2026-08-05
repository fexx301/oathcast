import json
from pathlib import Path
import unittest

from oathcast.discovery import discover_weather_miners, integration_records, load_registry_snapshot


ROOT = Path(__file__).resolve().parents[1]


class DiscoveryTests(unittest.TestCase):
    def test_discovery_filters_owned_inactive_and_wrong_intent_records(self):
        records = load_registry_snapshot(ROOT / "fixtures" / "miner_registry.json")
        discovered = discover_weather_miners(records, own_slugs={"oathcast-weather"})
        self.assertEqual(
            [miner.slug for miner in discovered],
            ["independent-weather-alpha", "independent-weather-beta"],
        )
        self.assertTrue(all(miner.supports_weather for miner in discovered))

    def test_discovery_accepts_nested_semantics_shape(self):
        discovered = discover_weather_miners(
            [
                {
                    "id": 1,
                    "slug": "nested-weather",
                    "base_url": "https://example.test",
                    "semantics": {"supported_intents": ["WEATHER_FORECAST"]},
                    "status": "active",
                }
            ]
        )
        self.assertEqual(discovered[0].slug, "nested-weather")

    def test_discovery_accepts_live_activation_status_and_selects_forecast_endpoint(self):
        discovered = discover_weather_miners(
            [
                {
                    "id": 211,
                    "slug": "openweathermap",
                    "name": "OpenWeatherMap",
                    "base_url": "https://api.openweathermap.org/data/2.5",
                    "supported_intents": ["WEATHER_CHECK", "WEATHER_FORECAST"],
                    "activation_status": "active",
                    "min_price_micro_usdc": 10000,
                    "endpoints": [{"path": "/weather"}, {"path": "/forecast"}],
                }
            ]
        )
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].endpoint_path, "/forecast")
        self.assertEqual(discovered[0].endpoint_name, "forecast")
        self.assertEqual(discovered[0].min_price_micro_usdc, 10000)

    def test_discovery_accepts_live_predict_endpoint_without_a_direct_base_url(self):
        discovered = discover_weather_miners(
            [
                {
                    "id": 18,
                    "slug": "bittensor-sn18-zeus",
                    "supported_intents": ["WEATHER_FORECAST"],
                    "activation_status": "active",
                    "endpoints": [{"path": "/predict"}],
                }
            ]
        )
        self.assertEqual(discovered[0].endpoint_name, "predict")

    def test_discovery_normalizes_yaml_style_decimal_price(self):
        discovered = discover_weather_miners(
            [
                {
                    "id": 1,
                    "slug": "decimal-price",
                    "base_url": "https://example.test",
                    "supported_intents": ["WEATHER_FORECAST"],
                    "min_price_usdc": 0.01,
                }
            ]
        )
        self.assertEqual(discovered[0].min_price_micro_usdc, 10000)

    def test_discovery_does_not_guess_integer_decimal_price_as_micro_units(self):
        discovered = discover_weather_miners(
            [
                {
                    "id": 1,
                    "slug": "decimal-price",
                    "base_url": "https://example.test",
                    "supported_intents": ["WEATHER_FORECAST"],
                    "min_price_usdc": 10000,
                }
            ]
        )
        self.assertEqual(discovered[0].min_price_micro_usdc, 10_000_000_000)

    def test_integration_records_accepts_wrapped_response(self):
        records = integration_records({"data": [{"id": 1}, {"id": 2}]})
        self.assertEqual([record["id"] for record in records], [1, 2])


if __name__ == "__main__":
    unittest.main()
