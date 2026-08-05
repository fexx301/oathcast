import json
from pathlib import Path
import unittest

from oathcast.reference_evaluator import evaluate_reference, normalize_response


ROOT = Path(__file__).resolve().parents[1]


class ReferenceEvaluatorTests(unittest.TestCase):
    def test_structured_and_chat_shapes_normalize_to_text(self):
        cases = json.loads((ROOT / "fixtures" / "evaluation_cases.json").read_text())
        structured = evaluate_reference(
            cases[0]["question"], cases[0]["ground_truth"], cases[0]["raw_response"]
        )
        chat = evaluate_reference(
            cases[1]["question"], cases[1]["ground_truth"], cases[1]["raw_response"]
        )
        self.assertTrue(structured.valid)
        self.assertTrue(chat.valid)
        self.assertIn("70%", structured.response_text)
        self.assertIn("20%", chat.response_text)
        self.assertGreaterEqual(structured.score, 0)
        self.assertLessEqual(structured.score, 1)
        self.assertEqual(structured.algorithm, "development_proxy_not_telegraph_scorer")

    def test_empty_and_excessive_responses_score_zero(self):
        empty = evaluate_reference("question", "truth", "")
        excessive = evaluate_reference("question", "truth", "x" * 5000)
        self.assertFalse(empty.valid)
        self.assertEqual(empty.score, 0.0)
        self.assertIn("empty_response", empty.issues)
        self.assertFalse(excessive.valid)
        self.assertEqual(excessive.score, 0.0)
        self.assertIn("response_too_long", excessive.issues)

    def test_plain_text_is_preserved_after_whitespace_normalization(self):
        self.assertEqual(normalize_response("  rainy\n  Lagos  "), "rainy Lagos")


if __name__ == "__main__":
    unittest.main()
