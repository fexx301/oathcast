import json
from datetime import datetime, timedelta, timezone
import math
import unittest
from urllib.parse import parse_qs, urlparse

from oathcast.adapters import OpenMeteoTemperatureWindowAdapter
from oathcast.adapters.base import AdapterError
from oathcast.forecast import (
    CanonicalTemperatureWindowForecast,
    HourlyTemperatureForecast,
    TemperatureWindowRequest,
)
from oathcast.render import (
    public_temperature_window_response,
    public_temperature_window_response_json,
    render_temperature_window_content,
)


UTC = timezone.utc
REFERENCE_TIME = datetime(2026, 8, 17, 11, tzinfo=UTC)
ISSUED_AT = datetime(2026, 8, 17, 11, 0, 5, tzinfo=UTC)


def _request(*, hours=3, event_id="temperature-window", **changes):
    values = {
        "event_id": event_id,
        "location_name": "Lagos",
        "latitude": 6.5244,
        "longitude": 3.3792,
        "forecast_hours": hours,
        "reference_time": REFERENCE_TIME,
    }
    values.update(changes)
    return TemperatureWindowRequest(**values)


def _payload(request, *, timezone_name="UTC"):
    times = [
        request.horizon_start + timedelta(hours=index)
        for index in range(request.forecast_hours)
    ]
    return {
        "model": "fixture-open-meteo-temperature",
        "timezone": timezone_name,
        "utc_offset_seconds": 0,
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "\N{DEGREE SIGN}C",
        },
        "hourly": {
            "time": [value.strftime("%Y-%m-%dT%H:%M") for value in times],
            "temperature_2m": [25.0 + index * 0.5 for index in range(len(times))],
        },
    }


def _forecast(request, temperatures=(25.0, 26.5, 24.25)):
    return CanonicalTemperatureWindowForecast(
        event_id=request.event_id,
        provider="open_meteo",
        reference_time=request.reference_time,
        issued_at=ISSUED_AT,
        hours=tuple(
            HourlyTemperatureForecast(
                interval_start=request.horizon_start + timedelta(hours=index),
                temperature_2m_c=temperature,
            )
            for index, temperature in enumerate(temperatures)
        ),
        temperature_native_definition="fixture temperature definition",
        adapter_version="open_meteo_temperature_window_v1",
        provider_model="fixture-open-meteo-temperature",
        retrieved_at=ISSUED_AT,
    )


class TemperatureWindowModelTests(unittest.TestCase):
    def test_request_accepts_inclusive_one_to_twenty_four_hour_bounds(self):
        for hours in (1, 24):
            with self.subTest(hours=hours):
                request = _request(hours=hours, event_id=f"temperature-{hours}")
                self.assertEqual(request.horizon_start, REFERENCE_TIME + timedelta(hours=1))
                self.assertEqual(
                    request.horizon_end,
                    REFERENCE_TIME + timedelta(hours=hours + 1),
                )
                self.assertEqual(TemperatureWindowRequest.from_dict(request.to_dict()), request)

    def test_request_rejects_non_integer_bounds_and_unaligned_reference_time(self):
        for value in (True, 1.0, 1.5, 0, 25):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _request(hours=value)
        with self.assertRaisesRegex(ValueError, "whole UTC hour"):
            _request(reference_time=REFERENCE_TIME + timedelta(seconds=1))

    def test_request_from_dict_rejects_inconsistent_derived_horizons(self):
        data = _request().to_dict()
        data["horizon_start"] = "2026-08-17T13:00:00Z"
        with self.assertRaisesRegex(ValueError, "horizon_start"):
            TemperatureWindowRequest.from_dict(data)

    def test_request_from_dict_rejects_coerced_forecast_hours(self):
        for value in (True, 1.0, 1.5, "1"):
            with self.subTest(value=value):
                data = _request().to_dict()
                data["forecast_hours"] = value
                with self.assertRaisesRegex(ValueError, "whole number"):
                    TemperatureWindowRequest.from_dict(data)

    def test_canonical_forecast_requires_exact_contiguous_next_hour_coverage(self):
        request = _request()
        forecast = _forecast(request)
        self.assertEqual(
            CanonicalTemperatureWindowForecast.from_dict(forecast.to_dict()),
            forecast,
        )
        invalid_hours = (
            (),
            (forecast.hours[0], forecast.hours[0], forecast.hours[2]),
            (forecast.hours[1], forecast.hours[0], forecast.hours[2]),
        )
        for hours in invalid_hours:
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                CanonicalTemperatureWindowForecast(
                    event_id=request.event_id,
                    provider="open_meteo",
                    reference_time=request.reference_time,
                    issued_at=ISSUED_AT,
                    hours=hours,
                    temperature_native_definition="fixture",
                    adapter_version="fixture-v1",
                )


class OpenMeteoTemperatureWindowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = OpenMeteoTemperatureWindowAdapter()

    def test_url_requests_temperature_only_in_utc(self):
        query = parse_qs(urlparse(self.adapter.build_url(_request())).query)
        self.assertEqual(
            query,
            {
                "forecast_days": ["3"],
                "hourly": ["temperature_2m"],
                "latitude": ["6.524400"],
                "longitude": ["3.379200"],
                "temperature_unit": ["celsius"],
                "timezone": ["UTC"],
            },
        )

    def test_metadata_requires_realistic_zero_offset_utc_and_celsius_units(self):
        request = _request(hours=1)
        for timezone_name in ("UTC", "GMT"):
            with self.subTest(timezone_name=timezone_name):
                forecast = self.adapter.parse(
                    _payload(request, timezone_name=timezone_name),
                    request,
                    issued_at=ISSUED_AT,
                )
                self.assertEqual(len(forecast.hours), 1)

        invalid = {
            "missing timezone": ("timezone", None),
            "non-UTC timezone": ("timezone", "Africa/Lagos"),
            "missing offset": ("utc_offset_seconds", None),
            "boolean offset": ("utc_offset_seconds", False),
            "non-zero offset": ("utc_offset_seconds", 3600),
            "missing units": ("hourly_units", None),
            "wrong time unit": (
                "hourly_units",
                {"time": "unix_time", "temperature_2m": "\N{DEGREE SIGN}C"},
            ),
            "wrong temperature unit": (
                "hourly_units",
                {"time": "iso8601", "temperature_2m": "\N{DEGREE SIGN}F"},
            ),
        }
        for label, (field_name, value) in invalid.items():
            payload = _payload(request)
            if value is None:
                del payload[field_name]
            else:
                payload[field_name] = value
            with self.subTest(label=label), self.assertRaises(AdapterError):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)

    def test_parse_preserves_all_twenty_four_contiguous_temperatures(self):
        request = _request(hours=24)
        payload = _payload(request)
        payload["hourly"]["temperature_2m"][0] = 17.25
        payload["hourly"]["temperature_2m"][-1] = 40.75

        forecast = self.adapter.parse(
            payload,
            request,
            issued_at=ISSUED_AT,
            retrieved_at=ISSUED_AT,
        )

        self.assertEqual(len(forecast.hours), 24)
        self.assertEqual(forecast.hours[0].interval_start, request.horizon_start)
        self.assertEqual(
            forecast.hours[-1].interval_start,
            request.horizon_end - timedelta(hours=1),
        )
        self.assertEqual(forecast.minimum_temperature_2m_c, 17.25)
        self.assertEqual(forecast.maximum_temperature_2m_c, 40.75)
        self.assertEqual(forecast.provider_model, "fixture-open-meteo-temperature")

    def test_parse_rejects_missing_duplicate_unequal_and_non_finite_data(self):
        request = _request()
        cases = {}

        missing = _payload(request)
        del missing["hourly"]["time"][1]
        del missing["hourly"]["temperature_2m"][1]
        cases["missing"] = missing

        duplicate = _payload(request)
        duplicate["hourly"]["time"][1] = duplicate["hourly"]["time"][0]
        cases["duplicate"] = duplicate

        unequal = _payload(request)
        unequal["hourly"]["temperature_2m"].pop()
        cases["unequal"] = unequal

        non_finite = _payload(request)
        non_finite["hourly"]["temperature_2m"][1] = math.nan
        cases["non-finite"] = non_finite

        for label, payload in cases.items():
            with self.subTest(label=label), self.assertRaises(AdapterError):
                self.adapter.parse(payload, request, issued_at=ISSUED_AT)


class TemperatureWindowRendererTests(unittest.TestCase):
    def test_response_uses_aligned_rfc3339_times_kelvin_values_and_scored_content(self):
        request = _request()
        forecast = _forecast(request)

        response = public_temperature_window_response(request, forecast)

        self.assertEqual(response["reference_time"], "2026-08-17T11:00:00Z")
        self.assertEqual(
            response["hourly"]["time"],
            [
                "2026-08-17T12:00:00Z",
                "2026-08-17T13:00:00Z",
                "2026-08-17T14:00:00Z",
            ],
        )
        self.assertEqual(response["hourly"]["2t"], [298.15, 299.65, 297.4])
        self.assertEqual(response["hourly_units"], {"time": "iso8601", "2t": "K"})
        for timestamp, temperature in zip(
            response["hourly"]["time"],
            ("298.15 K", "299.65 K", "297.4 K"),
        ):
            self.assertIn(timestamp, response["content"])
            self.assertIn(temperature, response["content"])
        encoded = public_temperature_window_response_json(request, forecast)
        self.assertEqual(json.loads(encoded), response)
        self.assertNotIn("\n", encoded)

    def test_renderer_rejects_identity_reference_and_count_mismatches(self):
        request = _request()
        forecast = _forecast(request)
        invalid = (
            _request(event_id="other"),
            _request(reference_time=REFERENCE_TIME - timedelta(hours=1)),
            _request(hours=2),
        )
        for other in invalid:
            with self.subTest(other=other), self.assertRaises(ValueError):
                render_temperature_window_content(other, forecast)


if __name__ == "__main__":
    unittest.main()
