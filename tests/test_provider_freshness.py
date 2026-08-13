import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

from oathcast.backtest import ChronologicalCase
from oathcast.forecast import format_timestamp
from scripts.check_provider_freshness import (
    FreshnessCheckError,
    _duration_from_hours,
    build_report,
    check_collection,
    check_resolution,
    main,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 12, 18, tzinfo=UTC)


def make_case(
    case_id: str,
    issued_at: datetime,
    *,
    outcome: int | None = None,
    resolved_at: datetime | None = None,
) -> ChronologicalCase:
    horizon_start = issued_at + timedelta(hours=3)
    return ChronologicalCase.from_dict(
        {
            "case_id": case_id,
            "issued_at": format_timestamp(issued_at),
            "forecast_cutoff": format_timestamp(issued_at),
            "horizon_start": format_timestamp(horizon_start),
            "horizon_end": format_timestamp(horizon_start + timedelta(hours=1)),
            "outcome": outcome,
            "resolved_at": (
                None if resolved_at is None else format_timestamp(resolved_at)
            ),
            "climatology_probability": 0.23,
            "forecasts": {
                "open_meteo": {"probability": 0.2, "status": "valid"},
                "weatherapi": {"probability": 0.3, "status": "valid"},
            },
        }
    )


def case_payload(case: ChronologicalCase) -> dict:
    return {
        "case_id": case.case_id,
        "issued_at": format_timestamp(case.issued_at),
        "forecast_cutoff": format_timestamp(case.forecast_cutoff),
        "horizon_start": format_timestamp(case.horizon_start),
        "horizon_end": format_timestamp(case.horizon_end),
        "outcome": case.outcome,
        "resolved_at": (
            None if case.resolved_at is None else format_timestamp(case.resolved_at)
        ),
        "climatology_probability": case.climatology_probability,
        "forecasts": {
            provider: {
                "probability": forecast.probability,
                "status": forecast.status,
            }
            for provider, forecast in case.forecasts.items()
        },
    }


class CollectionFreshnessTests(unittest.TestCase):
    def test_latest_issue_at_the_age_limit_is_fresh(self):
        cases = [
            make_case("older", NOW - timedelta(hours=9)),
            make_case("boundary", NOW - timedelta(hours=6)),
        ]
        status = check_collection(cases, now=NOW, max_age=timedelta(hours=6))
        self.assertTrue(status.fresh)
        self.assertEqual(status.latest_issued_at, "2026-08-12T12:00:00Z")
        self.assertEqual(status.age_hours, 6.0)

    def test_latest_issue_beyond_the_age_limit_is_stale(self):
        status = check_collection(
            [make_case("late", NOW - timedelta(hours=6, seconds=1))],
            now=NOW,
            max_age=timedelta(hours=6),
        )
        self.assertFalse(status.fresh)
        self.assertEqual(status.reason, "latest_issued_at_exceeds_max_age")

    def test_future_issued_data_cannot_mask_a_stalled_collector(self):
        status = check_collection(
            [make_case("future", NOW + timedelta(minutes=1))],
            now=NOW,
            max_age=timedelta(hours=6),
        )
        self.assertFalse(status.fresh)
        self.assertEqual(status.reason, "latest_issued_at_is_in_the_future")


class ResolutionFreshnessTests(unittest.TestCase):
    def test_an_unresolved_case_at_the_grace_boundary_is_not_overdue(self):
        # make_case closes four hours after issue, so this horizon ended exactly
        # 48 hours before NOW.
        case = make_case("boundary", NOW - timedelta(hours=52))
        status = check_resolution(
            [case], now=NOW, max_age=timedelta(hours=48)
        )
        self.assertTrue(status.fresh)
        self.assertEqual(status.unresolved_count, 1)
        self.assertEqual(status.overdue_count, 0)

    def test_overdue_is_measured_from_horizon_end_not_issue_time(self):
        case = make_case("overdue", NOW - timedelta(hours=52, minutes=1))
        status = check_resolution(
            [case], now=NOW, max_age=timedelta(hours=48)
        )
        self.assertFalse(status.fresh)
        self.assertEqual(status.overdue_count, 1)
        self.assertEqual(status.oldest_overdue_case["case_id"], "overdue")
        self.assertGreater(status.oldest_overdue_case["age_hours"], 48.0)

    def test_resolved_cases_are_ignored_even_when_old(self):
        case = make_case(
            "resolved",
            NOW - timedelta(days=30),
            outcome=1,
            resolved_at=NOW - timedelta(days=29),
        )
        status = check_resolution(
            [case], now=NOW, max_age=timedelta(hours=48)
        )
        self.assertTrue(status.fresh)
        self.assertEqual(status.unresolved_count, 0)

    def test_oldest_overdue_case_is_reported_deterministically(self):
        cases = [
            make_case("later", NOW - timedelta(days=4)),
            make_case("oldest-b", NOW - timedelta(days=5)),
            make_case("oldest-a", NOW - timedelta(days=5)),
        ]
        status = check_resolution(
            cases, now=NOW, max_age=timedelta(hours=48)
        )
        self.assertEqual(status.overdue_count, 3)
        self.assertEqual(status.oldest_overdue_case["case_id"], "oldest-a")


class IndependentCheckTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.dataset = Path(self._directory.name) / "paired-forecasts.json"

    def write_cases(self, cases: list[ChronologicalCase]) -> None:
        ordered = sorted(cases, key=lambda case: (case.issued_at, case.case_id))
        self.dataset.write_text(
            json.dumps([case_payload(case) for case in ordered]),
            encoding="utf-8",
        )

    def test_resolution_job_can_pass_while_collection_is_stale(self):
        self.write_cases([make_case("stale-collection", NOW - timedelta(hours=10))])
        report = build_report(
            self.dataset,
            now=NOW,
            max_collection_age=timedelta(hours=6),
            max_resolution_age=timedelta(hours=48),
            selected_checks=("resolution",),
        )
        self.assertFalse(report["collection"]["fresh"])
        self.assertTrue(report["resolution"]["fresh"])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["alerts"], [])

    def test_collection_job_can_pass_while_resolution_is_overdue(self):
        self.write_cases(
            [
                make_case("old-unresolved", NOW - timedelta(days=5)),
                make_case("fresh-collection", NOW - timedelta(hours=1)),
            ]
        )
        report = build_report(
            self.dataset,
            now=NOW,
            max_collection_age=timedelta(hours=6),
            max_resolution_age=timedelta(hours=48),
            selected_checks=("collection",),
        )
        self.assertTrue(report["collection"]["fresh"])
        self.assertFalse(report["resolution"]["fresh"])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["alerts"], [])

    def test_cli_exit_codes_distinguish_alerts_from_monitor_errors(self):
        self.write_cases([make_case("stale", NOW - timedelta(hours=10))])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            alert_status = main(
                [
                    "--dataset",
                    str(self.dataset),
                    "--check",
                    "collection",
                    "--now",
                    format_timestamp(NOW),
                ]
            )
        self.assertEqual(alert_status, 1)
        self.assertEqual(json.loads(output.getvalue())["alerts"], ["collection_stale"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            error_status = main(
                ["--dataset", str(self.dataset.with_name("missing.json"))]
            )
        self.assertEqual(error_status, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_empty_check_selection_and_invalid_threshold_are_rejected(self):
        self.write_cases([make_case("case", NOW - timedelta(hours=1))])
        with self.assertRaises(FreshnessCheckError):
            build_report(
                self.dataset,
                now=NOW,
                max_collection_age=timedelta(hours=6),
                max_resolution_age=timedelta(hours=48),
                selected_checks=(),
            )
        for value in (-1, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(FreshnessCheckError):
                    _duration_from_hours(value, field_name="threshold")


if __name__ == "__main__":
    unittest.main()
