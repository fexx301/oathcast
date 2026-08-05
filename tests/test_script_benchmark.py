import json
from pathlib import Path
import unittest

from oathcast.script_benchmark import (
    evaluate_robust_reference,
    load_script_benchmark_cases,
    run_script_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


class ScriptBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.fixture_sha256 = load_script_benchmark_cases(
            str(ROOT / "fixtures" / "script_author_adversarial.json")
        )
        self.report = run_script_benchmark(self.cases)

    def test_fixture_is_hashed_and_candidate_improves_behavior_accuracy(self):
        self.assertEqual(len(self.cases), 10)
        self.assertEqual(len(self.fixture_sha256), 64)
        summary = self.report["summary"]
        self.assertGreater(summary["candidate_behavior_accuracy"], summary["baseline_behavior_accuracy"])
        self.assertGreater(summary["behavior_accuracy_improvement"], 0)
        self.assertTrue(summary["score_bounds_ok"])

    def test_adversarial_cases_are_rejected_without_losing_good_cases(self):
        summary = self.report["summary"]
        self.assertEqual(summary["candidate_good_case_pass_rate"], 1.0)
        self.assertEqual(summary["candidate_adversarial_rejection_rate"], 1.0)
        self.assertEqual(summary["candidate_adversarial_accepts"], 0)
        self.assertGreater(summary["baseline_adversarial_accepts"], 0)

    def test_wrong_outcome_is_a_fatal_candidate_issue(self):
        case = next(case for case in self.cases if case.case_id == "bad_wrong_outcome")
        evaluation = evaluate_robust_reference(
            case.question,
            case.ground_truth,
            case.raw_response,
        )
        self.assertFalse(evaluation.valid)
        self.assertEqual(evaluation.score, 0.0)
        self.assertIn("polarity_mismatch", evaluation.issues)

    def test_report_is_json_serializable_and_marks_development_boundary(self):
        encoded = json.dumps(self.report, sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(
            decoded["official_status"],
            "development_only_not_telegraph_canonical_script",
        )
        self.assertEqual(
            decoded["scoring_lanes"]["brier"],
            "separate_domain_benchmark_not_included_here",
        )


if __name__ == "__main__":
    unittest.main()
