import unittest

from oathcast.scoring import BrierCase, brier_loss, brier_skill_score, evaluate_brier, score_attempt


class ScoringTests(unittest.TestCase):
    def test_brier_loss_is_squared_error(self):
        self.assertAlmostEqual(brier_loss(0.7, 1), 0.09)
        self.assertAlmostEqual(brier_loss(0.2, 0), 0.04)

    def test_skill_preserves_negative_values_and_handles_zero_baseline(self):
        self.assertAlmostEqual(brier_skill_score(0.04, 0.16), 0.75)
        self.assertAlmostEqual(brier_skill_score(0.20, 0.10), -1.0)
        self.assertIsNone(brier_skill_score(0.0, 0.0))

    def test_non_valid_responses_score_zero_and_reduce_coverage(self):
        cases = [
            BrierCase("valid", 0.8, 1, 0.5),
            BrierCase("late", 0.9, 1, 0.5, status="late"),
            BrierCase("missing", None, 0, 0.5, status="missing"),
            BrierCase("invalid", 1.2, 1, 0.5, status="invalid"),
            BrierCase("abstained", None, 0, 0.5, status="abstained"),
        ]
        summary = evaluate_brier(cases)
        self.assertEqual(summary.total_cases, 5)
        self.assertEqual(summary.valid_cases, 1)
        self.assertEqual(summary.coverage, 0.2)
        self.assertEqual(summary.invalid_cases_by_status["late"], 1)
        self.assertEqual(summary.invalid_cases_by_status["invalid"], 1)
        self.assertEqual(summary.invalid_cases_by_status["abstained"], 1)
        self.assertAlmostEqual(summary.end_to_end_score, (1 - 0.2**2) / 5)
        self.assertEqual(score_attempt(cases[1]), 0.0)

    def test_empty_evaluation_is_explicit(self):
        summary = evaluate_brier([])
        self.assertEqual(summary.total_cases, 0)
        self.assertEqual(summary.coverage, 0.0)
        self.assertIsNone(summary.brier_score)
        self.assertEqual(summary.end_to_end_score, 0.0)


if __name__ == "__main__":
    unittest.main()
