import json
from datetime import datetime, timedelta, timezone
import math
import unittest
from urllib.parse import parse_qs, urlparse

from oathcast.adapters import OpenMeteoWindowAdapter
from oathcast.adapters.base import AdapterError
from oathcast.forecast import (
    WINDOW_PROBABILITY_SEMANTICS,
    CanonicalWindowForecast,
    ForecastWindowRequest,
    HourlyWindowForecast,
)
from oathcast.render import (
    public_window_response,
    public_window_response_json,
    render_window_forecast_content,
)


UTC = timezone.utc
START = datetime(2026, 8, 17, 15, tzinfo=UTC)
ISSUED_AT = datetime(2026, 8, 17, 12, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 17, 12, 0, 5, tzinfo=UTC)


def _request(*, hours=24, event_id="window-24", **changes):
    values = {
        "event_id": event_id,
        "location_name": "Lagos",
        "latitude": 6.5244,
        "longitude": 3.3792,
        "horizon_start": START,
        "horizon_end": START + timedelta(hours=hours),
        "forecast_cutoff": ISSUED_AT,
    }
    values.update(changes)
    return ForecastWindowRequest(**values)


def _payload(request, *, model="fixture-open-meteo-window"):
    # Open-Meteo's precipitation probability at an hourly timestamp describes
    # the preceding hour, so an N-hour window needs N+1 timestamped rows.
    times = [
        request.horizon_start + timedelta(hours=index)
        for index in range(request.duration_hours + 1)
    ]
    return {
        "model": model,
        "hourly": {
            "time": [value.strftime("%Y-%m-%dT%H:%M") for value in times],
            "temperature_2m": [20.0 + index / 2 for index in range(len(times))],
            "precipitation_probability": [index for index in range(len(times))],
        },
    }


def _hour(index, *, temperature=None, probability=None):
    interval_start = START + timedelta(hours=index)
    return HourlyWindowForecast(
        interval_start=interval_start,
        interval_end=interval_start + timedelta(hours=1),
        temperature_2m_c=(20.0 + index if temperature is None else temperature),
        precipitation_probability=(
            index / 100 if probability is None else probability
        ),
    )


def _forecast(request, *, hours=None):
    count = request.duration_hours
    if hours is None:
        hours = tuple(_hour(index) for index in range(count))
    return CanonicalWindowForecast(
        event_id=request.event_id,
        provider="open_meteo",
        horizon_start=request.horizon_start,
        horizon_end=request.horizon_end,
        issued_at=ISSUED_AT,
        hours=tuple(hours),
        temperature_native_definition="fixture temperature definition",
        precipitation_native_definition="fixture precipitation definition",
        event_equivalence="documented_hourly_window",
        adapter_version="open_meteo_window_v1",
        provider_model="fixture-open-meteo-window",
        retrieved_at=RETRIEVED_AT,
    )


class ForecastWindowModelTests(unittest.TestCase):
    def test_request_accepts_inclusive_one_to_twenty_four_hour_bounds(self):
        for hours in (1, 24):
            with self.subTest(hours=hours):
                request = _request(hours=hours, event_id=f"window-{hours}")
                self.assertEqual(request.duration_hours, hours)
                self.assertEqual(
                    ForecastWindowRequest.from_dict(request.to_dict()),
                    request,
                )

    def test_request_rejects_duration_and_alignment_outside_contract(self):
        invalid = {
            "zero hours": {"horizon_end": START},
            "more than 24 hours": {
                "horizon_end": START + timedelta(hours=25)
            },
            "unaligned start": {
                "horizon_start": START + timedelta(minutes=30),
                "horizon_end": START + timedelta(hours=1, minutes=30),
            },
            "unaligned end": {
                "horizon_end": START + timedelta(hours=2, minutes=30)
            },
            "cutoff after start": {
                "forecast_cutoff": START + timedelta(seconds=1)
            },
        }
        for label, changes in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                _request(hours=1, **changes)

    def test_request_allows_a_cutoff_at_the_window_opening(self):
        """A window forecast may be committed right up to the window opening.

        The one-hour point contract still demands strict lead time. A window
        already reaches up to 24 hours forward, so requiring an extra hour of
        lead rejected Telegraph's "next 24 hours" request without protecting
        anything, and the service default now uses the opening itself.
        """

        request = _request(hours=24, forecast_cutoff=START)
        self.assertEqual(request.forecast_cutoff, START)
        self.assertEqual(request.horizon_start, START)

    def test_request_rejects_non_finite_coordinates_and_unsupported_semantics(self):
        invalid = {
            "latitude too low": {"latitude": -90.0001},
            "latitude too high": {"latitude": 90.0001},
            "latitude non-finite": {"latitude": math.nan},
            "longitude too low": {"longitude": -180.0001},
            "longitude too high": {"longitude": 180.0001},
            "longitude non-finite": {"longitude": math.inf},
            "non-UTC timezone": {"timezone": "Africa/Lagos"},
            "non-point location": {"spatial_semantics": "area"},
            "unsupported operator": {"operator": ">="},
            "unsupported threshold": {"threshold_mm": 1.0},
        }
        for label, changes in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                _request(**changes)

    def test_hourly_forecast_validates_interval_temperature_and_probability(self):
        invalid = {
            "short interval": {"interval_end": START + timedelta(minutes=30)},
            "unaligned interval": {
                "interval_start": START + timedelta(minutes=30),
                "interval_end": START + timedelta(hours=1, minutes=30),
            },
            "nan temperature": {"temperature_2m_c": math.nan},
            "infinite temperature": {"temperature_2m_c": math.inf},
            "negative probability": {"precipitation_probability": -0.001},
            "probability above one": {"precipitation_probability": 1.001},
            "nan probability": {"precipitation_probability": math.nan},
        }
        base = {
            "interval_start": START,
            "interval_end": START + timedelta(hours=1),
            "temperature_2m_c": 27.5,
            "precipitation_probability": 0.4,
        }
        for label, changes in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                HourlyWindowForecast(**(base | changes))

    def test_canonical_window_requires_complete_unique_contiguous_coverage(self):
        request = _request(hours=3)
        complete = [_hour(0), _hour(1), _hour(2)]
        self.assertEqual(len(_forecast(request, hours=complete).hours), 3)

        invalid = {
            "missing hour": complete[:2],
            "duplicate hour": [complete[0], complete[1], complete[1]],
            "out of order": [complete[1], complete[0], complete[2]],
        }
        for label, hours in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                _forecast(request, hours=hours)

    def test_canonical_window_horizon_must_align_to_whole_utc_hours(self):
        with self.assertRaisesRegex(ValueError, "horizon_start.*whole UTC hour"):
            CanonicalWindowForecast(
                event_id="unaligned-canonical-window",
                provider="fixture",
                horizon_start=START + timedelta(minutes=30),
                horizon_end=START + timedelta(hours=1, minutes=30),
                issued_at=ISSUED_AT,
                hours=(),
                temperature_native_definition="fixture temperature definition",
                precipitation_native_definition="fixture precipitation definition",
                event_equivalence="documented_hourly_window",
                adapter_version="fixture-window-v1",
            )

    def test_peak_probability_uses_the_earliest_hour_on_a_tie(self):
        request = _request(hours=3)
        forecast = _forecast(
            request,
            hours=(
                _hour(0, temperature=28.5, probability=0.4),
                _hour(1, temperature=24.25, probability=0.8),
                _hour(2, temperature=31.75, probability=0.8),
            ),
        )
        self.assertEqual(forecast.minimum_hourly_temperature_c, 24.25)
        self.assertEqual(forecast.maximum_hourly_temperature_c, 31.75)
        self.assertEqual(forecast.probability, 0.8)
        self.assertEqual(
            forecast.peak_precipitation_hour.interval_start,
            START + timedelta(hours=1),
        )
        self.assertEqual(forecast.probability_semantics, WINDOW_PROBABILITY_SEMANTICS)
        self.assertEqual(CanonicalWindowForecast.from_dict(forecast.to_dict()), forecast)


class OpenMeteoWindowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = OpenMeteoWindowAdapter()

    def test_url_requests_only_the_documented_utc_window_fields(self):
        query = parse_qs(urlparse(self.adapter.build_url(_request())).query)
        self.assertEqual(
            query,
            {
                "forecast_days": ["7"],
                "hourly": ["temperature_2m,precipitation_probability"],
                "latitude": ["6.524400"],
                "longitude": ["3.379200"],
                "temperature_unit": ["celsius"],
                "timezone": ["UTC"],
            },
        )

    def test_provider_metadata_must_confirm_utc_and_celsius_when_present(self):
        request = _request(hours=2)
        accepted = _payload(request)
        accepted["timezone"] = "GMT"
        accepted["utc_offset_seconds"] = 0
        accepted["hourly_units"] = {
            "time": "iso8601",
            "temperature_2m": "\N{DEGREE SIGN}C",
            "precipitation_probability": "%",
        }
        self.assertEqual(
            len(self.adapter.parse(accepted, request, issued_at=ISSUED_AT).hours),
            2,
        )

        invalid = (
            ("non-UTC timezone", {"timezone": "Africa/Lagos"}, "must use UTC"),
            ("non-zero offset", {"utc_offset_seconds": 3600}, "zero UTC offset"),
            (
                "non-Celsius temperature",
                {"hourly_units": {"temperature_2m": "\N{DEGREE SIGN}F"}},
                "must use Celsius",
            ),
            (
                "non-percent probability",
                {"hourly_units": {"precipitation_probability": "fraction"}},
                "must use percent units",
            ),
        )
        for label, changes, message in invalid:
            payload = _payload(request)
            payload.update(changes)
            with self.subTest(label=label), self.assertRaisesRegex(
                AdapterError,
                message,
            ):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_complete_twenty_four_hour_response_preserves_every_interval(self):
        request = _request()
        payload = _payload(request)
        payload["hourly"]["temperature_2m"][0] = 17.25
        payload["hourly"]["temperature_2m"][23] = 34.75
        payload["hourly"]["precipitation_probability"][8] = 91

        forecast = self.adapter.parse(
            payload,
            request,
            issued_at=ISSUED_AT,
            retrieved_at=RETRIEVED_AT,
        )

        self.assertEqual(len(forecast.hours), 24)
        self.assertEqual(forecast.hours[0].interval_start, request.horizon_start)
        self.assertEqual(forecast.hours[-1].interval_end, request.horizon_end)
        self.assertEqual(forecast.hours[0].temperature_2m_c, 17.25)
        self.assertEqual(forecast.hours[-1].temperature_2m_c, 34.75)
        self.assertEqual(forecast.minimum_hourly_temperature_c, 17.25)
        self.assertEqual(forecast.maximum_hourly_temperature_c, 34.75)
        self.assertAlmostEqual(forecast.probability, 0.91)
        self.assertEqual(
            forecast.peak_precipitation_hour.interval_start,
            request.horizon_start + timedelta(hours=7),
        )
        self.assertEqual(forecast.provider_model, "fixture-open-meteo-window")
        self.assertEqual(forecast.retrieved_at, RETRIEVED_AT)

    def test_missing_required_temperature_or_precipitation_timestamp_is_rejected(self):
        request = _request(hours=3)
        cases = {
            "window start temperature": 0,
            "interior timestamp": 2,
            "window end precipitation": 3,
        }
        for label, missing_index in cases.items():
            payload = _payload(request)
            for values in payload["hourly"].values():
                if isinstance(values, list):
                    del values[missing_index]
            with self.subTest(label=label), self.assertRaises(AdapterError):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_duplicate_required_timestamp_is_rejected(self):
        request = _request(hours=2)
        payload = _payload(request)
        for field_name in ("time", "temperature_2m", "precipitation_probability"):
            payload["hourly"][field_name].insert(2, payload["hourly"][field_name][1])

        with self.assertRaisesRegex(AdapterError, "duplicate hourly timestamps"):
            self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_irrelevant_extra_rows_do_not_poison_a_complete_requested_window(self):
        request = _request(hours=2)
        payload = _payload(request)
        payload["hourly"]["time"] = ["2026-08-17T14:00"] + payload["hourly"]["time"] + [
            "2026-08-17T18:00"
        ]
        payload["hourly"]["temperature_2m"] = [None] + payload["hourly"][
            "temperature_2m"
        ] + ["not-a-number"]
        payload["hourly"]["precipitation_probability"] = [math.nan] + payload[
            "hourly"
        ]["precipitation_probability"] + [math.inf]

        forecast = self.adapter.parse(payload, request, issued_at=ISSUED_AT)

        self.assertEqual(len(forecast.hours), 2)
        self.assertEqual(
            [hour.temperature_2m_c for hour in forecast.hours],
            [20.0, 20.5],
        )

    def test_duplicate_irrelevant_timestamp_is_still_rejected(self):
        request = _request(hours=2)
        payload = _payload(request)
        for field_name, value in (
            ("time", "2026-08-17T14:00"),
            ("temperature_2m", None),
            ("precipitation_probability", math.nan),
        ):
            payload["hourly"][field_name][0:0] = [value, value]

        with self.assertRaisesRegex(AdapterError, "duplicate hourly timestamps"):
            self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_non_finite_values_on_required_rows_are_rejected(self):
        request = _request(hours=2)
        cases = {
            "required temperature": ("temperature_2m", 0, math.nan),
            "required precipitation": (
                "precipitation_probability",
                2,
                math.inf,
            ),
        }
        for label, (field_name, index, value) in cases.items():
            payload = _payload(request)
            payload["hourly"][field_name][index] = value
            with self.subTest(label=label), self.assertRaisesRegex(
                AdapterError, "non-finite"
            ):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_non_numeric_values_on_required_rows_are_rejected(self):
        request = _request(hours=2)
        cases = {
            "required temperature": ("temperature_2m", 0, None, "temperature_2m"),
            "required precipitation": (
                "precipitation_probability",
                1,
                True,
                "precipitation_probability",
            ),
        }
        for label, (field_name, index, value, message) in cases.items():
            payload = _payload(request)
            payload["hourly"][field_name][index] = value
            with self.subTest(label=label), self.assertRaisesRegex(
                AdapterError,
                f"non-numeric {message}",
            ):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_required_precipitation_probability_must_be_in_percent_bounds(self):
        request = _request(hours=2)
        for value in (-0.01, 100.01):
            payload = _payload(request)
            payload["hourly"]["precipitation_probability"][1] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                AdapterError, r"outside \[0, 100\]"
            ):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)


class ForecastWindowRendererTests(unittest.TestCase):
    def setUp(self):
        self.request = _request(hours=3, event_id="render-window")
        self.forecast = _forecast(
            self.request,
            hours=(
                _hour(0, temperature=28.125, probability=0.12),
                _hour(1, temperature=23.444, probability=0.87654),
                _hour(2, temperature=31.996, probability=0.33),
            ),
        )

    def test_public_response_exposes_temperature_range_and_peak_hour_semantics(self):
        response = public_window_response(self.request, self.forecast)

        self.assertEqual(response["minimum_hourly_temperature_c"], 23.44)
        self.assertEqual(response["maximum_hourly_temperature_c"], 32.0)
        self.assertEqual(response["probability"], 0.8765)
        self.assertEqual(response["max_hourly_precipitation_probability"], 0.8765)
        self.assertEqual(response["probability_semantics"], WINDOW_PROBABILITY_SEMANTICS)
        self.assertEqual(
            response["max_hourly_precipitation_interval"],
            {
                "start": "2026-08-17T16:00:00Z",
                "end": "2026-08-17T17:00:00Z",
            },
        )
        self.assertIn("minimum hourly temperature is 23.44 C", response["content"])
        self.assertIn("maximum hourly temperature is 32 C", response["content"])
        self.assertIn("87.65%", response["content"])
        self.assertIn("from 16:00 to 17:00 UTC", response["content"])
        self.assertNotIn("whole-window probability", response["content"].lower())

    def test_twenty_four_hour_content_states_both_window_dates(self):
        request = _request()
        forecast = _forecast(request)
        content = render_window_forecast_content(request, forecast)

        self.assertIn("15:00 UTC on 17 August 2026", content)
        self.assertIn("15:00 UTC on 18 August 2026", content)

    def test_public_json_is_compact_stable_and_round_trips(self):
        encoded = public_window_response_json(self.request, self.forecast)
        self.assertEqual(json.loads(encoded), public_window_response(self.request, self.forecast))
        self.assertNotIn("\n", encoded)
        self.assertNotIn(": ", encoded)

    def test_renderer_rejects_mismatched_event_ids(self):
        other_request = _request(hours=3, event_id="different")
        with self.assertRaisesRegex(ValueError, "event_id"):
            render_window_forecast_content(other_request, self.forecast)

    def test_renderer_rejects_mismatched_horizons(self):
        shorter_request = _request(hours=2, event_id=self.request.event_id)
        shorter_forecast = _forecast(shorter_request)
        with self.assertRaisesRegex(ValueError, "horizon"):
            render_window_forecast_content(self.request, shorter_forecast)


if __name__ == "__main__":
    unittest.main()
