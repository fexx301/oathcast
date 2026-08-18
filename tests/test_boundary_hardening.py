from datetime import datetime, timezone
from urllib.error import HTTPError
import json
import unittest
import unittest.mock

from oathcast.application import CrossMinerRouter, HttpMinerClient
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion
from oathcast.payment import urllib_transport


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self.body = body
        self.headers = headers or {}
        self.status = status
        self.read_amounts: list[int | None] = []

    def read(self, amount: int | None = None) -> bytes:
        self.read_amounts.append(amount)
        return self.body if amount is None else self.body[:amount]

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _question() -> ForecastQuestion:
    return ForecastQuestion(
        event_id="boundary-test",
        location_name="Lagos",
        latitude=6.5244,
        longitude=3.3792,
        horizon_start=datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
        horizon_end=datetime(2026, 8, 17, 16, tzinfo=timezone.utc),
        forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
    )


def _capability() -> MinerCapability:
    return MinerCapability(
        "212",
        "external-alpha",
        "External Alpha",
        "https://miner.example",
        frozenset({"WEATHER_FORECAST"}),
        endpoint_path="/forecast",
    )


class ApplicationResponseBoundaryTests(unittest.TestCase):
    def test_exactly_at_the_cap_is_accepted_with_one_byte_overflow_probe(self):
        body = b'{"ok":true}'
        response = _FakeResponse(body)
        client = HttpMinerClient(
            _capability(),
            max_response_body_bytes=len(body),
        )

        with unittest.mock.patch("oathcast.application.urlopen", return_value=response):
            payload = client(_question())

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(response.read_amounts, [len(body) + 1])

    def test_declared_oversize_body_is_rejected_before_reading(self):
        body = b'{"ok":true}'
        response = _FakeResponse(
            body,
            headers={"Content-Length": str(len(body) + 1)},
        )
        client = HttpMinerClient(
            _capability(),
            max_response_body_bytes=len(body),
        )

        with unittest.mock.patch("oathcast.application.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "byte cap"):
                client(_question())

        self.assertEqual(response.read_amounts, [])


class PaymentResponseBoundaryTests(unittest.TestCase):
    def test_non_positive_cap_is_rejected_before_network_io(self):
        with unittest.mock.patch("oathcast.payment.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "must be positive"):
                urllib_transport(
                    "GET",
                    "https://dispatcher.example/x",
                    {},
                    max_response_body_bytes=0,
                )

        urlopen.assert_not_called()

    def test_success_body_over_the_cap_is_rejected(self):
        response = _FakeResponse(b'{"ok":true}x')

        with unittest.mock.patch("oathcast.payment.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "byte cap"):
                urllib_transport(
                    "GET",
                    "https://dispatcher.example/x",
                    {},
                    max_response_body_bytes=len(b'{"ok":true}'),
                )

        self.assertEqual(response.read_amounts, [len(b'{"ok":true}') + 1])

    def test_http_error_body_obeys_the_same_declared_size_cap(self):
        body = _FakeResponse(b"payment required")
        error = HTTPError(
            "https://dispatcher.example/x",
            402,
            "Payment Required",
            {"Content-Length": "999"},
            body,
        )
        self.addCleanup(error.close)

        with unittest.mock.patch("oathcast.payment.urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "byte cap"):
                urllib_transport(
                    "GET",
                    "https://dispatcher.example/x",
                    {},
                    max_response_body_bytes=64,
                )

        self.assertEqual(body.read_amounts, [])


class RouterEvidenceBoundaryTests(unittest.TestCase):
    def test_exception_text_stays_out_of_evidence_and_structured_log(self):
        secret = "https://miner.example/forecast?api_key=top-secret"

        def fail(_question):
            raise RuntimeError(secret)

        capability = _capability()
        router = CrossMinerRouter(
            [capability],
            {capability.slug: fail},
            own_slugs=set(),
            require_external=False,
        )

        with self.assertLogs("oathcast.application", level="ERROR") as captured:
            reply = router._reply(capability, _question(), "app-boundary-test")

        evidence = json.dumps(reply.to_dict(), sort_keys=True)
        self.assertEqual(reply.error, "miner request failed (RuntimeError)")
        self.assertEqual(reply.validity_reason, reply.error)
        self.assertNotIn(secret, evidence)
        self.assertIn('"error_type":"RuntimeError"', captured.output[0])
        self.assertIn('"request_id":"app-boundary-test"', captured.output[0])
        self.assertNotIn(secret, captured.output[0])


if __name__ == "__main__":
    unittest.main()
