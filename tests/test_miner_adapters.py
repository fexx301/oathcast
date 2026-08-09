from datetime import datetime, timezone
import unittest

from oathcast.application import CrossMinerRouter
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion
from oathcast.miner_adapters import (
    WeatherApiMinerAdapter,
    ZeusMinerAdapter,
)


UTC = timezone.utc


class MinerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="adapter-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=UTC),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=UTC),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

    def test_live_miner_request_urls_use_schema_specific_query_parameters(self):
        weather_url = WeatherApiMinerAdapter().build_url(
            "https://dispatcher.example/v1/212",
            self.question,
        )
        zeus_url = ZeusMinerAdapter().build_url(
            "https://dispatcher.example/v1/18",
            self.question,
        )
        self.assertEqual(
            weather_url,
            "https://dispatcher.example/v1/212/forecast?"
            "q=6.524400%2C3.379200&days=1",
        )
        self.assertEqual(
            zeus_url,
            "https://dispatcher.example/v1/18/predict?"
            "lat=6.524400&lon=3.379200&hourly=2t&forecast_hours=4",
        )

    def test_weatherapi_normalizes_nested_chance_of_rain_at_requested_time(self):
        result = WeatherApiMinerAdapter().parse_response(
            {
                "location": {"tz_id": "UTC"},
                "forecast": {
                    "forecastday": [
                        {
                            "date": "2026-08-17",
                            "hour": [
                                {"time": "2026-08-17 15:00", "chance_of_rain": 66},
                                {"time": "2026-08-17 16:00", "chance_of_rain": 5},
                            ],
                        }
                    ]
                },
            },
            self.question,
        )
        self.assertAlmostEqual(result.probability, 0.66)
        self.assertTrue(result.probability_comparable)
        self.assertTrue(result.has_comparable_probability)

    def test_zeus_temperature_context_is_excluded_from_probability_consensus(self):
        capabilities = [
            MinerCapability(
                "18",
                "bittensor-sn18-zeus",
                "Zeus",
                "https://zeus.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.9,
            ),
            MinerCapability(
                "999",
                "external-probability",
                "External",
                "https://external.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.5,
            ),
        ]
        router = CrossMinerRouter(
            capabilities,
            clients={
                "bittensor-sn18-zeus": lambda question: {
                    "reference_time": "2026-08-17T15:00:00Z",
                    "hourly": {
                        "time": [
                            "2026-08-17T15:00:00Z",
                            "2026-08-17T16:00:00Z",
                        ],
                        "2t": [299.2, 300.1],
                    },
                },
                "external-probability": lambda question: {"probability": 0.2},
            },
            own_slugs=set(),
            clock=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

        decision = router.decide(self.question)

        self.assertAlmostEqual(decision.aggregate_probability, 0.2)
        zeus_reply = decision.replies[0]
        self.assertIsNone(zeus_reply.probability)
        self.assertFalse(zeus_reply.probability_comparable)
        self.assertFalse(zeus_reply.valid)
        self.assertIn("supporting context only", zeus_reply.validity_reason)

    def test_zeus_enters_consensus_when_response_has_precipitation_probability(self):
        capabilities = [
            MinerCapability(
                "18",
                "bittensor-sn18-zeus",
                "Zeus",
                "https://zeus.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.9,
            ),
            MinerCapability(
                "999",
                "external-probability",
                "External",
                "https://external.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.5,
            ),
        ]
        router = CrossMinerRouter(
            capabilities,
            clients={
                "bittensor-sn18-zeus": lambda question: {
                    "hourly": {
                        "time": [
                            "2026-08-17T15:00:00Z",
                            "2026-08-17T16:00:00Z",
                        ],
                        "2t": [299.2, 300.1],
                        "precipitation_probability": [0.8, 0.1],
                    }
                },
                "external-probability": lambda question: {"probability": 0.2},
            },
            own_slugs=set(),
            clock=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

        decision = router.decide(self.question)

        self.assertAlmostEqual(decision.aggregate_probability, 0.5857)
        zeus_reply = decision.replies[0]
        self.assertAlmostEqual(zeus_reply.probability, 0.8)
        self.assertTrue(zeus_reply.probability_comparable)
        self.assertTrue(zeus_reply.valid)


if __name__ == "__main__":
    unittest.main()
