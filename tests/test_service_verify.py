from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

from oathcast.receipts import SqliteReceiptStore
from oathcast.service import ForecastService, VERIFY_PATH
from tests.test_service import RunningForecastServer, socket_get


FIXED_NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
LISBON = {"name": "Lisbon", "latitude": 38.71667, "longitude": -9.13333, "country": "Portugal"}


class FakeUpstream:
    """Open-Meteo geocoding and archive stand-in that records every URL."""

    def __init__(self, daily_value=3.4, fail=False):
        self.urls = []
        self.daily_value = daily_value
        self.fail = fail

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise TimeoutError("upstream timed out")
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if parsed.netloc.startswith("geocoding-api"):
            return {"results": [LISBON]}
        start = date.fromisoformat(query["start_date"])
        end = date.fromisoformat(query["end_date"])
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        field = query["daily"]
        unit = "°C" if "temperature" in field else "km/h" if "wind" in field else "mm"
        return {
            "latitude": 38.7,
            "longitude": -9.1,
            "timezone": "Europe/Lisbon",
            "daily_units": {"time": "iso8601", field: unit},
            "daily": {"time": days, field: [self.daily_value] * len(days)},
        }

    def count(self, host_prefix):
        return sum(urlparse(url).netloc.startswith(host_prefix) for url in self.urls)


def make_service(upstream, *, receipt_store=None, verify_enabled=True, require_auth=False):
    return ForecastService(
        fetcher=upstream,
        provider_order=["open_meteo"],
        receipt_store=receipt_store,
        require_auth=require_auth,
        auth_tokens=["secret-token"] if require_auth else None,
        clock=lambda: FIXED_NOW,
        verify_enabled=verify_enabled,
    )


def get(address, params, headers=()):
    status, body, response_headers = socket_get(address, f"{VERIFY_PATH}?{urlencode(params)}", list(headers))
    return status, json.loads(body), response_headers


CLAIM = {
    "location": "Lisbon",
    "date": "2026-09-15",
    "variable": "precipitation",
    "forecast_value": "12",
    "unit": "mm",
}


class VerifyRouteTests(unittest.TestCase):
    def test_route_is_hidden_when_disabled(self):
        with RunningForecastServer(make_service(FakeUpstream(), verify_enabled=False)) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual((status, body), (404, {"error": "not_found"}))

    def test_answer_keeps_the_claim_out_of_the_scored_text(self):
        upstream = FakeUpstream(daily_value=3.4)
        with RunningForecastServer(make_service(upstream)) as address:
            status, body, headers = get(address, CLAIM)
        self.assertEqual(status, 200)
        self.assertEqual(
            body["content"],
            "Lisbon, Portugal recorded 3.4 mm of precipitation on 15 September 2026, according to the "
            "Open-Meteo historical weather archive (ERA5 reanalysis).",
        )
        self.assertNotIn("12", body["content"])
        self.assertEqual(body["verdict"], "not_verified")
        self.assertIs(body["verified"], False)
        self.assertEqual((body["forecast_value"], body["observed_value"], body["tolerance"]), (12.0, 3.4, 3.0))
        self.assertIn("x-oathcast-request-id", headers)
        self.assertEqual((upstream.count("geocoding-api"), upstream.count("archive-api")), (1, 1))

    def test_replay_serves_the_stored_receipt(self):
        upstream = FakeUpstream()
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            with RunningForecastServer(make_service(upstream, receipt_store=store)) as address:
                first = get(address, CLAIM)
                upstream.daily_value = 99.0  # a later archive revision must not change the answer
                second = get(address, CLAIM)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1], second[1])
        self.assertEqual(
            first[2]["x-oathcast-receipt-sha256"], second[2]["x-oathcast-receipt-sha256"]
        )
        self.assertEqual(upstream.count("archive-api"), 1)

    def test_coordinates_skip_geocoding(self):
        upstream = FakeUpstream()
        params = {**CLAIM, "location": "38.7167, -9.1333"}
        with RunningForecastServer(make_service(upstream)) as address:
            status, body, _ = get(address, params)
        self.assertEqual(status, 200)
        self.assertEqual(upstream.count("geocoding-api"), 0)
        self.assertTrue(body["content"].startswith("38.7167, -9.1333 recorded"))

    def test_question_only_request(self):
        upstream = FakeUpstream(daily_value=11.0)
        params = {"question": "The forecast for Lisbon on 15 September 2026 called for 12 mm of rain. Did it verify?"}
        with RunningForecastServer(make_service(upstream)) as address:
            status, body, _ = get(address, params)
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "verified")

    def test_bad_requests_are_400(self):
        cases = [
            {**CLAIM, "provider": "open_meteo"},  # not a verify parameter
            {**CLAIM, "date": "2026-09-28"},  # window not finished
            {"date": "2026-09-15", "variable": "precipitation"},  # no location
        ]
        with RunningForecastServer(make_service(FakeUpstream())) as address:
            for params in cases:
                with self.subTest(params=params):
                    status, body, _ = get(address, params)
                    self.assertEqual(status, 400)
                    self.assertIn("error", body)

    def test_upstream_failure_is_502(self):
        with RunningForecastServer(make_service(FakeUpstream(fail=True))) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual((status, body["error"]), (502, "provider_unavailable"))

    def test_archive_gap_is_refused_not_zeroed(self):
        with RunningForecastServer(make_service(FakeUpstream(daily_value=None))) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual(status, 400)
        self.assertIn("no observation yet", body["error"])

    def test_authentication_applies(self):
        service = make_service(FakeUpstream(), require_auth=True)
        with RunningForecastServer(service) as address:
            unauthorized = get(address, CLAIM)
            authorized = get(address, CLAIM, headers=[("Authorization", "Bearer secret-token")])
        self.assertEqual(unauthorized[0], 401)
        self.assertEqual(authorized[0], 200)


if __name__ == "__main__":
    unittest.main()
