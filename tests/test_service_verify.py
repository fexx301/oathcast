from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

from oathcast.receipts import SqliteReceiptStore
from oathcast.service import ForecastService, VERIFY_DETAILS_PATH, VERIFY_PATH
from tests.test_service import RunningForecastServer, socket_get


FIXED_NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
LISBON = {"name": "Lisbon", "latitude": 38.71667, "longitude": -9.13333, "country": "Portugal"}


class FakeUpstream:
    """Open-Meteo geocoding and archive stand-in that records every URL."""

    def __init__(self, daily_value=3.4, fail=False, places=(LISBON,)):
        self.urls = []
        self.daily_value = daily_value
        self.fail = fail
        self.places = list(places)

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise TimeoutError("upstream timed out")
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if parsed.netloc.startswith("geocoding-api"):
            return {"results": self.places} if self.places else {"generationtime_ms": 0.1}
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
            details_status, _, _ = socket_get(address, f"{VERIFY_DETAILS_PATH}?{urlencode(CLAIM)}", [])
        self.assertEqual((status, body), (404, {"error": "not_found"}))
        self.assertEqual(details_status, 404)

    def test_answer_keeps_the_claim_out_of_the_scored_text(self):
        upstream = FakeUpstream(daily_value=3.4)
        with RunningForecastServer(make_service(upstream)) as address:
            status, body, headers = get(address, CLAIM)
        self.assertEqual(status, 200)
        self.assertEqual(
            body["content"],
            "Open-Meteo's historical weather model data estimates 3.4 mm of precipitation for "
            "Lisbon, Portugal on 15 September 2026.",
        )
        self.assertNotIn("12", json.dumps(body))
        self.assertEqual(
            set(body), {"content", "verified", "verdict", "observed_value", "unit"}
        )
        self.assertEqual((body["verdict"], body["verified"], body["observed_value"]), ("not_verified", False, 3.4))
        self.assertIn("x-oathcast-request-id", headers)
        self.assertEqual((upstream.count("geocoding-api"), upstream.count("archive-api")), (1, 1))

    def test_receipt_keeps_the_full_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            service = make_service(FakeUpstream(daily_value=3.4), receipt_store=store)
            result = service.verify_forecast(CLAIM, request_id="r1")
            receipt = store.get(result.event_id)
        self.assertEqual(receipt["kind"], "forecast_verification")
        self.assertEqual(
            (receipt["verification"]["forecast_value"], receipt["verification"]["error"],
             receipt["verification"]["tolerance"]),
            (12.0, -8.6, 3.0),
        )
        self.assertEqual(receipt["question"]["intent"], "WEATHER_FORECAST_VERIFY")

    def test_details_route_serves_the_structured_account(self):
        upstream = FakeUpstream(daily_value=3.4)
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            with RunningForecastServer(make_service(upstream, receipt_store=store)) as address:
                unanswered = socket_get(address, f"{VERIFY_DETAILS_PATH}?{urlencode(CLAIM)}", [])
                answer = get(address, CLAIM)
                status, raw, _ = socket_get(address, f"{VERIFY_DETAILS_PATH}?{urlencode(CLAIM)}", [])
        self.assertEqual((unanswered[0], json.loads(unanswered[1])["code"]), (404, "not_answered"))
        self.assertEqual(upstream.count("archive-api"), 1)  # the details route never fetches the archive
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["answer"], answer[1])
        self.assertEqual(body["receipt_sha256"], answer[2]["x-oathcast-receipt-sha256"])
        details = body["details"]
        self.assertEqual(details["kind"], "verification")
        self.assertEqual(details["claim"], {"value": 12.0, "unit": "mm", "as_supplied": {"value": 12.0, "unit": "mm"}})
        self.assertEqual(details["period"], {"start": "2026-09-15", "end": "2026-09-15", "days": 1,
                                             "timezone": "Europe/Lisbon"})
        self.assertEqual((details["location"]["resolved"], details["location"]["grid_latitude"]),
                         ("Lisbon, Portugal", 38.7))
        self.assertEqual(details["comparison"], {"error": -8.6, "tolerance": 3.0,
                                                 "tolerance_rule": "max(1 mm, 25% of the forecast)",
                                                 "within_tolerance": False})
        self.assertIn("not a station measurement", details["source"]["kind"])
        self.assertTrue(details["limitations"])

    def test_details_route_requires_the_same_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            service = make_service(FakeUpstream(), receipt_store=store, require_auth=True)
            with RunningForecastServer(service) as address:
                status, _, _ = socket_get(address, f"{VERIFY_DETAILS_PATH}?{urlencode(CLAIM)}", [])
        self.assertEqual(status, 401)

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
        self.assertIn("for 38.7167, -9.1333 on", body["content"])

    def test_question_only_request(self):
        upstream = FakeUpstream(daily_value=11.0)
        params = {"question": "The forecast for Lisbon on 15 September 2026 called for 12 mm of rain. Did it verify?"}
        with RunningForecastServer(make_service(upstream)) as address:
            status, body, _ = get(address, params)
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "verified")

    def test_bad_requests_are_400_with_a_code(self):
        cases = [
            ({**CLAIM, "provider": "open_meteo"}, "invalid_request"),  # not a verify parameter
            ({"date": "2026-09-15", "variable": "precipitation"}, "location_required"),
            ({**CLAIM, "date": "15/09/2026"}, "invalid_date"),
            ({**CLAIM, "date": ""}, "invalid_request"),  # empty parameter
            ({**{k: v for k, v in CLAIM.items() if k != "date"}, "start": "2026-08-01", "end": "2026-09-15"},
             "window_too_long"),
            ({**CLAIM, "forecast_value": "lots"}, "invalid_number"),
            ({**CLAIM, "unit": "F"}, "unit_incompatible"),
        ]
        with RunningForecastServer(make_service(FakeUpstream())) as address:
            for params, code in cases:
                with self.subTest(params=params):
                    status, body, headers = get(address, params)
                    self.assertEqual((status, body["code"]), (400, code))
                    self.assertIn("error", body)
                    self.assertNotIn("retry-after", headers)

    def test_unfinished_window_says_when_to_retry(self):
        with RunningForecastServer(make_service(FakeUpstream())) as address:
            status, body, headers = get(address, {**CLAIM, "date": "2026-09-29"})
        self.assertEqual((status, body["code"]), (422, "window_not_finished"))
        self.assertEqual(body["retry_after"], 2 * 86_400)
        self.assertEqual(headers["retry-after"], str(2 * 86_400))

    def test_unknown_place_is_422(self):
        with RunningForecastServer(make_service(FakeUpstream(places=()))) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual((status, body["code"]), (422, "place_not_found"))

    def test_upstream_failure_is_502(self):
        with RunningForecastServer(make_service(FakeUpstream(fail=True))) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual((status, body["error"], body["code"]), (502, "provider_unavailable", "provider_unavailable"))

    def test_archive_gap_is_refused_not_zeroed(self):
        with RunningForecastServer(make_service(FakeUpstream(daily_value=None))) as address:
            status, body, _ = get(address, CLAIM)
        self.assertEqual((status, body["code"], body["retry_after"]), (422, "no_archive_value", 86_400))
        self.assertIn("no value yet", body["error"])

    def test_authentication_applies(self):
        service = make_service(FakeUpstream(), require_auth=True)
        with RunningForecastServer(service) as address:
            unauthorized = get(address, CLAIM)
            authorized = get(address, CLAIM, headers=[("Authorization", "Bearer secret-token")])
        self.assertEqual(unauthorized[0], 401)
        self.assertEqual(authorized[0], 200)


if __name__ == "__main__":
    unittest.main()
