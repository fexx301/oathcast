from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import unittest

from oathcast.backtest import (
    load_chronological_cases,
    run_chronological_backtest,
)


ROOT = Path(__file__).resolve().parents[1]


class ChronologicalBacktestTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.fixture_sha256 = load_chronological_cases(
            str(ROOT / "fixtures" / "brier_cases.json")
        )
        self.report = run_chronological_backtest(
            self.cases,
            warmup_cases=4,
            min_history_valid_cases=2,
        )

    def test_fixture_has_temporal_split_and_common_case_metrics(self):
        self.assertEqual(len(self.cases), 10)
        self.assertEqual(len(self.fixture_sha256), 64)
        self.assertEqual(self.report["dataset"]["holdout_cases"], 6)
        self.assertEqual(self.report["dataset"]["resolved_holdout_cases"], 6)
        self.assertEqual(
            self.report["provider_summaries"]["open_meteo"]["holdout"][
                "common_case_count"
            ],
            4,
        )
        self.assertAlmostEqual(
            self.report["provider_summaries"]["open_meteo"]["holdout"]["brier_score"],
            0.06106,
        )
        self.assertEqual(
            self.report["official_status"],
            "development_only_synthetic_not_live_provider_performance",
        )

    def test_delayed_resolution_is_not_used_before_it_is_available(self):
        trace = next(
            row for row in self.report["selection_trace"] if row["case_id"] == "dev-004"
        )
        self.assertEqual(trace["history_cases_before_decision"], 2)
        self.assertNotIn("dev-003", trace["prior_profiles"])
        self.assertTrue(
            self.report["no_leakage_checks"][
                "provider_selection_uses_prior_resolved_cases_only"
            ]
        )

    def test_current_outcome_cannot_change_current_selection(self):
        changed_last_case = replace(self.cases[-1], outcome=1)
        changed_report = run_chronological_backtest(
            [*self.cases[:-1], changed_last_case],
            warmup_cases=4,
            min_history_valid_cases=2,
        )
        original_trace = self.report["selection_trace"][-1]
        changed_trace = changed_report["selection_trace"][-1]
        self.assertEqual(
            original_trace["selected_provider"], changed_trace["selected_provider"]
        )
        self.assertEqual(original_trace["prior_profiles"], changed_trace["prior_profiles"])

    def test_unresolved_case_is_excluded_from_scoring_and_history(self):
        unresolved = replace(self.cases[0], outcome=None, resolved_at=None)
        report = run_chronological_backtest(
            [unresolved, *self.cases[1:]],
            warmup_cases=4,
            min_history_valid_cases=2,
        )
        self.assertEqual(report["dataset"]["unresolved_cases"], 1)
        self.assertEqual(report["dataset"]["resolved_cases"], 9)
        second_trace = next(
            row for row in report["selection_trace"] if row["case_id"] == "dev-002"
        )
        self.assertEqual(second_trace["history_cases_before_decision"], 0)

    def test_equal_issue_times_are_batched(self):
        first = replace(self.cases[0], case_id="dev-000")
        second = replace(self.cases[1], issued_at=first.issued_at)
        report = run_chronological_backtest(
            [first, second, *self.cases[2:]],
            warmup_cases=4,
            min_history_valid_cases=2,
        )
        first_trace, second_trace = report["selection_trace"][:2]
        self.assertEqual(first_trace["history_cases_before_decision"], 0)
        self.assertEqual(second_trace["history_cases_before_decision"], 0)
        self.assertEqual(first_trace["simultaneous_timestamp_batch_size"], 2)

    def test_unsorted_or_mixed_horizon_input_is_rejected(self):
        with self.assertRaises(ValueError):
            run_chronological_backtest(list(reversed(self.cases)))
        with self.assertRaises(ValueError):
            run_chronological_backtest(
                [replace(self.cases[0], horizon_end=self.cases[0].horizon_end + timedelta(hours=1)), *self.cases[1:]]
            )


if __name__ == "__main__":
    unittest.main()
