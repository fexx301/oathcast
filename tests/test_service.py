import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse

from oathcast.forecast import ForecastQuestion
from oathcast.receipts import ReceiptConflict, SqliteReceiptStore
from oathcast.service import (
    ForecastCutoffPassed,
    ForecastRequestHandler,
    ForecastService,
    ProviderUnavailable,
    RequestRateLimiter,
    authorization_valid,
    question_from_query,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
FIXED_NOW = datetime(2026, 8, 17, 11, tzinfo=timezone.utc)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion.from_dict(load_json("question.json"))

    def test_service_wraps_a_provider_and_keeps_provenance(self):
        def fake_fetcher(url):
            self.assertIn("api.open-meteo.com", url)
            return load_json("open_meteo.json")

        service = ForecastService(
            fetcher=fake_fetcher,
            provider_order=["open_meteo"],
            clock=lambda: FIXED_NOW,
        )
        result = service.forecast(self.question, request_id="req-1")
        self.assertEqual(result.forecast.provider, "open_meteo")
        self.assertAlmostEqual(result.to_public_response()["probability"], 0.7)
        self.assertEqual(result.provenance["request_id"], "req-1")
        self.assertEqual(len(result.provenance["raw_payload_sha256"]), 64)

    def test_service_fails_over_to_the_next_provider(self):
        def fake_fetcher(url):
            if "api.open-meteo.com" in url:
                raise RuntimeError("development outage")
            self.assertIn("weatherapi.com", url)
            return load_json("weatherapi.json")

        service = ForecastService(
            fetcher=fake_fetcher,
            provider_order=["open_meteo", "weatherapi"],
            api_keys={"weatherapi": "development-key"},
            allow_unverified_providers=True,
            clock=lambda: FIXED_NOW,
        )
        result = service.forecast(self.question, request_id="req-failover")
        self.assertEqual(result.forecast.provider, "weatherapi")
        self.assertAlmostEqual(result.forecast.probability, 0.66)

    def test_query_parser_accepts_short_aliases_without_future_ground_truth(self):
        question = question_from_query(
            {
                "event_id": ["q-1"],
                "location_name": ["Lagos"],
                "lat": ["6.5244"],
                "lon": ["3.3792"],
                "start": ["2026-08-17T15:00:00Z"],
                "end": ["2026-08-17T16:00:00Z"],
            }
        )
        self.assertEqual(question.event_id, "q-1")
        self.assertEqual(question.forecast_cutoff.isoformat(), "2026-08-17T14:00:00+00:00")

    def test_bearer_auth_is_optional_locally_but_fail_closed_when_configured(self):
        self.assertTrue(authorization_valid(None, None))
        self.assertTrue(authorization_valid("Bearer secret", "secret"))
        self.assertFalse(authorization_valid("Bearer wrong", "secret"))
        self.assertFalse(authorization_valid(None, "secret"))
        self.assertFalse(authorization_valid(None, None, require_auth=True))
        self.assertTrue(authorization_valid("Bearer next-secret", ("old-secret", "next-secret"), require_auth=True))
        self.assertFalse(authorization_valid("Bearer retired", ("old-secret", "next-secret"), require_auth=True))

        service = ForecastService(
            fetcher=lambda url: load_json("open_meteo.json"),
            provider_order=["open_meteo"],
            auth_token="secret",
            require_auth=True,
        )
        self.assertEqual(service.auth_token, "secret")

    def test_rate_limiter_enforces_a_window_and_returns_retry_after(self):
        ticks = iter([0.0, 1.0, 2.0, 61.0])
        limiter = RequestRateLimiter(2, clock=lambda: next(ticks))
        self.assertEqual(limiter.check("client"), (True, 0))
        self.assertEqual(limiter.check("client"), (True, 0))
        allowed, retry_after = limiter.check("client")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)
        self.assertEqual(limiter.check("client"), (True, 0))

    def test_rate_limiter_bounds_identity_buckets_and_expires_idle_keys(self):
        ticks = iter([0.0, 0.0, 0.0, 61.0])
        limiter = RequestRateLimiter(20, max_keys=2, clock=lambda: next(ticks))
        limiter.check("first")
        limiter.check("second")
        limiter.check("third")
        self.assertLessEqual(limiter.tracked_key_count, 2)
        limiter.check("fourth")
        self.assertEqual(limiter.tracked_key_count, 1)

    def test_rate_limit_key_does_not_depend_on_authorization_value(self):
        service = ForecastService(provider_order=["open_meteo"])
        self.assertEqual(
            service.rate_limit_key(remote_address="127.0.0.1"),
            service.rate_limit_key(remote_address="127.0.0.1"),
        )

    def test_rotating_invalid_authorization_headers_share_one_failure_bucket(self):
        service = ForecastService(
            provider_order=["open_meteo"],
            auth_token="correct-secret",
            require_auth=True,
            auth_failure_limit_per_minute=2,
        )

        class RecordingHandler(ForecastRequestHandler):
            def _send_json(self, status, payload, *, headers=None):
                self.responses.append((status, payload, headers or {}))

        handler = object.__new__(RecordingHandler)
        handler.service = service
        handler.client_address = ("127.0.0.1", 0)
        handler.path = "/v1/forecast/point?event_id=auth-test"
        handler.responses = []
        for index in range(3):
            handler.headers = {"Authorization": f"Bearer invalid-{index}"}
            handler.do_GET()
        self.assertEqual([response[0] for response in handler.responses], [401, 401, 429])

    def test_production_auth_cannot_start_without_a_secret(self):
        with self.assertRaises(ValueError):
            ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                auth_token="",
                require_auth=True,
            )

    def test_question_contract_matches_open_meteo_native_event(self):
        for field, value in (("operator", ">="), ("threshold_mm", 0.2), ("threshold_mm", float("nan")), ("threshold_mm", float("inf"))):
            data = load_json("question.json")
            data[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                ForecastQuestion.from_dict(data)

    def test_unverified_provider_cannot_be_a_production_failover(self):
        service = ForecastService(
            fetcher=lambda url: load_json("weatherapi.json"),
            provider_order=["weatherapi"],
            api_keys={"weatherapi": "development-key"},
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaises(ProviderUnavailable):
            service.forecast(self.question, request_id="unverified")

    def test_new_forecast_is_rejected_at_cutoff_before_fetching(self):
        calls = []
        service = ForecastService(
            fetcher=lambda url: calls.append(url) or load_json("open_meteo.json"),
            provider_order=["open_meteo"],
            clock=lambda: datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        )
        with self.assertRaises(ForecastCutoffPassed):
            service.forecast(self.question, request_id="too-late")
        self.assertEqual(calls, [])

    def test_response_completed_at_cutoff_is_not_persisted(self):
        clock_values = iter(
            [
                datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 17, 11, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=store,
                clock=lambda: next(clock_values),
            )
            with self.assertRaises(ForecastCutoffPassed):
                service.forecast(self.question, request_id="late-response")
            self.assertIsNone(store.get(self.question.event_id))

    def test_receipt_replays_identically_after_cutoff_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_calls = []
            first_service = ForecastService(
                fetcher=lambda url: first_calls.append(url) or load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(path),
                clock=lambda: FIXED_NOW,
            )
            first = first_service.forecast(self.question, request_id="first")
            self.assertEqual(len(first_calls), 1)

            replay_service = ForecastService(
                fetcher=lambda url: self.fail("replay must not call the provider"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(path),
                clock=lambda: datetime(2026, 8, 17, 13, tzinfo=timezone.utc),
            )
            replay = replay_service.forecast(self.question, request_id="retry")
            self.assertEqual(replay.to_public_response(), first.to_public_response())
            self.assertEqual(replay.request_id, "first")
            stored = replay_service.receipt_store.get(self.question.event_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["receipt_sha256"], replay_service.receipt_store.get(self.question.event_id)["receipt_sha256"])

    def test_receipt_rejects_same_event_id_with_a_different_question(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(path),
                clock=lambda: FIXED_NOW,
            )
            service.forecast(self.question, request_id="first")
            changed = dict(self.question.to_dict())
            changed["location_name"] = "Ibadan"
            conflicting_question = ForecastQuestion.from_dict(changed)
            with self.assertRaises(ReceiptConflict):
                service.forecast(conflicting_question, request_id="conflict")


if __name__ == "__main__":
    unittest.main()
