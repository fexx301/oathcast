from datetime import date
import unittest

from oathcast.verify import (
    VARIABLES,
    Place,
    VerifyError,
    archive_url,
    convert,
    coordinates_from_location,
    parse_archive,
    parse_geocoding,
    parse_verify_request,
    render_answer,
    verify,
)


TODAY = date(2026, 9, 28)
LISBON = Place("Lisbon", 38.71667, -9.13333, country="Portugal")


def archive_payload(field, values, unit, start="2026-09-15"):
    first = date.fromisoformat(start)
    days = [date.fromordinal(first.toordinal() + i).isoformat() for i in range(len(values))]
    return {
        "latitude": 38.7,
        "longitude": -9.1,
        "timezone": "Europe/Lisbon",
        "daily_units": {"time": "iso8601", field: unit},
        "daily": {"time": days, field: values},
    }


class ParseRequestTests(unittest.TestCase):
    def test_explicit_parameters_win(self):
        request = parse_verify_request(
            {
                "location": "Lisbon",
                "date": "2026-09-15",
                "variable": "precipitation",
                "forecast_value": "12",
                "unit": "mm",
                "question": "Did the 30°C forecast for Paris verify on 2026-09-01?",
            },
            today=TODAY,
        )
        self.assertEqual(request.location, "Lisbon")
        self.assertEqual((request.start, request.end), (date(2026, 9, 15),) * 2)
        self.assertEqual(request.variable.key, "precipitation")
        self.assertEqual((request.forecast_value, request.forecast_unit), (12.0, "mm"))

    def test_reads_place_date_variable_and_claim_from_question(self):
        request = parse_verify_request(
            {"question": "Yesterday's forecast called for 12 mm of rain in Lisbon: did it verify?"},
            today=TODAY,
        )
        self.assertEqual(request.location, "Lisbon")
        self.assertEqual(request.start, date(2026, 9, 27))
        self.assertEqual(request.variable.key, "precipitation")
        self.assertEqual((request.forecast_value, request.forecast_unit), (12.0, "mm"))

    def test_spoken_dates_and_fahrenheit(self):
        request = parse_verify_request(
            {"question": "The high in Denver on September 18, 2026 was forecast at 82°F. Did it verify?"},
            today=TODAY,
        )
        self.assertEqual(request.start, date(2026, 9, 18))
        self.assertEqual(request.variable.key, "temperature_max")
        self.assertEqual((request.forecast_value, request.forecast_unit), (82.0, "°F"))

    def test_year_before_place_is_not_read_as_inches(self):
        request = parse_verify_request(
            {"question": "What fell on 2026-09-15 in Lisbon against a forecast of 12 mm of rain?"},
            today=TODAY,
        )
        self.assertEqual((request.forecast_value, request.forecast_unit), (12.0, "mm"))

    def test_two_dates_make_a_window(self):
        request = parse_verify_request(
            {"location": "Lisbon", "variable": "precipitation",
             "question": "Rain total from 2026-09-10 to 2026-09-12 in Lisbon"},
            today=TODAY,
        )
        self.assertEqual((request.start, request.end, request.days), (date(2026, 9, 10), date(2026, 9, 12), 3))

    def test_refuses_unfinished_window(self):
        with self.assertRaises(VerifyError):
            parse_verify_request({"location": "Lisbon", "date": "2026-09-28", "variable": "precipitation"}, today=TODAY)

    def test_requires_location_and_date(self):
        with self.assertRaises(VerifyError):
            parse_verify_request({"date": "2026-09-01", "variable": "precipitation"}, today=TODAY)
        with self.assertRaises(VerifyError):
            parse_verify_request({"location": "Lisbon", "variable": "precipitation"}, today=TODAY)

    def test_no_claim_is_allowed(self):
        request = parse_verify_request({"location": "Lisbon", "date": "2026-09-15", "variable": "wind"}, today=TODAY)
        self.assertEqual(request.variable.key, "wind_max")
        self.assertIsNone(request.forecast_value)


class AdapterTests(unittest.TestCase):
    def test_coordinates(self):
        self.assertEqual(coordinates_from_location("38.72, -9.14"), (38.72, -9.14))
        self.assertEqual(coordinates_from_location("latitude 14.6042, longitude 120.9822"), (14.6042, 120.9822))
        self.assertIsNone(coordinates_from_location("Lisbon"))

    def test_geocoding_uses_qualifier(self):
        payload = {
            "results": [
                {"name": "Portland", "latitude": 45.52, "longitude": -122.68, "country": "United States", "admin1": "Oregon"},
                {"name": "Portland", "latitude": 43.66, "longitude": -70.26, "country": "United States", "admin1": "Maine"},
            ]
        }
        self.assertEqual(parse_geocoding(payload, "Portland, Maine").latitude, 43.66)
        self.assertEqual(parse_geocoding(payload, "Portland").latitude, 45.52)
        with self.assertRaises(VerifyError):
            parse_geocoding({}, "Nowhere")

    def test_archive_url_asks_for_local_days(self):
        request = parse_verify_request({"location": "Lisbon", "date": "2026-09-15", "variable": "precipitation"}, today=TODAY)
        url = archive_url(LISBON, request)
        self.assertIn("daily=precipitation_sum", url)
        self.assertIn("timezone=auto", url)
        self.assertIn("start_date=2026-09-15", url)

    def test_archive_sums_precipitation_over_window(self):
        request = parse_verify_request(
            {"location": "Lisbon", "start": "2026-09-15", "end": "2026-09-17", "variable": "precipitation"}, today=TODAY
        )
        observation = parse_archive(archive_payload("precipitation_sum", [1.2, 0.0, 2.3], "mm"), LISBON, request)
        self.assertEqual(observation.value, 3.5)
        self.assertEqual(observation.timezone, "Europe/Lisbon")

    def test_archive_refuses_missing_or_misaligned_days(self):
        request = parse_verify_request(
            {"location": "Lisbon", "start": "2026-09-15", "end": "2026-09-16", "variable": "precipitation"}, today=TODAY
        )
        with self.assertRaises(VerifyError):
            parse_archive(archive_payload("precipitation_sum", [1.0, None], "mm"), LISBON, request)
        with self.assertRaises(VerifyError):
            parse_archive(archive_payload("precipitation_sum", [1.0], "mm"), LISBON, request)
        with self.assertRaises(VerifyError):
            parse_archive(archive_payload("precipitation_sum", [1.0, 2.0], "inch"), LISBON, request)


class VerdictTests(unittest.TestCase):
    def _result(self, params, field, values, unit):
        request = parse_verify_request({"location": "Lisbon", "date": "2026-09-15", **params}, today=TODAY)
        return verify(request, parse_archive(archive_payload(field, values, unit), LISBON, request))

    def test_precipitation_outside_tolerance(self):
        result = self._result({"variable": "precipitation", "forecast_value": "12"}, "precipitation_sum", [3.4], "mm")
        self.assertEqual(result.verdict, "not_verified")
        self.assertEqual((result.error, result.tolerance), (-8.6, 3.0))

    def test_dry_forecast_verifies_on_trace_rain(self):
        result = self._result({"variable": "precipitation", "forecast_value": "0"}, "precipitation_sum", [0.4], "mm")
        self.assertEqual(result.verdict, "verified")

    def test_fahrenheit_claim_is_converted(self):
        result = self._result(
            {"variable": "temperature_max", "forecast_value": "82", "unit": "F"}, "temperature_2m_max", [28.9], "°C"
        )
        self.assertAlmostEqual(result.forecast_value, 27.78)
        self.assertEqual(result.verdict, "verified")

    def test_custom_tolerance(self):
        result = self._result(
            {"variable": "temperature_max", "forecast_value": "25", "tolerance": "0.5"}, "temperature_2m_max", [25.8], "°C"
        )
        self.assertEqual(result.verdict, "not_verified")

    def test_incompatible_units_are_refused(self):
        with self.assertRaises(VerifyError):
            convert(10, "mm", VARIABLES["temperature_max"])


class RenderTests(unittest.TestCase):
    def setUp(self):
        request = parse_verify_request(
            {"location": "Lisbon", "date": "2026-09-15", "variable": "precipitation", "forecast_value": "12"}, today=TODAY
        )
        self.result = verify(request, parse_archive(archive_payload("precipitation_sum", [3.4], "mm"), LISBON, request))

    def test_styles(self):
        observed = render_answer(self.result, "observed_only")
        self.assertEqual(
            observed,
            "Lisbon, Portugal recorded 3.4 mm of precipitation on 15 September 2026, according to the "
            "Open-Meteo historical weather archive (ERA5 reanalysis).",
        )
        verdict_first = render_answer(self.result, "verdict_first")
        self.assertTrue(verdict_first.startswith("Not verified. The forecast of 12 mm did not verify, 8.6 mm too high"))
        combined = render_answer(self.result, "observed_then_verdict")
        self.assertTrue(combined.startswith(observed))
        self.assertIn("outside the ±3 mm tolerance", combined)

    def test_temperature_reads_as_english(self):
        request = parse_verify_request(
            {"location": "Lisbon", "date": "2026-09-15", "variable": "temperature_max", "forecast_value": "21"},
            today=TODAY,
        )
        result = verify(request, parse_archive(archive_payload("temperature_2m_max", [19.3], "°C"), LISBON, request))
        self.assertTrue(
            render_answer(result, "observed_only").startswith(
                "Lisbon, Portugal recorded a maximum temperature of 19.3°C on 15 September 2026"
            )
        )

    def test_no_claim_renders_observation_only(self):
        request = parse_verify_request({"location": "Lisbon", "date": "2026-09-15", "variable": "precipitation"}, today=TODAY)
        result = verify(request, parse_archive(archive_payload("precipitation_sum", [3.4], "mm"), LISBON, request))
        self.assertEqual(result.verdict, "no_forecast_supplied")
        self.assertEqual(render_answer(result, "verdict_first"), render_answer(result, "observed_only"))
        self.assertIsNone(result.to_dict()["verified"])


if __name__ == "__main__":
    unittest.main()
