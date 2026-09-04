from datetime import datetime, timezone
import unittest

from oathcast.application import CrossMinerRouter, RoutingError, extract_probability
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="route-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=timezone.utc),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        )
        self.capabilities = [
            MinerCapability("1", "oathcast-weather", "OathCast", "http://own", frozenset({"WEATHER_FORECAST"}), historical_reliability=0.5),
            MinerCapability("2", "external-alpha", "External Alpha", "http://alpha", frozenset({"WEATHER_FORECAST"}), historical_reliability=0.8),
            MinerCapability("3", "external-beta", "External Beta", "http://beta", frozenset({"WEATHER_FORECAST"}), historical_reliability=0.7),
        ]

    def test_external_answers_change_the_application_decision(self):
        router = CrossMinerRouter(
            self.capabilities,
            clients={
                "oathcast-weather": lambda question: {"content": "90%", "probability": 0.9},
                "external-alpha": lambda question: {"content": "20%", "probability": 0.2},
                "external-beta": lambda question: {"content": "30%", "probability": 0.3},
            },
            own_slugs={"oathcast-weather"},
            clock=lambda: datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        )
        decision = router.decide(self.question)
        self.assertTrue(decision.used_external_miner)
        self.assertTrue(decision.external_influence)
        self.assertFalse(decision.event_likely)
        self.assertEqual(len(decision.replies), 3)
        self.assertEqual(decision.replies[1].raw_response["probability"], 0.2)
        self.assertTrue(decision.application_request_id.startswith("app-"))
        self.assertEqual(
            {reply.request_id for reply in decision.replies},
            {decision.application_request_id},
        )

    def test_decision_threshold_is_applied_and_persisted_in_the_decision(self):
        router = CrossMinerRouter(
            self.capabilities[:2],
            clients={
                "oathcast-weather": lambda question: {"probability": 0.7},
                "external-alpha": lambda question: {"probability": 0.7},
            },
            own_slugs={"oathcast-weather"},
        )

        decision = router.decide(self.question, decision_threshold=0.7)

        self.assertTrue(decision.event_likely)
        self.assertEqual(decision.recommended_action, "plan_for_event")
        self.assertEqual(decision.to_dict()["decision_threshold"], 0.7)

    def test_application_survives_with_owned_miner_disabled(self):
        router = CrossMinerRouter(
            self.capabilities,
            clients={
                "oathcast-weather": lambda question: {"probability": 0.9},
                "external-alpha": lambda question: {"content": "20%"},
                "external-beta": lambda question: {"content": "30%"},
            },
            own_slugs={"oathcast-weather"},
        )
        decision = router.decide(self.question, disable_owned=True)
        self.assertTrue(decision.used_external_miner)
        self.assertTrue(decision.external_influence)
        self.assertEqual(len(decision.replies), 2)

    def test_router_rejects_an_owner_only_loop(self):
        router = CrossMinerRouter(
            self.capabilities[:1],
            clients={"oathcast-weather": lambda question: {"probability": 0.9}},
            own_slugs={"oathcast-weather"},
        )
        with self.assertRaises(RoutingError):
            router.decide(self.question)

    def test_telegraph_client_uses_discovered_endpoint(self):
        from oathcast.application import TelegraphMinerClient

        capability = MinerCapability(
            "211",
            "openweathermap",
            "OpenWeatherMap",
            "https://dispatcher.example",
            frozenset({"WEATHER_FORECAST"}),
            endpoint_name="forecast",
        )
        calls = []

        class PaymentClient:
            def request_miner(self, miner_id, endpoint, params):
                calls.append((miner_id, endpoint, params))
                return type("Response", (), {"body": {"probability": 0.4}})()

        response = TelegraphMinerClient(capability, PaymentClient())(self.question)
        self.assertEqual(response["probability"], 0.4)
        self.assertEqual(calls[0][0:2], ("211", "forecast"))

    def test_telegraph_client_uses_weatherapi_miner_schema_parameters(self):
        from oathcast.application import TelegraphMinerClient

        capability = MinerCapability(
            "212",
            "weatherapi",
            "WeatherAPI",
            "https://dispatcher.example",
            frozenset({"WEATHER_FORECAST"}),
        )
        calls = []

        class PaymentClient:
            def request_miner(self, miner_id, endpoint, params):
                calls.append((miner_id, endpoint, params))
                return type("Response", (), {"body": {"probability": 0.4}})()

        TelegraphMinerClient(capability, PaymentClient())(self.question)
        self.assertEqual(
            calls,
            [("212", "forecast", {"q": "6.524400,3.379200", "days": "1"})],
        )

    def test_telegraph_client_records_settled_application_provenance(self):
        from oathcast.application import TelegraphMinerClient
        from oathcast.demand import DemandLedger

        capability = MinerCapability(
            "211",
            "openweathermap",
            "OpenWeatherMap",
            "https://dispatcher.example",
            frozenset({"WEATHER_FORECAST"}),
            endpoint_name="forecast",
        )
        ledger = DemandLedger(":memory:")

        class Response:
            status = 200
            settlement_proof = "settled-proof"
            settlement_verified = True
            settlement_verification = "verified"
            body = {"probability": 0.4}

        class PaymentClient:
            def request_miner(self, miner_id, endpoint, params, request_headers=None):
                self.request_headers = request_headers
                return Response()

        payment_client = PaymentClient()
        response = TelegraphMinerClient(
            capability,
            payment_client,
            demand_ledger=ledger,
        ).request_with_id(self.question, "app-settled")
        self.assertEqual(response["probability"], 0.4)
        self.assertEqual(payment_client.request_headers["X-OathCast-Application-Request-ID"], "app-settled")
        self.assertEqual(ledger.summary()["local_candidate_events"], 1)
        event = ledger.list_events()[0]
        self.assertEqual(event["question_event_id"], self.question.event_id)
        self.assertTrue(event["local_candidate"])
        self.assertEqual(event["settlement_verification"], "verified")
        self.assertEqual(len(event["protocol_receipt_sha256"]), 64)

    def test_header_presence_without_verified_settlement_is_not_a_local_candidate(self):
        from oathcast.application import TelegraphMinerClient
        from oathcast.demand import DemandLedger

        capability = MinerCapability(
            "211",
            "openweathermap",
            "OpenWeatherMap",
            "https://dispatcher.example",
            frozenset({"WEATHER_FORECAST"}),
            endpoint_name="forecast",
        )
        ledger = DemandLedger(":memory:")

        class Response:
            status = 200
            settlement_proof = "header-only"
            settlement_verified = False
            settlement_verification = "unverified"
            body = {"probability": 0.4}

        class PaymentClient:
            def request_miner(self, miner_id, endpoint, params, request_headers=None):
                return Response()

        result = TelegraphMinerClient(
            capability,
            PaymentClient(),
            demand_ledger=ledger,
        )(self.question)
        self.assertEqual(result["probability"], 0.4)
        self.assertEqual(ledger.summary()["local_candidate_events"], 0)
        self.assertEqual(ledger.list_events()[0]["payment_status"], "paid_unverified")

    def test_external_response_normalizer_accepts_common_weather_shapes(self):
        self.assertAlmostEqual(extract_probability({"chance_of_rain": 70}), 0.7)
        self.assertAlmostEqual(
            extract_probability({"forecast": {"precipitation_probability": 0.63}}),
            0.63,
        )
        self.assertAlmostEqual(
            extract_probability({"choices": [{"message": {"content": "Rain chance: 42%"}}]}),
            0.42,
        )


if __name__ == "__main__":
    unittest.main()
