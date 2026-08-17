import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oathcast.service import FORECAST_PATHS
from scripts.smoke_miner import (
    CANONICAL_FORECAST_PATH,
    REGISTERED_FORECAST_PATH,
    format_timestamp,
    receipt_capacity_check,
    receipt_write_check,
    rolling_horizon,
    temperature_smoke_event_id,
    valid_forecast_response,
    valid_temperature_window_response,
)


ROOT = Path(__file__).resolve().parents[1]


def readyz(**capacity) -> dict:
    return {"ready": True, "receipt_store": dict(capacity)}


class ReceiptCapacityCheckTests(unittest.TestCase):
    """The canary must warn while there is still room to act."""

    def test_a_healthy_store_passes_and_reports_headroom(self):
        check = receipt_capacity_check(
            readyz(
                rows=10,
                max_rows=1000,
                used_bytes=1_000,
                max_bytes=1_000_000,
                accepting_new_receipts=True,
            ),
            min_headroom_percent=10.0,
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])
        self.assertAlmostEqual(check["rows_headroom_percent"], 99.0)
        self.assertAlmostEqual(check["bytes_headroom_percent"], 99.9)

    def test_low_headroom_fails_before_the_store_is_actually_full(self):
        # The point of the check: alert while there is still time to act,
        # not once every forecast is already returning 507.
        check = receipt_capacity_check(
            readyz(rows=995, max_rows=1000, accepting_new_receipts=True),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])
        self.assertIn("headroom", check["error"])

    def test_a_full_store_fails_with_an_explicit_error(self):
        check = receipt_capacity_check(
            readyz(rows=1000, max_rows=1000, accepting_new_receipts=False),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])
        self.assertIn("507", check["error"])

    def test_the_tightest_configured_cap_decides(self):
        # Plenty of rows left, but the byte cap is nearly exhausted.
        check = receipt_capacity_check(
            readyz(
                rows=1,
                max_rows=1000,
                used_bytes=999_000,
                max_bytes=1_000_000,
                accepting_new_receipts=True,
            ),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])

    def test_an_uncapped_store_passes(self):
        check = receipt_capacity_check(
            readyz(rows=10_000, max_rows=None, used_bytes=5, max_bytes=None,
                   accepting_new_receipts=True),
            min_headroom_percent=10.0,
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])
        self.assertNotIn("rows_headroom_percent", check)

    def test_a_release_that_does_not_report_capacity_is_visible_not_silent(self):
        # A live host can legitimately lag the repo between merge and redeploy.
        # That must not fail the canary, but it must also not read as healthy.
        check = receipt_capacity_check({"ready": True}, min_headroom_percent=10.0)
        self.assertTrue(check["ok"])
        self.assertFalse(check["reported"])
        self.assertNotIn("rows_headroom_percent", check)

    def test_a_non_dict_readyz_payload_does_not_raise(self):
        check = receipt_capacity_check("service unavailable", min_headroom_percent=10.0)
        self.assertTrue(check["ok"])
        self.assertFalse(check["reported"])


class ReceiptWriteCheckTests(unittest.TestCase):
    def test_verified_transactional_probe_passes(self):
        check = receipt_write_check(
            {
                "ready": True,
                "receipt_store_write": {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "cached": False,
                },
            }
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])

    def test_missing_probe_is_visible_but_legacy_canary_compatible(self):
        check = receipt_write_check({"ready": True})
        self.assertTrue(check["ok"])
        self.assertFalse(check["required"])
        self.assertFalse(check["reported"])

    def test_missing_probe_fails_the_v6_release_smoke(self):
        check = receipt_write_check({"ready": True}, required=True)
        self.assertFalse(check["ok"])
        self.assertFalse(check["reported"])
        self.assertIn("does not report", check["error"])

    def test_ready_without_verified_rollback_fails(self):
        check = receipt_write_check(
            {
                "receipt_store_write": {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": False,
                }
            },
            required=True,
        )
        self.assertFalse(check["ok"])
        self.assertTrue(check["reported"])

    def test_reported_failed_probe_fails_even_in_legacy_canary_mode(self):
        check = receipt_write_check(
            {
                "receipt_store_write": {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "error": "write_unavailable",
                }
            }
        )
        self.assertFalse(check["ok"])
        self.assertFalse(check["required"])
        self.assertTrue(check["reported"])


class ForecastResponseCheckTests(unittest.TestCase):
    def test_nonempty_content_and_probability_pass(self):
        self.assertTrue(
            valid_forecast_response(
                {"content": "Rain is unlikely. Probability: 20%.", "probability": 0.2}
            )
        )

    def test_empty_or_invalid_answers_fail(self):
        for response in (
            {"content": "", "probability": 0.2},
            {"content": "   ", "probability": 0.2},
            {"content": "Rain is unlikely."},
            {"content": "Rain is unlikely.", "probability": True},
            {"content": "Rain is unlikely.", "probability": float("nan")},
            {"content": "Rain is unlikely.", "probability": 10**10000},
            {"content": "Rain is unlikely.", "probability": 1.1},
        ):
            with self.subTest(response=response):
                self.assertFalse(valid_forecast_response(response))


class TemperatureWindowResponseCheckTests(unittest.TestCase):
    def _response(self, *, hours=24):
        reference = "2026-08-17T11:00:00Z"
        times = [
            (datetime(2026, 8, 17, 12, tzinfo=timezone.utc) + timedelta(hours=index)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            for index in range(hours)
        ]
        return {
            "content": "Hourly 2 metre temperatures.",
            "reference_time": reference,
            "hourly": {"time": times, "2t": [298.15] * hours},
            "hourly_units": {"time": "iso8601", "2t": "K"},
        }

    def test_valid_twenty_four_hour_temperature_response_passes(self):
        self.assertTrue(valid_temperature_window_response(self._response()))

    def test_temperature_response_rejects_wrong_shape_units_and_spacing(self):
        response = self._response()
        for mutate in (
            lambda value: value["hourly"].pop("2t"),
            lambda value: value["hourly_units"].update({"2t": "C"}),
            lambda value: value["hourly"]["time"].__setitem__(1, value["hourly"]["time"][0]),
            lambda value: value["hourly"]["2t"].__setitem__(0, 0),
        ):
            candidate = json.loads(json.dumps(response))
            mutate(candidate)
            with self.subTest(candidate=candidate):
                self.assertFalse(valid_temperature_window_response(candidate))

    def test_temperature_response_requires_exact_count(self):
        self.assertFalse(valid_temperature_window_response(self._response(hours=23)))

    def test_temperature_response_can_be_pinned_to_the_request_hour(self):
        response = self._response()
        expected = datetime(2026, 8, 17, 11, tzinfo=timezone.utc)
        self.assertTrue(
            valid_temperature_window_response(
                response,
                expected_reference_times={expected},
            )
        )
        self.assertFalse(
            valid_temperature_window_response(
                response,
                expected_reference_times={expected - timedelta(hours=1)},
            )
        )

    def test_temperature_smoke_id_is_stable_only_within_one_utc_hour(self):
        first = datetime(2026, 8, 17, 11, 1, tzinfo=timezone.utc)
        same_hour = first + timedelta(minutes=58)
        next_hour = first + timedelta(hours=1)

        self.assertEqual(
            temperature_smoke_event_id(first),
            "smoke-temperature-20260817T11z",
        )
        self.assertEqual(
            temperature_smoke_event_id(first),
            temperature_smoke_event_id(same_hour),
        )
        self.assertNotEqual(
            temperature_smoke_event_id(first),
            temperature_smoke_event_id(next_hour),
        )


class RollingHorizonTests(unittest.TestCase):
    """The recurring canary must always pick a requestable horizon.

    A fixed date is squeezed between two independent failure modes: past the
    provider's rolling 7-day window (Open-Meteo -> provider_unavailable -> 502)
    and past its own forecast_cutoff (service.py:435 rejects when now >= cutoff).
    The rolling horizon must stay between both bounds at every clock time, and
    must stay stable within a UTC day so the 96 daily runs replay one receipt
    instead of writing 96.
    """

    def test_horizon_lands_at_noon_utc_tomorrow(self):
        now = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
        start, end, cutoff = rolling_horizon(now)
        self.assertEqual(format_timestamp(start), "2026-08-11T12:00:00Z")
        self.assertEqual(format_timestamp(end), "2026-08-11T13:00:00Z")
        self.assertEqual(format_timestamp(cutoff), "2026-08-11T11:00:00Z")

    def test_cutoff_stays_in_the_future_even_at_235959(self):
        # service.py:435 rejects a request issued at or after forecast_cutoff,
        # so the tightest time is the last instant of the UTC day.
        now = datetime(2026, 8, 10, 23, 59, 59, tzinfo=timezone.utc)
        _, _, cutoff = rolling_horizon(now)
        self.assertGreater(cutoff, now)
        self.assertLessEqual((cutoff - now).total_seconds(), 12 * 3600)

    def test_lead_stays_inside_the_provider_window_at_midnight(self):
        # The loosest time (just after UTC midnight) gives the largest lead.
        # Open-Meteo publishes a rolling 7 days; select_exact_point refuses to
        # substitute a neighbouring hour, so the horizon must stay inside it.
        now = datetime(2026, 8, 11, 0, 1, tzinfo=timezone.utc)
        start, end, cutoff = rolling_horizon(now)
        lead_hours = (start - now).total_seconds() / 3600
        self.assertLess(lead_hours, 7 * 24)
        self.assertGreater(lead_hours, 12)
        self.assertEqual(end - start, timedelta(hours=1))
        self.assertEqual(start - cutoff, timedelta(hours=1))

    def test_horizon_rolls_at_utc_midnight(self):
        late = rolling_horizon(datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc))
        early = rolling_horizon(datetime(2026, 8, 11, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(format_timestamp(late[0]), "2026-08-11T12:00:00Z")
        self.assertEqual(format_timestamp(early[0]), "2026-08-12T12:00:00Z")

    def test_stable_within_a_utc_day_replays_one_receipt(self):
        # The receipt hash derives from the canonical question (event_id is
        # excluded, service.py:603), so an identical horizon within a day must
        # not multiply rows in the receipt store.
        first = rolling_horizon(datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc))
        last = rolling_horizon(datetime(2026, 8, 10, 23, 55, tzinfo=timezone.utc))
        self.assertEqual(first, last)


class RegisteredRouteConfigTests(unittest.TestCase):
    def test_smoke_paths_match_the_service_aliases(self):
        self.assertEqual(REGISTERED_FORECAST_PATH, "/predict")
        self.assertEqual(CANONICAL_FORECAST_PATH, "/v1/forecast/point")
        self.assertIn(REGISTERED_FORECAST_PATH, FORECAST_PATHS)
        self.assertIn(CANONICAL_FORECAST_PATH, FORECAST_PATHS)
        self.assertNotIn("/v1/forecast/window", FORECAST_PATHS)

    def test_caddy_routes_the_registered_path_to_the_miner(self):
        matcher = next(
            line.strip()
            for line in (ROOT / "Caddyfile").read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("@miner path ")
        )
        self.assertIn(" /predict ", f" {matcher} ")


if __name__ == "__main__":
    unittest.main()
