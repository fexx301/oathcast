from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
import unittest.mock
from urllib.parse import parse_qs, urlencode, urlparse

from oathcast.adapters import OpenMeteoWindowAdapter
from oathcast.forecast import (
    MAX_FORECAST_WINDOW_HOURS,
    ForecastWindowRequest,
    TemperatureWindowRequest,
)
from oathcast.receipts import ReceiptConflict, SqliteReceiptStore, receipt_digest
from oathcast.render import MAX_PUBLIC_WINDOW_RESPONSE_BYTES, WINDOW_RESPONSE_VERSION
from oathcast.service import (
    ForecastCutoffPassed,
    ForecastRequestHandler,
    ForecastService,
    ProviderUnavailable,
    ServiceTemperatureWindowForecast,
    ServiceWindowForecast,
    forecast_request_from_query,
    question_from_query,
    window_request_from_query,
)
from scripts.validate_miner_drafts import (
    load_schema_contract,
    schema_instance_errors,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REGISTERED_MANIFEST = ROOT / "miners" / "oathcast-weather.yaml"
WINDOW_CANDIDATE_MANIFEST = (
    ROOT / "miners" / "candidates" / "oathcast-weather-window-unregistered.yaml"
)
HOURLY_REREGISTRATION_MANIFEST = (
    ROOT / "miners" / "candidates" / "oathcast-weather-hourly-v4.yaml"
)
UTC = timezone.utc
START = datetime(2026, 8, 17, 15, tzinfo=UTC)
FIXED_NOW = datetime(2026, 8, 17, 11, tzinfo=UTC)

LEGACY_EVENT_ID = (
    "request-a32e009f3e72fe836d52cac4da32b505faa62469889af927a7ecf554af67f84b"
)
ONE_HOUR_WINDOW_EVENT_ID = (
    "window-request-769607fe66452cdd5f439dff3959b2a87063730666629bf378a06d4d1cf3a653"
)
TWENTY_FOUR_HOUR_WINDOW_EVENT_ID = (
    "window-request-69a53a127e5e746bff35efb679210bde7536253c1324dea43eb8433a6c509571"
)
TEMPERATURE_WINDOW_EVENT_ID = (
    "temperature-request-eb47c6fb466a2bab7523687acb917ed320fb6f6033d4bba3e9027e65e7ec27c2"
)
REGISTERED_MANIFEST_SHA256 = (
    "9ad11f06fda61960d621b7160e2f27a84daafa21683a24f6a3278427bb56ee0e"
)
LEGACY_PUBLIC_RESPONSE_BYTES = (
    b'{"content":"Measurable precipitation > 0.1 mm is likely to occur in Lagos '
    b'in the hour from 15:00 to 16:00 UTC on 17 August 2026. Probability: '
    b'70%.","probability":0.7}'
)


def _load_json(name):
    return json.loads((FIXTURES / name).read_text())


def _query_params(*, hours=24, event_id=None, provider=None):
    params = {
        "location_name": ["Lagos"],
        "lat": ["6.5244"],
        "lon": ["3.3792"],
        "start": ["2026-08-17T15:00:00Z"],
        "end": [
            (START + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
        ],
        "cutoff": ["2026-08-17T12:00:00Z"],
    }
    if event_id is not None:
        params["event_id"] = [event_id]
    if provider is not None:
        params["provider"] = [provider]
    return params


def _compatibility_params(*, hours=24, hourly="2t", event_id=None, provider=None):
    params = {
        "lat": ["6.5244"],
        "lon": ["3.3792"],
        "forecast_hours": [str(hours)],
        "hourly": [hourly],
    }
    if event_id is not None:
        params["event_id"] = [event_id]
    if provider is not None:
        params["provider"] = [provider]
    return params


def _window_payload(*, hours=24, start=START):
    timestamps = [
        start + timedelta(hours=index)
        for index in range(hours + 1)
    ]
    temperatures = [25.0 + (index % 7) * 0.5 for index in range(hours + 1)]
    probabilities = [index % 101 for index in range(hours + 1)]
    probabilities[min(6, hours)] = 88
    precipitation = [round((index % 9) / 10, 2) for index in range(hours + 1)]
    wind_speeds = [8.0 + (index % 13) * 0.5 for index in range(hours + 1)]
    return {
        "model": "service-window-fixture",
        "hourly": {
            "time": [value.strftime("%Y-%m-%dT%H:%M") for value in timestamps],
            "temperature_2m": temperatures,
            "precipitation": precipitation,
            "precipitation_probability": probabilities,
            "wind_speed_10m": wind_speeds,
        },
    }


def _temperature_payload(*, hours=24, start=None):
    if start is None:
        start = FIXED_NOW + timedelta(hours=1)
    timestamps = [start + timedelta(hours=index) for index in range(hours)]
    return {
        "model": "service-temperature-fixture",
        "timezone": "UTC",
        "utc_offset_seconds": 0,
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "\N{DEGREE SIGN}C",
        },
        "hourly": {
            "time": [value.strftime("%Y-%m-%dT%H:%M") for value in timestamps],
            "temperature_2m": [25.0 + (index % 7) * 0.5 for index in range(hours)],
        },
    }


def _provider_payload(url, *, window_hours=24):
    if "hourly=temperature_2m&" in url:
        return _temperature_payload(hours=window_hours)
    if "temperature_2m" in url:
        return _window_payload(hours=window_hours)
    return _load_json("open_meteo.json")


def _service(*, fetcher, receipt_store=None, provider_order=None, **changes):
    values = {
        "fetcher": fetcher,
        "provider_order": provider_order or ["open_meteo"],
        "receipt_store": receipt_store,
        "require_auth": False,
        "temperature_window_enabled": True,
        "clock": lambda: FIXED_NOW,
    }
    values.update(changes)
    return ForecastService(**values)


def _handler_request(service, path):
    class RecordingHandler(ForecastRequestHandler):
        def _send_json(self, status, payload, *, headers=None):
            encoded = json.dumps(
                payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.responses.append((status, payload, headers or {}, encoded))

    handler = object.__new__(RecordingHandler)
    handler.service = service
    handler.client_address = ("203.0.113.10", 0)
    handler.path = path
    handler.headers = {"X-Request-ID": "window-http-test"}
    handler.responses = []
    handler.do_GET()
    return handler.responses[0]


class ForecastWindowHttpTests(unittest.TestCase):
    def test_temperature_compatibility_is_explicitly_opt_in_at_http_boundary(self):
        service = ForecastService(
            fetcher=lambda url: self.fail("disabled compatibility must not fetch"),
            provider_order=["open_meteo"],
            require_auth=False,
            clock=lambda: FIXED_NOW,
        )
        status, payload, _, _ = _handler_request(
            service,
            "/predict?" + urlencode(_compatibility_params(), doseq=True),
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "temperature compatibility window is disabled")

    def test_predict_serves_the_legacy_multi_hour_shape(self):
        """A multi-hour start/end span is answered, not refused.

        Telegraph dispatches 24-hour WEATHER_FORECAST requests to the registered
        route. Refusing them with "only accepts one-hour windows" returned no
        temperature and scored zero on the leaderboard. The window response is
        the right vehicle for them: it carries the temperature range *and* the
        ``probability`` the registered ``output_schema`` lists as required, which
        the ``hourly=2t`` compatibility response does not.
        """

        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(),
        )
        status, payload, headers, _ = _handler_request(
            service,
            "/predict?" + urlencode(_query_params(), doseq=True),
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["X-OathCast-Request-ID"], "window-http-test")
        self.assertIn("content", payload)
        self.assertIn("probability", payload)
        self.assertIn("minimum_hourly_temperature_c", payload)
        self.assertIn("maximum_hourly_temperature_c", payload)
        self.assertEqual(len(calls), 1)

    def test_predict_accepts_a_one_hundred_sixty_eight_hour_window(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url)
            or _window_payload(hours=MAX_FORECAST_WINDOW_HOURS),
        )
        status, payload, _, encoded = _handler_request(
            service,
            "/predict?"
            + urlencode(
                _query_params(hours=MAX_FORECAST_WINDOW_HOURS),
                doseq=True,
            ),
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["response_version"], WINDOW_RESPONSE_VERSION)
        self.assertEqual(len(payload["hourly"]["time"]), MAX_FORECAST_WINDOW_HOURS)
        for field in ("2t", "precip", "precipitation_probability", "wind_speed_10m"):
            self.assertEqual(len(payload["hourly"][field]), MAX_FORECAST_WINDOW_HOURS)
        self.assertIn("minimum_hourly_temperature_c", payload)
        self.assertIn("maximum_hourly_temperature_c", payload)
        self.assertIn("max_hourly_precipitation_probability", payload)
        self.assertLessEqual(len(encoded), MAX_PUBLIC_WINDOW_RESPONSE_BYTES)
        query = parse_qs(urlparse(calls[0]).query)
        self.assertEqual(query["start_hour"], ["2026-08-17T15:00"])
        self.assertEqual(query["end_hour"], ["2026-08-24T15:00"])

    def test_multi_hour_response_matches_the_v4_registration_schema(self):
        service = _service(fetcher=lambda url: _window_payload())
        status, payload, _, _ = _handler_request(
            service,
            "/predict?" + urlencode(_query_params(), doseq=True),
        )

        self.assertEqual(status, 200, payload)
        schema = load_schema_contract(HOURLY_REREGISTRATION_MANIFEST)
        self.assertEqual(schema_instance_errors(schema, payload), [])

    def test_frozen_one_hour_response_matches_the_v4_registration_schema(self):
        service = _service(fetcher=lambda url: _load_json("open_meteo.json"))
        status, payload, _, encoded = _handler_request(
            service,
            "/predict?" + urlencode(_query_params(hours=1), doseq=True),
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(encoded, LEGACY_PUBLIC_RESPONSE_BYTES)
        schema = load_schema_contract(HOURLY_REREGISTRATION_MANIFEST)
        self.assertEqual(schema_instance_errors(schema, payload), [])

    def test_predict_returns_502_when_provider_lacks_the_final_endpoint(self):
        def incomplete_payload(url):
            del url
            payload = _window_payload(hours=MAX_FORECAST_WINDOW_HOURS)
            for field in (
                "time",
                "temperature_2m",
                "precipitation",
                "precipitation_probability",
                "wind_speed_10m",
            ):
                payload["hourly"][field].pop()
            return payload

        service = _service(fetcher=incomplete_payload)
        status, payload, headers, _ = _handler_request(
            service,
            "/predict?"
            + urlencode(
                _query_params(hours=MAX_FORECAST_WINDOW_HOURS),
                doseq=True,
            ),
        )

        self.assertEqual(status, 502)
        self.assertEqual(payload, {"error": "provider_unavailable", "request_id": "window-http-test"})
        self.assertEqual(headers["X-OathCast-Request-ID"], "window-http-test")

    def test_predict_returns_502_when_provider_lacks_a_first_or_interior_timestamp(self):
        for label, missing_index in (
            ("first", 0),
            ("interior", MAX_FORECAST_WINDOW_HOURS // 2),
        ):
            with self.subTest(label=label):
                def incomplete_payload(url, *, index=missing_index):
                    del url
                    payload = _window_payload(hours=MAX_FORECAST_WINDOW_HOURS)
                    for field in (
                        "time",
                        "temperature_2m",
                        "precipitation",
                        "precipitation_probability",
                        "wind_speed_10m",
                    ):
                        del payload["hourly"][field][index]
                    return payload

                service = _service(fetcher=incomplete_payload)
                status, payload, headers, _ = _handler_request(
                    service,
                    "/predict?"
                    + urlencode(
                        _query_params(hours=MAX_FORECAST_WINDOW_HOURS),
                        doseq=True,
                    ),
                )

                self.assertEqual(status, 502)
                self.assertEqual(
                    payload,
                    {
                        "error": "provider_unavailable",
                        "request_id": "window-http-test",
                    },
                )
                self.assertEqual(
                    headers["X-OathCast-Request-ID"],
                    "window-http-test",
                )

    def test_predict_refuses_a_span_longer_than_one_hundred_sixty_eight_hours(self):
        """The 168-hour bound is the limit, and it fails before fetching."""

        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(),
        )
        status, payload, _, _ = _handler_request(
            service,
            "/predict?"
            + urlencode(
                _query_params(hours=MAX_FORECAST_WINDOW_HOURS + 1),
                doseq=True,
            ),
        )

        self.assertEqual(status, 400)
        self.assertIn("between 1 and 168 hours", payload["error"])
        self.assertEqual(calls, [])

    def test_predict_refuses_the_observed_one_hundred_ninety_one_hour_dispatch(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(),
        )
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/point?"
            + urlencode(_query_params(hours=191), doseq=True),
        )

        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        self.assertEqual(calls, [])

    def test_predict_refuses_relative_start_as_an_api_value(self):
        calls = []
        params = _query_params()
        params["start"] = ["tomorrow"]
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(),
        )
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/point?" + urlencode(params, doseq=True),
        )

        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        self.assertEqual(calls, [])

    def test_predict_reports_observed_invalid_one_hour_timestamps_clearly(self):
        for field, value, expected_error in (
            (
                "start",
                "tomorrow",
                "horizon_start must be a valid ISO-8601 timestamp",
            ),
            (
                "end",
                "2026-08-17",
                "horizon_end must be a timezone-aware ISO-8601 timestamp",
            ),
        ):
            with self.subTest(field=field):
                calls = []
                params = _query_params(hours=1)
                params[field] = [value]
                service = _service(
                    fetcher=lambda url: calls.append(url) or _window_payload(hours=1),
                )

                status, payload, _, _ = _handler_request(
                    service,
                    "/v1/forecast/point?" + urlencode(params, doseq=True),
                )

                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], expected_error)
                self.assertEqual(calls, [])

    def test_predict_refuses_an_expired_explicit_cutoff_before_provider_fetch(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(),
        )
        params = _query_params()
        params["cutoff"] = ["2026-08-17T10:59:59Z"]

        status, payload, _, _ = _handler_request(
            service,
            "/predict?" + urlencode(params, doseq=True),
        )

        self.assertEqual(status, 410)
        self.assertIn("forecast_cutoff_passed", payload["error"])
        self.assertEqual(calls, [])

    def test_registered_paths_normalize_unaligned_windows_to_provider_hours(self):
        """Telegraph's arbitrary ISO bounds are rounded before provider access."""

        params = _query_params()
        params["start"] = ["2026-08-19T11:45:43Z"]
        params["end"] = ["2026-08-20T11:45:43Z"]
        del params["cutoff"]

        expected_start = datetime(2026, 8, 19, 12, tzinfo=UTC)
        for path in ("/predict", "/v1/forecast/point"):
            calls = []
            service = _service(
                fetcher=lambda url: calls.append(url)
                or _window_payload(start=expected_start),
            )
            with self.subTest(path=path):
                status, payload, _, _ = _handler_request(
                    service,
                    path + "?" + urlencode(params, doseq=True),
                )

                self.assertEqual(status, 200, payload)
                self.assertIn("12:00 UTC on 19 August 2026", payload["content"])
                self.assertIn("12:00 UTC on 20 August 2026", payload["content"])
                self.assertEqual(len(calls), 1)

    def test_legacy_window_path_is_not_publicly_reachable_for_one_hour(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _window_payload(hours=1),
        )
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/window?" + urlencode(_query_params(hours=1), doseq=True),
        )

        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})
        self.assertEqual(calls, [])

    def test_legacy_window_path_is_not_publicly_reachable_for_twenty_four_hours(self):
        calls = []
        service = _service(fetcher=lambda url: calls.append(url) or _window_payload())
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/window?" + urlencode(_query_params(), doseq=True),
        )

        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})
        self.assertEqual(calls, [])

    def test_registered_point_path_serves_the_legacy_multi_hour_shape(self):
        """The canonical alias behaves identically to /predict on a 24h span."""

        calls = []
        service = _service(fetcher=lambda url: calls.append(url) or _window_payload())
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/point?" + urlencode(_query_params(), doseq=True),
        )

        self.assertEqual(status, 200)
        self.assertIn("content", payload)
        self.assertIn("probability", payload)
        self.assertIn("minimum_hourly_temperature_c", payload)
        self.assertEqual(len(calls), 1)

    def test_multi_hour_window_defaults_its_cutoff_to_the_first_hour_end(self):
        """Telegraph sends no cutoff, so the default must cover the first hour.

        Nearest-hour normalization can move a request into the current hour. The
        canonical one-hour grace keeps all dispatcher timestamp positions usable.
        """

        calls = []
        service = _service(fetcher=lambda url: calls.append(url) or _window_payload())
        params = _query_params()
        del params["cutoff"]
        status, payload, _, _ = _handler_request(
            service, "/predict?" + urlencode(params, doseq=True)
        )

        self.assertEqual(status, 200, payload)
        self.assertIn("minimum_hourly_temperature_c", payload)
        self.assertEqual(len(calls), 1)

    def test_multi_hour_window_accepts_every_position_in_the_current_hour(self):
        cases = (
            ("2026-08-19T11:00:00Z", datetime(2026, 8, 19, 11, 0, 0, tzinfo=UTC)),
            ("2026-08-19T11:15:00Z", datetime(2026, 8, 19, 11, 15, 0, tzinfo=UTC)),
            ("2026-08-19T11:29:59Z", datetime(2026, 8, 19, 11, 29, 59, tzinfo=UTC)),
            ("2026-08-19T11:30:00Z", datetime(2026, 8, 19, 11, 30, 0, tzinfo=UTC)),
            ("2026-08-19T11:59:59Z", datetime(2026, 8, 19, 11, 59, 59, tzinfo=UTC)),
        )
        for raw_start, now in cases:
            with self.subTest(raw_start=raw_start):
                calls = []
                params = _query_params()
                requested_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                params["start"] = [raw_start]
                params["end"] = [
                    (requested_start + timedelta(hours=24))
                    .isoformat()
                    .replace("+00:00", "Z")
                ]
                del params["cutoff"]
                expected_start = (
                    datetime(2026, 8, 19, 12, tzinfo=UTC)
                    if requested_start.minute >= 30
                    else datetime(2026, 8, 19, 11, tzinfo=UTC)
                )
                service = _service(
                    fetcher=lambda url: calls.append(url)
                    or _window_payload(start=expected_start),
                    clock=lambda now=now: now,
                )

                request = window_request_from_query(params)
                self.assertEqual(request.horizon_start, expected_start)
                self.assertEqual(request.forecast_cutoff, expected_start)
                self.assertEqual(
                    request.effective_cutoff,
                    expected_start + timedelta(hours=1),
                )

                status, payload, _, _ = _handler_request(
                    service, "/predict?" + urlencode(params, doseq=True)
                )

                self.assertEqual(status, 200, payload)
                self.assertIn("minimum_hourly_temperature_c", payload)
                self.assertEqual(len(calls), 1)

    def test_multi_hour_window_is_refused_once_the_first_normalized_hour_elapsed(self):
        """The compatibility grace ends after the first normalized hour.

        A request naming an older window remains 410 rather than being treated as
        a fresh forecast merely because the dispatcher omitted the cutoff.
        """

        calls = []
        service = _service(fetcher=lambda url: calls.append(url) or _window_payload())
        params = _query_params()
        del params["cutoff"]
        params["start"] = [
            (FIXED_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        ]
        params["end"] = [
            (FIXED_NOW + timedelta(hours=23)).isoformat().replace("+00:00", "Z")
        ]
        status, payload, _, _ = _handler_request(
            service, "/predict?" + urlencode(params, doseq=True)
        )

        self.assertEqual(status, 410)
        self.assertIn("forecast_cutoff_passed", payload["error"])
        self.assertEqual(calls, [])

    def test_registered_paths_accept_telegraph_twenty_four_hour_2t_query(self):
        compatibility_start = datetime(2026, 8, 17, 12, tzinfo=UTC)
        encoded_bodies = []
        for path in ("/predict", "/v1/forecast/point"):
            calls = []
            service = _service(
                fetcher=lambda url: calls.append(url)
                or _temperature_payload(start=compatibility_start),
            )
            with self.subTest(path=path):
                status, payload, _, encoded = _handler_request(
                    service,
                    path + "?" + urlencode(_compatibility_params(), doseq=True),
                )

                self.assertEqual(status, 200)
                self.assertEqual(payload["reference_time"], "2026-08-17T11:00:00Z")
                self.assertEqual(len(payload["hourly"]["time"]), 24)
                self.assertEqual(len(payload["hourly"]["2t"]), 24)
                self.assertEqual(payload["hourly"]["time"][0], "2026-08-17T12:00:00Z")
                self.assertEqual(payload["hourly"]["2t"][0], 298.15)
                self.assertEqual(payload["hourly_units"], {"time": "iso8601", "2t": "K"})
                self.assertIn("298.15 K", payload["content"])
                self.assertIn("301.15 K", payload["content"])
                self.assertEqual(len(calls), 1)
                self.assertIn("hourly=temperature_2m", calls[0])
                self.assertNotIn("precipitation_probability", calls[0])
                encoded_bodies.append(encoded)
        self.assertEqual(encoded_bodies[0], encoded_bodies[1])

    def test_twenty_four_hour_compatibility_response_matches_candidate_schema(self):
        compatibility_start = datetime(2026, 8, 17, 12, tzinfo=UTC)
        service = _service(
            fetcher=lambda url: _temperature_payload(start=compatibility_start),
        )

        status, payload, _, _ = _handler_request(
            service,
            "/predict?" + urlencode(_compatibility_params(hours=24), doseq=True),
        )

        self.assertEqual(status, 200)
        schema = load_schema_contract(WINDOW_CANDIDATE_MANIFEST)
        self.assertEqual(schema_instance_errors(payload, schema), [])
        self.assertEqual(
            set(payload),
            {"content", "reference_time", "hourly", "hourly_units"},
        )
        self.assertEqual(set(payload["hourly"]), {"time", "2t"})
        self.assertEqual(payload["hourly_units"], {"time": "iso8601", "2t": "K"})
        self.assertEqual(len(payload["hourly"]["time"]), 24)
        self.assertEqual(len(payload["hourly"]["2t"]), 24)
        field_names = set()

        def collect_keys(value):
            if isinstance(value, dict):
                field_names.update(value)
                for nested in value.values():
                    collect_keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_keys(nested)

        collect_keys(payload)
        self.assertFalse(
            any(
                "probability" in name or "precipitation" in name
                for name in field_names
            )
        )

    def test_registered_paths_accept_one_and_twenty_four_hour_bounds(self):
        for hours in (1, 24):
            with self.subTest(hours=hours):
                service = _service(
                    fetcher=lambda url, hours=hours: _temperature_payload(hours=hours),
                )
                status, payload, _, _ = _handler_request(
                    service,
                    "/predict?"
                    + urlencode(_compatibility_params(hours=hours), doseq=True),
                )
                self.assertEqual(status, 200)
                self.assertEqual(len(payload["hourly"]["time"]), hours)
                self.assertEqual(len(payload["hourly"]["2t"]), hours)

    def test_legacy_one_hour_predict_response_bytes_are_unchanged(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _load_json("open_meteo.json"),
        )
        status, payload, _, encoded = _handler_request(
            service,
            "/predict?" + urlencode(_query_params(hours=1), doseq=True),
        )

        self.assertEqual(status, 200)
        self.assertEqual(encoded, LEGACY_PUBLIC_RESPONSE_BYTES)
        self.assertEqual(set(payload), {"content", "probability"})
        self.assertEqual(len(calls), 1)
        self.assertIn("hourly=precipitation_probability", calls[0])
        self.assertNotIn("temperature_2m", calls[0])

    def test_legacy_one_hour_registered_path_response_bytes_are_unchanged(self):
        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url) or _load_json("open_meteo.json"),
        )
        status, payload, _, encoded = _handler_request(
            service,
            "/v1/forecast/point?" + urlencode(_query_params(hours=1), doseq=True),
        )

        self.assertEqual(status, 200)
        self.assertEqual(encoded, LEGACY_PUBLIC_RESPONSE_BYTES)
        self.assertEqual(set(payload), {"content", "probability"})
        self.assertEqual(len(calls), 1)
        self.assertIn("hourly=precipitation_probability", calls[0])
        self.assertNotIn("temperature_2m", calls[0])

    def test_telegraph_2t_query_rejects_incomplete_invalid_and_mixed_shapes(self):
        service = _service(
            fetcher=lambda url: self.fail("invalid compatibility query must not fetch"),
        )
        cases = {
            "missing hourly": {
                key: value
                for key, value in _compatibility_params().items()
                if key != "hourly"
            },
            "missing forecast_hours": {
                key: value
                for key, value in _compatibility_params().items()
                if key != "forecast_hours"
            },
            "zero hours": _compatibility_params(hours=0),
            "too many hours": _compatibility_params(hours=25),
            "fractional hours": _compatibility_params(hours="1.5"),
            "unsupported hourly field": _compatibility_params(hourly="temperature_2m"),
            "mixed explicit start": _compatibility_params()
            | {"start": ["2026-08-17T15:00:00Z"]},
            "duplicate hourly": _compatibility_params()
            | {"hourly": ["2t", "2t"]},
        }
        for label, params in cases.items():
            with self.subTest(label=label):
                status, payload, _, _ = _handler_request(
                    service,
                    "/predict?" + urlencode(params, doseq=True),
                )
                self.assertEqual(status, 400)
                self.assertTrue(payload["error"])

    def test_legacy_window_path_returns_not_found_for_temperature_shape(self):
        service = _service(
            fetcher=lambda url: self.fail("window path must reject temperature shape"),
        )
        status, payload, _, _ = _handler_request(
            service,
            "/v1/forecast/window?"
            + urlencode(_compatibility_params(), doseq=True),
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})

    def test_unknown_query_parameter_remains_rejected(self):
        service = _service(
            fetcher=lambda url: self.fail("unknown parameters must not fetch"),
        )
        params = _compatibility_params() | {"unexpected": ["value"]}
        status, payload, _, _ = _handler_request(
            service,
            "/predict?" + urlencode(params, doseq=True),
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unknown query parameter: unexpected")

    def test_internal_window_provider_selection_rejects_non_capable_providers(self):
        service = _service(
            fetcher=lambda url: self.fail("unsupported provider must not fetch"),
        )
        request = window_request_from_query(_query_params())
        for provider in ("weatherapi", "openweather_onecall", "not_a_provider"):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(
                    ValueError,
                    "does not support complete 1-to-168-hour",
                ):
                    service.forecast_window(
                        request,
                        request_id="unsupported-window-provider",
                        requested_provider=provider,
                    )


class ForecastWindowQueryTests(unittest.TestCase):
    def test_predict_query_dispatches_by_duration(self):
        point = forecast_request_from_query(_query_params(hours=1))
        window = forecast_request_from_query(_query_params(hours=24))

        self.assertNotIsInstance(point, ForecastWindowRequest)
        self.assertIsInstance(window, ForecastWindowRequest)
        self.assertEqual(window.duration_hours, 24)

    def test_unaligned_one_hour_query_keeps_the_existing_point_contract(self):
        params = _query_params(hours=1)
        params["start"] = ["2026-08-17T15:45:43Z"]
        params["end"] = ["2026-08-17T16:45:43Z"]

        question = forecast_request_from_query(params)

        self.assertNotIsInstance(question, ForecastWindowRequest)
        self.assertEqual(
            question.horizon_start,
            datetime(2026, 8, 17, 15, 45, 43, tzinfo=UTC),
        )
        self.assertEqual(
            question.horizon_end,
            datetime(2026, 8, 17, 16, 45, 43, tzinfo=UTC),
        )

    def test_one_hour_query_retains_legacy_naive_timestamp_acceptance(self):
        params = _query_params(hours=1)
        params["start"] = ["2026-08-17T15:45:43"]
        params["end"] = ["2026-08-17T16:45:43"]

        question = forecast_request_from_query(params)

        self.assertNotIsInstance(question, ForecastWindowRequest)
        self.assertEqual(
            question.horizon_start,
            datetime(2026, 8, 17, 15, 45, 43, tzinfo=UTC),
        )

    def test_query_timestamps_require_timezone_aware_iso_date_times(self):
        cases = {
            "naive start": {"start": ["2026-08-19T11:15:00"]},
            "date-only start": {"start": ["2026-08-19"]},
            "naive end": {"end": ["2026-08-20T11:15:00"]},
            "date-only end": {"end": ["2026-08-20"]},
            "naive cutoff": {"cutoff": ["2026-08-19T11:00:00"]},
        }
        for label, replacement in cases.items():
            with self.subTest(label=label):
                params = _query_params()
                params.update(replacement)
                with self.assertRaisesRegex(
                    ValueError,
                    "timezone-aware ISO-8601 timestamp",
                ):
                    window_request_from_query(params)

    def test_unaligned_window_bounds_round_to_nearest_utc_hour_with_half_up_ties(self):
        cases = {
            "just before half-hour": (
                "2026-08-19T13:29:59.999+02:00",
                "2026-08-20T13:29:59.999+02:00",
                datetime(2026, 8, 19, 11, tzinfo=UTC),
            ),
            "half-hour tie rounds up": (
                "2026-08-19T13:30:00+02:00",
                "2026-08-20T13:30:00+02:00",
                datetime(2026, 8, 19, 12, tzinfo=UTC),
            ),
            "after half-hour": (
                "2026-08-19T11:45:43Z",
                "2026-08-20T11:45:43Z",
                datetime(2026, 8, 19, 12, tzinfo=UTC),
            ),
            "rounds across UTC day boundary": (
                "2026-08-19T23:45:43Z",
                "2026-08-20T23:45:43Z",
                datetime(2026, 8, 20, 0, tzinfo=UTC),
            ),
        }
        for label, (raw_start, raw_end, expected_start) in cases.items():
            with self.subTest(label=label):
                params = _query_params()
                params["start"] = [raw_start]
                params["end"] = [raw_end]
                del params["cutoff"]

                request = window_request_from_query(params)

                self.assertEqual(request.horizon_start, expected_start)
                self.assertEqual(
                    request.horizon_end,
                    expected_start + timedelta(hours=24),
                )
                self.assertEqual(request.duration_hours, 24)
                self.assertEqual(
                    request.forecast_cutoff,
                    expected_start,
                )
                self.assertEqual(
                    request.effective_cutoff,
                    expected_start + timedelta(hours=1),
                )

    def test_explicit_cutoff_is_not_rounded_when_window_bounds_are_normalized(self):
        params = _query_params()
        params["start"] = ["2026-08-19T13:45:43+02:00"]
        params["end"] = ["2026-08-20T13:45:43+02:00"]
        params["cutoff"] = ["2026-08-19T07:17:13.456-03:00"]

        request = window_request_from_query(params)

        self.assertEqual(request.horizon_start, datetime(2026, 8, 19, 12, tzinfo=UTC))
        self.assertEqual(request.horizon_end, datetime(2026, 8, 20, 12, tzinfo=UTC))
        self.assertEqual(
            request.forecast_cutoff,
            datetime(2026, 8, 19, 10, 17, 13, 456000, tzinfo=UTC),
        )

    def test_explicit_cutoff_after_normalized_start_is_not_silently_rounded(self):
        params = _query_params()
        params["start"] = ["2026-08-19T11:15:00Z"]
        params["end"] = ["2026-08-20T11:15:00Z"]
        params["cutoff"] = ["2026-08-19T11:30:00Z"]

        with self.assertRaisesRegex(
            ValueError,
            "forecast_cutoff must not be after horizon_start",
        ):
            window_request_from_query(params)

    def test_implicit_grace_and_earlier_explicit_cutoff_have_distinct_identities(self):
        omitted = _query_params()
        omitted["start"] = ["2026-08-19T11:15:00Z"]
        omitted["end"] = ["2026-08-20T11:15:00Z"]
        del omitted["cutoff"]
        explicit = {**omitted, "cutoff": ["2026-08-19T10:00:00Z"]}

        implicit_request = window_request_from_query(omitted)
        explicit_request = window_request_from_query(explicit)

        self.assertEqual(
            implicit_request.horizon_start,
            explicit_request.horizon_start,
        )
        self.assertEqual(
            implicit_request.forecast_cutoff,
            datetime(2026, 8, 19, 11, tzinfo=UTC),
        )
        self.assertEqual(
            implicit_request.effective_cutoff,
            datetime(2026, 8, 19, 12, tzinfo=UTC),
        )
        self.assertEqual(
            explicit_request.forecast_cutoff,
            datetime(2026, 8, 19, 10, tzinfo=UTC),
        )
        self.assertNotEqual(implicit_request.to_dict(), explicit_request.to_dict())
        self.assertNotEqual(implicit_request.event_id, explicit_request.event_id)

        service = _service(
            fetcher=lambda url: _window_payload(
                start=datetime(2026, 8, 19, 11, tzinfo=UTC)
            ),
            clock=lambda: datetime(2026, 8, 19, 11, 15, tzinfo=UTC),
        )
        service.forecast_window(implicit_request, request_id="implicit-grace")
        with self.assertRaises(ForecastCutoffPassed):
            service.forecast_window(explicit_request, request_id="explicit-cutoff")

    def test_implicit_grace_identity_matches_legacy_start_cutoff_for_rollback(self):
        omitted = _query_params()
        omitted["start"] = ["2026-08-19T11:15:00Z"]
        omitted["end"] = ["2026-08-20T11:15:00Z"]
        del omitted["cutoff"]
        explicit_start = {**omitted, "cutoff": ["2026-08-19T11:00:00Z"]}

        implicit_request = window_request_from_query(omitted)
        legacy_request = window_request_from_query(explicit_start)
        self.assertEqual(implicit_request.event_id, legacy_request.event_id)
        implicit_identity = implicit_request.to_dict()
        self.assertEqual(implicit_identity["forecast_cutoff"], "2026-08-19T11:00:00Z")
        expected_legacy_identity = {
            key: value
            for key, value in implicit_identity.items()
            if key != "cutoff_policy"
        }
        expected_legacy_identity["cutoff_policy"] = "explicit"
        self.assertEqual(legacy_request.to_dict(), expected_legacy_identity)
        self.assertIn("cutoff_policy", implicit_identity)

    def test_explicit_cutoff_at_normalized_start_has_no_omitted_grace(self):
        omitted = _query_params()
        omitted["start"] = ["2026-08-19T11:15:00Z"]
        omitted["end"] = ["2026-08-20T11:15:00Z"]
        del omitted["cutoff"]
        explicit = {**omitted, "cutoff": ["2026-08-19T11:00:00Z"]}

        implicit_request = window_request_from_query(omitted)
        explicit_request = window_request_from_query(explicit)

        self.assertEqual(implicit_request.horizon_start, explicit_request.horizon_start)
        self.assertEqual(implicit_request.forecast_cutoff, explicit_request.forecast_cutoff)
        self.assertEqual(implicit_request.event_id, explicit_request.event_id)
        self.assertEqual(
            implicit_request.effective_cutoff,
            datetime(2026, 8, 19, 12, tzinfo=UTC),
        )
        self.assertEqual(
            explicit_request.effective_cutoff,
            datetime(2026, 8, 19, 11, tzinfo=UTC),
        )

        calls = []
        service = _service(
            fetcher=lambda url: calls.append(url)
            or _window_payload(start=implicit_request.horizon_start),
            clock=lambda: datetime(2026, 8, 19, 11, 15, tzinfo=UTC),
        )
        with self.assertRaises(ForecastCutoffPassed):
            service.forecast_window(explicit_request, request_id="explicit-start")
        service.forecast_window(implicit_request, request_id="implicit-grace")
        self.assertEqual(len(calls), 1)

    def test_late_explicit_cutoff_cannot_replay_implicit_receipt(self):
        """A shared legacy event ID must not bypass an explicit deadline."""

        omitted = _query_params(event_id="shared-cutoff-replay")
        omitted["start"] = ["2026-08-19T11:15:00Z"]
        omitted["end"] = ["2026-08-20T11:15:00Z"]
        del omitted["cutoff"]
        explicit = {**omitted, "cutoff": ["2026-08-19T11:00:00Z"]}

        implicit_request = window_request_from_query(omitted)
        explicit_request = window_request_from_query(explicit)
        self.assertEqual(implicit_request.event_id, explicit_request.event_id)

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            calls = []
            try:
                service = _service(
                    fetcher=lambda url: calls.append(url)
                    or _window_payload(
                        start=implicit_request.horizon_start,
                        hours=implicit_request.duration_hours,
                    ),
                    receipt_store=store,
                    clock=lambda: datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
                )
                service.forecast_window(
                    implicit_request,
                    request_id="implicit-before-explicit",
                )
                self.assertEqual(len(calls), 1)

                late_service = _service(
                    fetcher=lambda url: self.fail(
                        "late explicit request must not fetch or replay"
                    ),
                    receipt_store=store,
                    clock=lambda: datetime(2026, 8, 19, 11, 15, tzinfo=UTC),
                )
                with self.assertRaises(ReceiptConflict):
                    late_service.forecast_window(
                        explicit_request,
                        request_id="late-explicit",
                    )
                self.assertEqual(len(calls), 1)
            finally:
                store.close()

    def test_legacy_and_implicit_window_receipts_replay_in_both_directions(self):
        params = _query_params()
        params["start"] = ["2026-08-19T11:15:00Z"]
        params["end"] = ["2026-08-20T11:15:00Z"]
        del params["cutoff"]
        implicit_request = window_request_from_query(params)
        legacy_question = {
            key: value
            for key, value in implicit_request.to_dict().items()
            if key != "cutoff_policy"
        }
        legacy_request = ForecastWindowRequest.from_dict(legacy_question)
        self.assertEqual(legacy_request.event_id, implicit_request.event_id)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_store = SqliteReceiptStore(path)
            first_service = _service(
                fetcher=lambda url: _window_payload(
                    start=implicit_request.horizon_start,
                    hours=implicit_request.duration_hours,
                ),
                receipt_store=first_store,
                clock=lambda: datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
            )
            first = first_service.forecast_window(
                legacy_request,
                request_id="legacy-window",
            )
            first_store.close()

            new_store = SqliteReceiptStore(path)
            new_service = _service(
                fetcher=lambda url: self.fail("legacy receipt must replay"),
                receipt_store=new_store,
                clock=lambda: datetime(2026, 8, 19, 13, tzinfo=UTC),
            )
            replay = new_service.forecast_window(
                implicit_request,
                request_id="new-window-retry",
            )
            self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
            self.assertEqual(replay.to_public_response(), first.to_public_response())
            new_store.close()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            new_store = SqliteReceiptStore(path)
            new_service = _service(
                fetcher=lambda url: _window_payload(
                    start=implicit_request.horizon_start,
                    hours=implicit_request.duration_hours,
                ),
                receipt_store=new_store,
                clock=lambda: datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
            )
            new_result = new_service.forecast_window(
                implicit_request,
                request_id="new-window",
            )
            new_store.close()

            rollback_store = SqliteReceiptStore(path)
            rollback_service = _service(
                fetcher=lambda url: self.fail("new receipt must replay on rollback"),
                receipt_store=rollback_store,
                clock=lambda: datetime(2026, 8, 19, 13, tzinfo=UTC),
            )
            rollback = rollback_service.forecast_window(
                legacy_request,
                request_id="legacy-window-retry",
            )
            self.assertEqual(rollback.receipt_sha256, new_result.receipt_sha256)
            self.assertEqual(rollback.to_public_response(), new_result.to_public_response())
            rollback_store.close()

    def test_non_integral_window_span_is_rejected_before_rounding(self):
        params = _query_params()
        params["start"] = ["2026-08-19T11:45:43Z"]
        params["end"] = ["2026-08-19T13:15:43Z"]
        del params["cutoff"]

        with self.assertRaisesRegex(ValueError, "whole number of hours"):
            window_request_from_query(params)

    def test_unaligned_and_aligned_window_queries_share_canonical_generated_identity(self):
        unaligned = _query_params()
        unaligned["start"] = ["2026-08-17T15:45:43Z"]
        unaligned["end"] = ["2026-08-18T15:45:43Z"]
        del unaligned["cutoff"]

        aligned = _query_params()
        aligned["start"] = ["2026-08-17T16:00:00Z"]
        aligned["end"] = ["2026-08-18T16:00:00Z"]
        del aligned["cutoff"]

        unaligned_request = forecast_request_from_query(unaligned)
        aligned_request = forecast_request_from_query(aligned)

        self.assertIsInstance(unaligned_request, ForecastWindowRequest)
        self.assertIsInstance(aligned_request, ForecastWindowRequest)
        self.assertEqual(unaligned_request.to_dict(), aligned_request.to_dict())
        self.assertEqual(unaligned_request.event_id, aligned_request.event_id)

    def test_telegraph_2t_query_anchors_the_next_complete_utc_hour(self):
        request = forecast_request_from_query(
            _compatibility_params(),
            reference_time=datetime(2026, 8, 17, 11, 37, 22, tzinfo=UTC),
        )

        self.assertIsInstance(request, TemperatureWindowRequest)
        self.assertEqual(request.reference_time, datetime(2026, 8, 17, 11, tzinfo=UTC))
        self.assertEqual(request.horizon_start, datetime(2026, 8, 17, 12, tzinfo=UTC))
        self.assertEqual(request.horizon_end, datetime(2026, 8, 18, 12, tzinfo=UTC))
        self.assertEqual(request.forecast_hours, 24)

    def test_generated_ids_are_stable_versioned_and_distinct(self):
        point = question_from_query(_query_params(hours=1))
        repeated_point = question_from_query(_query_params(hours=1))
        one_hour_window = window_request_from_query(_query_params(hours=1))
        full_window = window_request_from_query(_query_params())
        repeated_window = window_request_from_query(_query_params())
        temperature = forecast_request_from_query(
            _compatibility_params(),
            reference_time=FIXED_NOW + timedelta(minutes=1),
        )
        repeated_temperature = forecast_request_from_query(
            _compatibility_params(),
            reference_time=FIXED_NOW + timedelta(minutes=59),
        )
        next_hour_temperature = forecast_request_from_query(
            _compatibility_params(),
            reference_time=FIXED_NOW + timedelta(hours=1),
        )

        self.assertEqual(point.event_id, LEGACY_EVENT_ID)
        self.assertEqual(repeated_point.event_id, LEGACY_EVENT_ID)
        self.assertEqual(one_hour_window.event_id, ONE_HOUR_WINDOW_EVENT_ID)
        self.assertEqual(full_window.event_id, TWENTY_FOUR_HOUR_WINDOW_EVENT_ID)
        self.assertEqual(repeated_window.event_id, TWENTY_FOUR_HOUR_WINDOW_EVENT_ID)
        self.assertEqual(temperature.event_id, TEMPERATURE_WINDOW_EVENT_ID)
        self.assertEqual(temperature.event_id, repeated_temperature.event_id)
        self.assertNotEqual(temperature.event_id, next_hour_temperature.event_id)
        self.assertNotEqual(point.event_id, one_hour_window.event_id)
        self.assertNotEqual(point.event_id, full_window.event_id)
        self.assertNotEqual(temperature.event_id, point.event_id)
        self.assertNotEqual(temperature.event_id, full_window.event_id)

        changed = _query_params()
        changed["location_name"] = ["Ibadan"]
        self.assertNotEqual(
            window_request_from_query(changed).event_id,
            full_window.event_id,
        )


class ForecastWindowServiceTests(unittest.TestCase):
    def test_service_temperature_result_rejects_identity_reference_and_count_mismatches(self):
        request = forecast_request_from_query(
            _compatibility_params(hours=2, event_id="temperature-binding"),
            reference_time=FIXED_NOW,
        )
        self.assertIsInstance(request, TemperatureWindowRequest)
        source = _service(
            fetcher=lambda url: _temperature_payload(hours=2),
        ).forecast_temperature_window(request, request_id="source-temperature")

        invalid_requests = (
            replace(request, event_id="different-temperature-event"),
            replace(request, reference_time=request.reference_time - timedelta(hours=1)),
            replace(request, forecast_hours=1),
        )
        for invalid in invalid_requests:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ServiceTemperatureWindowForecast(
                    request=invalid,
                    forecast=source.forecast,
                    raw_payload=source.raw_payload,
                    request_id="mismatched-temperature",
                )

    def test_service_window_result_rejects_request_identity_and_horizon_mismatches(self):
        source_request = window_request_from_query(
            _query_params(hours=1, event_id="service-window-binding")
        )
        source = _service(
            fetcher=lambda url: _window_payload(hours=1),
        ).forecast_window(source_request, request_id="source-window")

        different_event = window_request_from_query(
            _query_params(hours=1, event_id="different-window-event")
        )
        with self.assertRaisesRegex(ValueError, "event_id"):
            ServiceWindowForecast(
                request=different_event,
                forecast=source.forecast,
                raw_payload=source.raw_payload,
                request_id="mismatched-event",
            )

        different_horizon = window_request_from_query(
            _query_params(hours=2, event_id=source_request.event_id)
        )
        with self.assertRaisesRegex(ValueError, "horizon"):
            ServiceWindowForecast(
                request=different_horizon,
                forecast=source.forecast,
                raw_payload=source.raw_payload,
                request_id="mismatched-horizon",
            )

    def test_unverified_window_provider_is_blocked_before_fetch(self):
        service = _service(
            fetcher=lambda url: self.fail("unverified provider must not fetch"),
            provider_order=["weatherapi"],
            allow_unverified_providers=False,
        )
        service.window_adapters = {"weatherapi": object()}
        request = window_request_from_query(_query_params())

        with self.assertRaisesRegex(
            ProviderUnavailable,
            "disabled until its window forecast semantics are validated",
        ):
            service.forecast_window(request, request_id="unverified-window-provider")

    def test_window_event_equivalence_is_gated_unless_explicitly_allowed(self):
        native_adapter = OpenMeteoWindowAdapter()

        class UnverifiedEquivalenceAdapter:
            def build_url(self, request, api_key=None):
                return native_adapter.build_url(request, api_key)

            def parse(self, payload, request, issued_at, retrieved_at=None):
                forecast = native_adapter.parse(
                    payload,
                    request,
                    issued_at,
                    retrieved_at,
                )
                return replace(forecast, event_equivalence="unverified")

        request = window_request_from_query(_query_params(hours=2))
        blocked = _service(fetcher=lambda url: _window_payload(hours=2))
        blocked.window_adapters = {"open_meteo": UnverifiedEquivalenceAdapter()}
        with self.assertRaisesRegex(
            ProviderUnavailable,
            "disabled until its window forecast semantics are validated",
        ):
            blocked.forecast_window(request, request_id="blocked-equivalence")

        allowed = _service(
            fetcher=lambda url: _window_payload(hours=2),
            allow_unverified_providers=True,
        )
        allowed.window_adapters = {"open_meteo": UnverifiedEquivalenceAdapter()}
        result = allowed.forecast_window(request, request_id="allowed-equivalence")
        self.assertEqual(result.forecast.event_equivalence, "unverified")

    def test_schema_v4_receipt_persists_replays_and_freezes_public_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_calls = []
            first_store = SqliteReceiptStore(path)
            first_service = _service(
                fetcher=lambda url: first_calls.append(url) or _window_payload(),
                receipt_store=first_store,
            )
            request = window_request_from_query(
                _query_params(event_id="window-receipt-v4")
            )
            first = first_service.forecast_window(request, request_id="first-window")
            frozen_response = first.to_public_response()
            stored = first_store.get(request.event_id)

            self.assertEqual(len(first_calls), 1)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["schema_version"], 4)
            self.assertEqual(stored["public_response"], frozen_response)
            self.assertEqual(stored["question"], request.to_dict())
            self.assertEqual(first.receipt_sha256, stored["receipt_sha256"])

            replay_store = SqliteReceiptStore(path)
            replay_service = _service(
                fetcher=lambda url: self.fail("receipt replay must not fetch"),
                receipt_store=replay_store,
                clock=lambda: datetime(2026, 8, 17, 13, tzinfo=UTC),
            )
            changed_response = {
                "content": "changed window renderer",
                "probability": 0.01,
            }
            with unittest.mock.patch(
                "oathcast.service.public_window_response",
                return_value=changed_response,
            ):
                replay = replay_service.forecast_window(
                    request,
                    request_id="retry-window",
                )

            self.assertEqual(replay.to_public_response(), frozen_response)
            self.assertEqual(replay.request_id, "first-window")
            self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
            first_store.close()
            replay_store.close()

    def test_schema_v2_receipt_replays_legacy_response_without_inventing_fields(self):
        request = window_request_from_query(
            _query_params(event_id="legacy-window-receipt-v2")
        )
        source = _service(fetcher=lambda url: _window_payload()).forecast_window(
            request,
            request_id="legacy-window-request",
        )
        legacy_forecast = source.forecast.to_dict()
        for hour in legacy_forecast["hours"]:
            hour.pop("precipitation_mm")
            hour.pop("wind_speed_10m_kmh")
        legacy_response = {
            "content": "Legacy summary bytes stay frozen.",
            "probability": 0.88,
        }
        legacy_bytes = json.dumps(
            legacy_response,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        receipt = {
            "schema_version": 2,
            "created_at": "2026-08-17T11:00:00Z",
            "request_id": "legacy-window-request",
            "question": request.to_dict(),
            "forecast": legacy_forecast,
            "raw_payload": {},
            "public_response": legacy_response,
        }
        receipt["receipt_sha256"] = receipt_digest(receipt)

        store = SqliteReceiptStore(":memory:")
        try:
            store.save(receipt)
            replay = _service(
                fetcher=lambda url: self.fail("legacy receipt replay must not fetch"),
                receipt_store=store,
            ).forecast_window(request, request_id="ignored-retry-id")

            self.assertFalse(replay.forecast.has_complete_hourly_weather)
            self.assertEqual(replay.to_public_response(), legacy_response)
            self.assertEqual(
                json.dumps(
                    replay.to_public_response(),
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
                legacy_bytes,
            )
        finally:
            store.close()

    def test_schema_v4_168_hour_receipt_persists_and_replays_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_calls = []
            first_store = SqliteReceiptStore(path)
            first_service = _service(
                fetcher=lambda url: first_calls.append(url)
                or _window_payload(hours=MAX_FORECAST_WINDOW_HOURS),
                receipt_store=first_store,
            )
            request = window_request_from_query(
                _query_params(
                    hours=MAX_FORECAST_WINDOW_HOURS,
                    event_id="window-receipt-168",
                )
            )
            first = first_service.forecast_window(request, request_id="first-window-168")
            stored = first_store.get(request.event_id)

            self.assertEqual(len(first_calls), 1)
            self.assertIsNotNone(stored)
            self.assertEqual(len(first.forecast.hours), MAX_FORECAST_WINDOW_HOURS)
            self.assertEqual(len(stored["forecast"]["hours"]), MAX_FORECAST_WINDOW_HOURS)
            self.assertEqual(stored["public_response"], first.to_public_response())
            first_store.close()

            replay_store = SqliteReceiptStore(path)
            replay_service = _service(
                fetcher=lambda url: self.fail("168-hour receipt replay must not fetch"),
                receipt_store=replay_store,
                clock=lambda: datetime(2026, 8, 17, 13, tzinfo=UTC),
            )
            replay = replay_service.forecast_window(
                request,
                request_id="retry-window-168",
            )

            self.assertEqual(replay.request_id, "first-window-168")
            self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
            self.assertEqual(replay.to_public_response(), first.to_public_response())
            self.assertEqual(replay_store.row_count(), 1)
            replay_store.close()

    def test_schema_v4_implicit_response_completing_at_cutoff_is_not_persisted(self):
        params = _query_params(event_id="window-implicit-cutoff-completion")
        params["start"] = ["2026-08-19T11:15:00Z"]
        params["end"] = ["2026-08-20T11:15:00Z"]
        del params["cutoff"]
        request = window_request_from_query(params)
        clock_value = [datetime(2026, 8, 19, 11, 59, 59, tzinfo=UTC)]
        calls = []

        def fetch(url):
            calls.append(url)
            clock_value[0] = request.effective_cutoff
            return _window_payload(
                start=request.horizon_start,
                hours=request.duration_hours,
            )

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            try:
                service = _service(
                    fetcher=fetch,
                    receipt_store=store,
                    clock=lambda: clock_value[0],
                )

                with self.assertRaises(ForecastCutoffPassed):
                    service.forecast_window(
                        request,
                        request_id="implicit-cutoff-completion",
                    )

                self.assertEqual(len(calls), 1)
                self.assertEqual(store.row_count(), 0)
                self.assertIsNone(store.get(request.event_id))
            finally:
                store.close()

    def test_normalized_window_identity_replays_from_an_aligned_retry(self):
        unaligned = _query_params()
        unaligned["start"] = ["2026-08-17T15:45:43Z"]
        unaligned["end"] = ["2026-08-18T15:45:43Z"]
        del unaligned["cutoff"]
        aligned = _query_params()
        aligned["start"] = ["2026-08-17T16:00:00Z"]
        aligned["end"] = ["2026-08-18T16:00:00Z"]
        del aligned["cutoff"]

        first_request = window_request_from_query(unaligned)
        retry_request = window_request_from_query(aligned)
        self.assertEqual(first_request, retry_request)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            calls = []
            first_store = SqliteReceiptStore(path)
            first_service = _service(
                fetcher=lambda url: calls.append(url)
                or _window_payload(
                    start=first_request.horizon_start,
                    hours=first_request.duration_hours,
                ),
                receipt_store=first_store,
            )
            first = first_service.forecast_window(
                first_request,
                request_id="unaligned-window",
            )
            frozen_response = first.to_public_response()
            first_store.close()

            replay_store = SqliteReceiptStore(path)
            replay_service = _service(
                fetcher=lambda url: self.fail(
                    "canonicalized receipt replay must not fetch"
                ),
                receipt_store=replay_store,
            )
            replay = replay_service.forecast_window(
                retry_request,
                request_id="aligned-window-retry",
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(replay.request, first.request)
            self.assertEqual(replay.to_public_response(), frozen_response)
            self.assertEqual(replay.request_id, "unaligned-window")
            self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
            replay_store.close()

    def test_schema_v3_receipt_persists_replays_and_freezes_public_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_calls = []
            first_store = SqliteReceiptStore(path)
            first_service = _service(
                fetcher=lambda url: first_calls.append(url)
                or _temperature_payload(),
                receipt_store=first_store,
            )
            request = forecast_request_from_query(
                _compatibility_params(event_id="temperature-receipt-v3"),
                reference_time=FIXED_NOW,
            )
            self.assertIsInstance(request, TemperatureWindowRequest)
            first = first_service.forecast_temperature_window(
                request,
                request_id="first-temperature",
            )
            frozen_response = first.to_public_response()
            stored = first_store.get(request.event_id)

            self.assertEqual(len(first_calls), 1)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["schema_version"], 3)
            self.assertEqual(stored["public_response"], frozen_response)
            self.assertEqual(
                stored["question"],
                {
                    "request_contract": "temperature_window_v3",
                    "event_id": request.event_id,
                    "location_name": "requested location",
                    "latitude": 6.5244,
                    "longitude": 3.3792,
                    "forecast_hours": 24,
                    "hourly": "2t",
                    "timezone": "UTC",
                    "spatial_semantics": "point",
                },
            )
            self.assertNotIn("reference_time", stored["question"])
            self.assertNotIn("horizon_start", stored["question"])
            self.assertNotIn("horizon_end", stored["question"])
            self.assertEqual(stored["resolved_request"], request.to_dict())
            self.assertEqual(first.receipt_sha256, stored["receipt_sha256"])

            replay_store = SqliteReceiptStore(path)
            replay_service = _service(
                fetcher=lambda url: self.fail("receipt replay must not fetch"),
                receipt_store=replay_store,
                clock=lambda: datetime(2026, 8, 17, 13, tzinfo=UTC),
            )
            changed_response = {
                "content": "changed temperature renderer",
                "reference_time": "2026-08-17T00:00:00Z",
                "hourly": {"time": [], "2t": []},
                "hourly_units": {"time": "iso8601", "2t": "K"},
            }
            with unittest.mock.patch(
                "oathcast.service.public_temperature_window_response",
                return_value=changed_response,
            ):
                replay_request = forecast_request_from_query(
                    _compatibility_params(event_id="temperature-receipt-v3"),
                    reference_time=datetime(2026, 8, 17, 13, 37, tzinfo=UTC),
                )
                replay = replay_service.forecast_temperature_window(
                    replay_request,
                    request_id="retry-temperature",
                )

            self.assertEqual(replay.to_public_response(), frozen_response)
            self.assertEqual(replay.request, request)
            self.assertEqual(replay.request_id, "first-temperature")
            self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
            self.assertEqual(replay_store.row_count(), 1)
            first_store.close()
            replay_store.close()

    def test_schema_v3_explicit_id_rejects_changed_client_fields_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            calls = []
            service = _service(
                fetcher=lambda url: calls.append(url) or _temperature_payload(hours=2),
                receipt_store=store,
            )
            first = forecast_request_from_query(
                _compatibility_params(hours=2, event_id="temperature-client-conflict"),
                reference_time=FIXED_NOW,
            )
            service.forecast_temperature_window(first, request_id="temperature-first")
            changed = forecast_request_from_query(
                _compatibility_params(hours=3, event_id="temperature-client-conflict"),
                reference_time=FIXED_NOW + timedelta(hours=1),
            )

            with self.assertRaisesRegex(ReceiptConflict, "different question"):
                service.forecast_temperature_window(
                    changed,
                    request_id="temperature-conflict",
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(store.row_count(), 1)
            store.close()

    def test_temperature_forecast_is_not_fetched_at_or_after_its_first_hour(self):
        request = forecast_request_from_query(
            _compatibility_params(event_id="temperature-cutoff"),
            reference_time=FIXED_NOW,
        )
        service = _service(
            fetcher=lambda url: self.fail("expired temperature request must not fetch"),
            clock=lambda: self.fail("explicit accepted_at must be used"),
        )
        with self.assertRaises(ForecastCutoffPassed):
            service.forecast_temperature_window(
                request,
                request_id="temperature-cutoff",
                accepted_at=request.horizon_start,
            )

    def test_temperature_request_crossing_next_utc_hour_is_persisted(self):
        accepted_at = datetime(2026, 8, 17, 22, 59, 59, tzinfo=UTC)
        completed_at = datetime(2026, 8, 17, 23, 0, 1, tzinfo=UTC)
        clock_value = [accepted_at]

        def fetch(url):
            clock_value[0] = completed_at
            return _temperature_payload(
                start=datetime(2026, 8, 17, 23, tzinfo=UTC)
            )

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
            service = _service(
                fetcher=fetch,
                receipt_store=store,
                clock=lambda: clock_value[0],
            )
            status, payload, headers, _ = _handler_request(
                service,
                "/predict?"
                + urlencode(
                    _compatibility_params(event_id="temperature-hour-boundary"),
                    doseq=True,
                ),
            )
            stored = store.get("temperature-hour-boundary")

            self.assertEqual(status, 200)
            self.assertEqual(payload["reference_time"], "2026-08-17T22:00:00Z")
            self.assertEqual(payload["hourly"]["time"][0], "2026-08-17T23:00:00Z")
            self.assertIn("X-OathCast-Receipt-SHA256", headers)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["schema_version"], 3)
            self.assertEqual(stored["created_at"], "2026-08-17T23:00:01Z")
            self.assertEqual(stored["forecast"]["issued_at"], "2026-08-17T22:59:59Z")
            self.assertEqual(stored["forecast"]["retrieved_at"], "2026-08-17T23:00:01Z")
            self.assertEqual(stored["public_response"], payload)
            store.close()

    def test_concurrent_schema_v3_explicit_id_across_hours_uses_first_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            first_store = SqliteReceiptStore(path)
            second_store = SqliteReceiptStore(path)
            first_request = forecast_request_from_query(
                _compatibility_params(event_id="temperature-hour-race"),
                reference_time=FIXED_NOW,
            )
            second_request = forecast_request_from_query(
                _compatibility_params(event_id="temperature-hour-race"),
                reference_time=FIXED_NOW + timedelta(hours=1),
            )
            fetch_barrier = threading.Barrier(2)
            calls = []

            def fetch_for(request):
                def fetch(url):
                    calls.append(url)
                    fetch_barrier.wait(timeout=5)
                    return _temperature_payload(
                        hours=request.forecast_hours,
                        start=request.horizon_start,
                    )

                return fetch

            first_service = _service(
                fetcher=fetch_for(first_request),
                receipt_store=first_store,
                clock=lambda: FIXED_NOW,
            )
            second_service = _service(
                fetcher=fetch_for(second_request),
                receipt_store=second_store,
                clock=lambda: FIXED_NOW + timedelta(hours=1),
            )
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = (
                        executor.submit(
                            first_service.forecast_temperature_window,
                            first_request,
                            request_id="temperature-hour-11",
                        ),
                        executor.submit(
                            second_service.forecast_temperature_window,
                            second_request,
                            request_id="temperature-hour-12",
                        ),
                    )
                    results = tuple(future.result(timeout=10) for future in futures)

                self.assertEqual(len(calls), 2)
                self.assertEqual(results[0].request, results[1].request)
                self.assertEqual(results[0].request_id, results[1].request_id)
                self.assertEqual(results[0].receipt_sha256, results[1].receipt_sha256)
                self.assertEqual(
                    results[0].to_public_response(),
                    results[1].to_public_response(),
                )
                self.assertIn(results[0].request, (first_request, second_request))
                self.assertEqual(first_store.row_count(), 1)
            finally:
                first_store.close()
                second_store.close()

    def test_schema_v1_v2_and_v3_conflict_under_one_explicit_event_id(self):
        for first_contract in ("point", "window", "temperature"):
            for second_contract in ("point", "window", "temperature"):
                if first_contract == second_contract:
                    continue
                with (
                    self.subTest(first=first_contract, second=second_contract),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
                    calls = []
                    service = _service(
                        fetcher=lambda url: calls.append(url) or _provider_payload(url),
                        receipt_store=store,
                    )
                    event_id = f"shared-three-contracts-{first_contract}-{second_contract}"
                    question = question_from_query(
                        _query_params(hours=1, event_id=event_id)
                    )
                    window = window_request_from_query(
                        _query_params(event_id=event_id)
                    )
                    temperature = forecast_request_from_query(
                        _compatibility_params(event_id=event_id),
                        reference_time=FIXED_NOW,
                    )
                    contracts = {
                        "point": lambda: service.forecast(
                            question,
                            request_id="point-contract",
                        ),
                        "window": lambda: service.forecast_window(
                            window,
                            request_id="window-contract",
                        ),
                        "temperature": lambda: service.forecast_temperature_window(
                            temperature,
                            request_id="temperature-contract",
                        ),
                    }
                    first_result = contracts[first_contract]()
                    stored_before = store.get(event_id)
                    with self.assertRaisesRegex(
                        ReceiptConflict,
                        "different forecast contract",
                    ):
                        contracts[second_contract]()
                    stored_after = store.get(event_id)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(store.row_count(), 1)
                    self.assertEqual(
                        stored_after["receipt_sha256"],
                        stored_before["receipt_sha256"],
                    )
                    self.assertEqual(
                        first_result.receipt_sha256,
                        stored_before["receipt_sha256"],
                    )
                    store.close()

    def test_schema_v1_and_v2_conflict_under_the_same_explicit_event_id(self):
        for first_contract in ("point", "window"):
            with (
                self.subTest(first_contract=first_contract),
                tempfile.TemporaryDirectory() as directory,
            ):
                store = SqliteReceiptStore(Path(directory) / "receipts.sqlite3")
                service = _service(
                    fetcher=lambda url: _provider_payload(url),
                    receipt_store=store,
                )
                event_id = f"shared-contract-{first_contract}"
                question = question_from_query(
                    _query_params(hours=1, event_id=event_id)
                )
                request = window_request_from_query(
                    _query_params(event_id=event_id)
                )

                if first_contract == "point":
                    service.forecast(question, request_id="point-first")
                    with self.assertRaisesRegex(ReceiptConflict, "different forecast contract"):
                        service.forecast_window(request, request_id="window-conflict")
                else:
                    service.forecast_window(request, request_id="window-first")
                    with self.assertRaisesRegex(ReceiptConflict, "different forecast contract"):
                        service.forecast(question, request_id="point-conflict")
                store.close()

    def test_default_provider_order_must_contain_a_window_capable_provider(self):
        service = _service(
            fetcher=lambda url: self.fail("capability rejection must not fetch"),
            provider_order=["weatherapi"],
            allow_unverified_providers=True,
        )
        request = window_request_from_query(_query_params())

        with self.assertRaisesRegex(
            ProviderUnavailable,
            "no configured provider supports complete 1-to-168-hour",
        ):
            service.forecast_window(request, request_id="no-window-provider")


class WindowCandidateManifestTests(unittest.TestCase):
    def test_candidate_is_explicitly_unregistered_and_registered_bytes_stay_pinned(self):
        registered_bytes = REGISTERED_MANIFEST.read_bytes()
        candidate_bytes = WINDOW_CANDIDATE_MANIFEST.read_bytes()
        registered = registered_bytes.decode("utf-8")
        candidate = candidate_bytes.decode("utf-8")

        self.assertEqual(len(registered_bytes), 4_960)
        self.assertEqual(
            hashlib.sha256(registered_bytes).hexdigest(),
            REGISTERED_MANIFEST_SHA256,
        )
        self.assertNotEqual(candidate_bytes, registered_bytes)
        self.assertTrue(
            candidate.startswith("# UNREGISTERED COMPATIBILITY CANDIDATE ONLY.\n")
        )
        self.assertIn("uploaded, signed", candidate)
        self.assertIn("# This file documents the additive 1-to-24-hour", candidate)
        self.assertIn("# registered OathCast Miner contract", candidate)
        self.assertIn(
            "description: Unregistered compatibility manifest",
            candidate,
        )
        self.assertIn("served additively behind the existing Miner endpoint", candidate)
        self.assertIn("\nid: 0\n", candidate)
        self.assertIn("\nslug: oathcast-weather-window-unregistered\n", candidate)
        self.assertIn("\n    external_path: /v1/forecast/point\n", candidate)
        self.assertIn("\n          - name: forecast_hours\n", candidate)
        self.assertIn("\n          - name: hourly\n", candidate)
        self.assertIn("\n        2t:\n", candidate)
        self.assertIn("\nid: 64173\n", registered)
        self.assertIn("\nslug: oathcast-weather\n", registered)
        self.assertIn("\n    external_path: /v1/forecast/point\n", registered)
        self.assertNotIn("\nid: 64173\n", candidate)


if __name__ == "__main__":
    unittest.main()
