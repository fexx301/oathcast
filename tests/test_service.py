import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unittest.mock
from urllib.parse import urlencode

from oathcast.forecast import ForecastQuestion
from oathcast.receipts import (
    ReceiptConflict,
    ReceiptStoreFull,
    ReceiptTampering,
    SqliteReceiptStore,
    receipt_digest,
)
from oathcast.service import (
    ForecastCutoffPassed,
    ForecastRequestHandler,
    ForecastService,
    MAX_PROVIDER_BODY_BYTES,
    MAX_QUERY_LENGTH,
    ProviderUnavailable,
    ReceiptStoreUnavailable,
    RequestRateLimiter,
    authorization_valid,
    fetch_json,
    question_from_query,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
FIXED_NOW = datetime(2026, 8, 17, 11, tzinfo=timezone.utc)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def valid_query_params():
    return {
        "location_name": ["Lagos"],
        "lat": ["6.5244"],
        "lon": ["3.3792"],
        "start": ["2026-08-17T15:00:00Z"],
        "end": ["2026-08-17T16:00:00Z"],
        "cutoff": ["2026-08-17T12:00:00Z"],
    }


class _FakeResponse:
    """Minimal stand-in for the object `urlopen` yields as a context manager."""

    def __init__(self, body: bytes, content_length=None):
        self._body = body
        self._position = 0
        if content_length is None:
            self.headers = {}
        else:
            self.headers = {"Content-Length": content_length}

    def read(self, amount=None):
        if amount is None:
            chunk = self._body[self._position :]
        else:
            chunk = self._body[self._position : self._position + amount]
        self._position += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class ProviderBodyCapTests(unittest.TestCase):
    """`fetch_json` must not read an unbounded provider body into memory."""

    def _fetch(self, body: bytes, *, content_length=None, cap=64):
        response = _FakeResponse(body, content_length)
        with unittest.mock.patch(
            "oathcast.service.urlopen", return_value=response
        ):
            return fetch_json("https://provider.example/x", max_body_bytes=cap)

    def test_default_cap_is_two_megabytes(self):
        self.assertEqual(MAX_PROVIDER_BODY_BYTES, 2 * 1024 * 1024)

    def test_small_body_is_parsed(self):
        self.assertEqual(self._fetch(b'{"ok":true}'), {"ok": True})

    def test_body_exactly_at_the_cap_is_accepted(self):
        padding = "a" * (64 - len('{"k":""}'))
        body = json.dumps({"k": padding}, separators=(",", ":")).encode("utf-8")
        self.assertEqual(len(body), 64)
        self.assertEqual(self._fetch(body), {"k": padding})

    def test_body_one_byte_over_the_cap_is_rejected(self):
        padding = "a" * (65 - len('{"k":""}'))
        body = json.dumps({"k": padding}, separators=(",", ":")).encode("utf-8")
        self.assertEqual(len(body), 65)
        with self.assertRaises(ValueError) as caught:
            self._fetch(body)
        self.assertIn("byte cap", str(caught.exception))

    def test_oversized_body_is_not_silently_truncated_into_valid_json(self):
        """A truncating read could yield a parse error that hides the real cause."""

        body = b'{"a":"' + b"x" * 500 + b'"}'
        with self.assertRaises(ValueError) as caught:
            self._fetch(body)
        self.assertIn("byte cap", str(caught.exception))

    def test_declared_content_length_over_cap_is_rejected_before_reading(self):
        response = _FakeResponse(b'{"ok":true}', content_length="999999")
        with unittest.mock.patch("oathcast.service.urlopen", return_value=response):
            with self.assertRaises(ValueError) as caught:
                fetch_json("https://provider.example/x", max_body_bytes=64)
        self.assertIn("byte cap", str(caught.exception))
        self.assertEqual(response._position, 0, "body was read despite the header")

    def test_unparsable_content_length_falls_back_to_reading_under_the_cap(self):
        self.assertEqual(
            self._fetch(b'{"ok":true}', content_length="not-a-number"),
            {"ok": True},
        )

    def test_non_object_payload_is_still_rejected(self):
        with self.assertRaises(ValueError):
            self._fetch(b"[1,2,3]")

    def test_non_positive_cap_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            fetch_json("https://provider.example/x", max_body_bytes=0)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion.from_dict(load_json("question.json"))

    def test_receipt_write_probe_interval_must_be_finite_and_non_negative(self):
        for invalid in (float("nan"), float("inf"), float("-inf"), -0.001):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "finite and not negative"):
                    ForecastService(
                        provider_order=["open_meteo"],
                        receipt_write_probe_interval_seconds=invalid,
                    )

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

    def test_default_event_id_is_stable_and_bound_to_the_canonical_question(self):
        first = question_from_query(valid_query_params())
        repeated = question_from_query(valid_query_params())
        changed = valid_query_params()
        changed["location_name"] = ["Ibadan"]
        changed_question = question_from_query(changed)

        self.assertEqual(first.event_id, repeated.event_id)
        self.assertTrue(first.event_id.startswith("request-"))
        self.assertEqual(len(first.event_id), len("request-") + 64)
        self.assertNotEqual(first.event_id, changed_question.event_id)

    def test_generated_event_id_preserves_receipt_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            service = ForecastService(
                fetcher=lambda url: calls.append(url) or load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(Path(directory) / "receipts.sqlite3"),
                clock=lambda: FIXED_NOW,
            )
            first_question = question_from_query(valid_query_params())
            first = service.forecast(first_question, request_id="first")
            replay = service.forecast(
                question_from_query(valid_query_params()),
                request_id="retry",
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(replay.request_id, "first")
            self.assertEqual(replay.to_public_response(), first.to_public_response())

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

    def test_query_parser_rejects_nonfinite_and_out_of_range_coordinates(self):
        for field, value, expected in (
            ("lat", "nan", "latitude"),
            ("lat", "91", "latitude"),
            ("lon", "inf", "longitude"),
            ("lon", "-181", "longitude"),
        ):
            params = valid_query_params()
            params[field] = [value]
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError,
                expected,
            ):
                question_from_query(params)

    def test_query_parser_rejects_duplicate_and_oversized_identity_parameters(self):
        duplicate = valid_query_params()
        duplicate["lat"] = ["6.5", "6.6"]
        with self.assertRaisesRegex(ValueError, "lat.*exactly once"):
            question_from_query(duplicate)

        aliases = valid_query_params()
        aliases["latitude"] = ["6.5244"]
        with self.assertRaisesRegex(ValueError, "latitude.*lat"):
            question_from_query(aliases)

        oversized_event = valid_query_params()
        oversized_event["event_id"] = ["e" * 129]
        with self.assertRaisesRegex(ValueError, "event_id.*128"):
            question_from_query(oversized_event)

        oversized_location = valid_query_params()
        oversized_location["location_name"] = ["L" * 257]
        with self.assertRaisesRegex(ValueError, "location_name.*256"):
            question_from_query(oversized_location)

    def test_get_query_validation_returns_clear_400_errors(self):
        service = ForecastService(provider_order=["open_meteo"])

        class RecordingHandler(ForecastRequestHandler):
            def _send_json(self, status, payload, *, headers=None):
                self.responses.append((status, payload, headers or {}))

        def request(path):
            handler = object.__new__(RecordingHandler)
            handler.service = service
            handler.client_address = ("203.0.113.10", 0)
            handler.path = path
            handler.headers = {}
            handler.responses = []
            handler.do_GET()
            return handler.responses[0]

        invalid_coordinate = valid_query_params()
        invalid_coordinate["lat"] = ["nan"]
        status, payload, _ = request(
            "/v1/forecast/point?" + urlencode(invalid_coordinate, doseq=True)
        )
        self.assertEqual(status, 400)
        self.assertIn("latitude", payload["error"])

        status, payload, _ = request(
            "/v1/forecast/point?" + "x" * (MAX_QUERY_LENGTH + 1)
        )
        self.assertEqual(status, 400)
        self.assertIn("query string", payload["error"])

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

    def test_forwarded_rate_limit_identity_requires_a_trusted_socket_peer(self):
        service = ForecastService(
            provider_order=["open_meteo"],
            trusted_proxy_networks=["10.0.0.0/8"],
        )
        self.assertEqual(
            service.rate_limit_identity(
                remote_address="198.51.100.10",
                forwarded_for="203.0.113.10",
            ),
            "198.51.100.10",
        )
        self.assertEqual(
            service.rate_limit_identity(
                remote_address="10.1.2.3",
                forwarded_for="203.0.113.10, 10.1.2.3",
            ),
            "203.0.113.10",
        )
        self.assertNotEqual(
            service.rate_limit_key(
                remote_address="127.0.0.1",
                forwarded_for="203.0.113.10",
            ),
            service.rate_limit_key(
                remote_address="127.0.0.1",
                forwarded_for="203.0.113.11",
            ),
        )
        self.assertEqual(
            service.rate_limit_identity(
                remote_address="127.0.0.1",
                forwarded_for="not-an-ip",
            ),
            "127.0.0.1",
        )

    def test_forwarded_public_clients_do_not_share_auth_failure_buckets(self):
        service = ForecastService(
            provider_order=["open_meteo"],
            auth_token="correct-secret",
            require_auth=True,
            auth_failure_limit_per_minute=1,
        )

        class RecordingHandler(ForecastRequestHandler):
            def _send_json(self, status, payload, *, headers=None):
                self.responses.append((status, payload, headers or {}))

        def request(forwarded_for):
            handler = object.__new__(RecordingHandler)
            handler.service = service
            handler.client_address = ("127.0.0.1", 0)
            handler.path = "/v1/forecast/point?event_id=auth-test"
            handler.headers = {
                "Authorization": "Bearer invalid",
                "X-Forwarded-For": forwarded_for,
            }
            handler.responses = []
            handler.do_GET()
            return handler.responses[0][0]

        self.assertEqual(request("203.0.113.10"), 401)
        self.assertEqual(request("203.0.113.11"), 401)
        self.assertEqual(request("203.0.113.10"), 429)

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

    def test_receipt_replay_returns_the_frozen_response_after_renderer_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(path),
                clock=lambda: FIXED_NOW,
            )
            first = first_service.forecast(self.question, request_id="first")
            frozen_response = first.to_public_response()

            replay_service = ForecastService(
                fetcher=lambda url: self.fail("replay must not call the provider"),
                provider_order=["open_meteo"],
                receipt_store=SqliteReceiptStore(path),
                clock=lambda: datetime(2026, 8, 17, 13, tzinfo=timezone.utc),
            )
            with unittest.mock.patch(
                "oathcast.service.public_response",
                return_value={"content": "changed renderer", "probability": 0.01},
            ):
                replay = replay_service.forecast(self.question, request_id="retry")
                self.assertEqual(replay.to_public_response(), frozen_response)

    def test_persistence_failure_is_not_mislabeled_as_provider_unavailable(self):
        class BrokenStore:
            def get(self, event_id):
                return None

            def save(self, receipt):
                raise sqlite3.OperationalError("database is readonly")

        service = ForecastService(
            fetcher=lambda url: load_json("open_meteo.json"),
            provider_order=["open_meteo"],
            receipt_store=BrokenStore(),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaises(ReceiptStoreUnavailable):
            service.forecast(self.question, request_id="persist-failure")

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


class ReceiptCapacityHandlerTests(unittest.TestCase):
    """A full receipt store must fail closed and be visible before it fills."""

    def _handler(self, service, path, *, headers=None):
        class RecordingHandler(ForecastRequestHandler):
            def _send_json(self, status, payload, *, headers=None):
                self.responses.append((status, payload, headers or {}))

        handler = object.__new__(RecordingHandler)
        handler.service = service
        handler.client_address = ("203.0.113.10", 0)
        handler.path = path
        handler.headers = headers or {}
        handler.responses = []
        handler.do_GET()
        return handler.responses[0]

    def test_legacy_receipt_without_public_response_fails_closed_with_503(self):
        """Never reconstruct an old response with today's renderer."""

        question = ForecastQuestion.from_dict(load_json("question.json"))
        source = ForecastService(
            fetcher=lambda url: load_json("open_meteo.json"),
            provider_order=["open_meteo"],
            clock=lambda: FIXED_NOW,
        ).forecast(question, request_id="legacy-original")
        legacy_receipt = {
            "schema_version": 1,
            "created_at": "2026-08-12T00:00:00Z",
            "request_id": source.request_id,
            "question": source.question.to_dict(),
            "forecast": source.forecast.to_dict(),
            "raw_payload": source.raw_payload,
        }
        legacy_receipt["receipt_sha256"] = receipt_digest(legacy_receipt)

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            store.save(legacy_receipt)
            service = ForecastService(
                fetcher=lambda url: self.fail(
                    "a legacy receipt must not fall through to the provider"
                ),
                provider_order=["open_meteo"],
                receipt_store=store,
                require_auth=False,
                clock=lambda: FIXED_NOW,
            )
            params = valid_query_params()
            params["event_id"] = [question.event_id]
            with self.assertLogs("oathcast.service", level="ERROR"):
                status, payload, headers = self._handler(
                    service,
                    "/v1/forecast/point?" + urlencode(params, doseq=True),
                )

            self.assertEqual(status, 503)
            self.assertEqual(payload["error"], "receipt_store_unavailable")
            self.assertIn("X-OathCast-Request-ID", headers)
            store.close()

    def test_a_full_receipt_store_returns_507_rather_than_an_unrecorded_forecast(self):
        # Serving a 200 with no receipt would hand out a forecast that cannot
        # later be replayed or verified -- the opposite of what this service
        # claims to sell. Fail closed instead.
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3", max_rows=1)
            service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=store,
                require_auth=False,
                clock=lambda: FIXED_NOW,
            )
            first = valid_query_params()
            first["event_id"] = ["cap-first"]
            status, _, _ = self._handler(
                service, "/v1/forecast/point?" + urlencode(first, doseq=True)
            )
            self.assertEqual(status, 200)

            second = valid_query_params()
            second["event_id"] = ["cap-second"]
            status, payload, headers = self._handler(
                service, "/v1/forecast/point?" + urlencode(second, doseq=True)
            )
            self.assertEqual(status, 507)
            self.assertEqual(payload["error"], "receipt_store_full")
            self.assertIn("X-OathCast-Request-ID", headers)

            # The already-recorded event still replays at capacity.
            status, _, _ = self._handler(
                service, "/v1/forecast/point?" + urlencode(first, doseq=True)
            )
            self.assertEqual(status, 200)
            store.close()

    def test_readyz_reports_receipt_capacity_and_goes_unready_when_full(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3", max_rows=1)
            service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=store,
                require_auth=False,
                clock=lambda: FIXED_NOW,
            )
            status, payload, _ = self._handler(service, "/readyz")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ready"])
            self.assertTrue(payload["receipt_store"]["accepting_new_receipts"])
            self.assertEqual(payload["receipt_store"]["max_rows"], 1)
            self.assertEqual(
                payload["receipt_store_write"],
                {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "cached": False,
                },
            )

            params = valid_query_params()
            params["event_id"] = ["ready-first"]
            self._handler(service, "/v1/forecast/point?" + urlencode(params, doseq=True))

            status, payload, _ = self._handler(service, "/readyz")
            self.assertEqual(status, 503)
            self.assertFalse(payload["ready"])
            self.assertFalse(payload["receipt_store"]["accepting_new_receipts"])
            self.assertEqual(payload["receipt_store"]["rows_remaining"], 0)
            store.close()

    def test_healthz_stays_200_when_the_receipt_store_is_full(self):
        # Docker's HEALTHCHECK probes /healthz. If a full store failed the
        # liveness probe, the container would restart-loop instead of showing
        # an operator a stable, diagnosable 507/503.
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3", max_rows=1)
            service = ForecastService(
                fetcher=lambda url: load_json("open_meteo.json"),
                provider_order=["open_meteo"],
                receipt_store=store,
                require_auth=False,
                clock=lambda: FIXED_NOW,
            )
            params = valid_query_params()
            params["event_id"] = ["health-first"]
            self._handler(service, "/v1/forecast/point?" + urlencode(params, doseq=True))
            self.assertFalse(store.capacity()["accepting_new_receipts"])

            status, payload, _ = self._handler(service, "/healthz")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            store.close()

    def test_readyz_is_unready_when_capacity_cannot_be_read(self):
        class BrokenStore:
            def capacity(self):
                raise sqlite3.OperationalError("database is locked")

        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=BrokenStore(),
            require_auth=False,
        )
        status, payload, _ = self._handler(service, "/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["receipt_store"], {"error": "capacity_unavailable"})

    def test_readyz_is_unready_when_transactional_write_probe_fails(self):
        class ReadableButNotWritableStore:
            def capacity(self):
                return {"accepting_new_receipts": True}

            def write_readiness(self):
                return {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "error": "write_unavailable",
                }

        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=ReadableButNotWritableStore(),
            require_auth=False,
        )
        status, payload, _ = self._handler(service, "/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["receipt_store_write"]["error"], "write_unavailable")

    def test_readyz_caches_write_probe_between_requests(self):
        class ProbeStore:
            def __init__(self):
                self.calls = 0

            def capacity(self):
                return {"accepting_new_receipts": True}

            def write_readiness(self):
                self.calls += 1
                return {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                }

        store = ProbeStore()
        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=store,
            require_auth=False,
            receipt_write_probe_interval_seconds=60,
        )
        first_status, first_payload, _ = self._handler(service, "/readyz")
        second_status, second_payload, _ = self._handler(service, "/readyz")
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(store.calls, 1)
        self.assertFalse(first_payload["receipt_store_write"]["cached"])
        self.assertTrue(second_payload["receipt_store_write"]["cached"])

    def test_readyz_rejects_a_probe_that_claims_ready_without_rollback(self):
        class InconsistentProbeStore:
            def capacity(self):
                return {"accepting_new_receipts": True}

            def write_readiness(self):
                return {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": False,
                }

        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=InconsistentProbeStore(),
            require_auth=False,
        )
        status, payload, _ = self._handler(service, "/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertFalse(payload["receipt_store_write"]["ready"])
        self.assertEqual(
            payload["receipt_store_write"]["error"], "rollback_unverified"
        )

    def test_receipt_store_failure_returns_sanitized_json_and_logs_types(self):
        class BrokenStore:
            def get(self, event_id):
                raise sqlite3.OperationalError("secret path /data/private.sqlite3 is readonly")

        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=BrokenStore(),
            require_auth=False,
        )
        with self.assertLogs("oathcast.service", level="ERROR") as captured:
            status, payload, headers = self._handler(
                service,
                "/v1/forecast/point?" + urlencode(valid_query_params(), doseq=True),
            )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "receipt_store_unavailable")
        self.assertIn("request_id", payload)
        self.assertEqual(headers["X-OathCast-Request-ID"], payload["request_id"])
        log_payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(log_payload["event"], "receipt_store_unavailable")
        self.assertEqual(log_payload["error_type"], "ReceiptStoreUnavailable")
        self.assertEqual(log_payload["cause_type"], "OperationalError")
        self.assertNotIn("private.sqlite3", captured.output[0])

    def test_receipt_tampering_returns_sanitized_500(self):
        class TamperedStore:
            def get(self, event_id):
                raise ReceiptTampering("stored secret receipt bytes changed")

        service = ForecastService(
            provider_order=["open_meteo"],
            receipt_store=TamperedStore(),
            require_auth=False,
        )
        with self.assertLogs("oathcast.service", level="ERROR") as captured:
            status, payload, _ = self._handler(
                service,
                "/v1/forecast/point?" + urlencode(valid_query_params(), doseq=True),
            )
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "receipt_integrity_failure")
        self.assertNotIn("secret receipt", captured.output[0])


if __name__ == "__main__":
    unittest.main()
