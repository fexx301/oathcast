import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "wasm_scoring_cases.json"
EVIDENCE = ROOT / "scoring-modules" / "oathcast-weather" / "release-evidence.json"


class WasmScoringFixtureTests(unittest.TestCase):
    def setUp(self):
        self.raw = FIXTURE.read_bytes()
        self.fixture = json.loads(self.raw.decode("utf-8"))
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.cases = self.fixture["cases"]

    def test_fixture_is_an_explicit_three_string_abi_corpus(self):
        self.assertEqual(
            self.fixture["abi"],
            "rank_answer(question_utf8, ground_truth_utf8, miner_answer_utf8) -> f32",
        )
        self.assertGreaterEqual(len(self.cases), 27)
        self.assertEqual(len(hashlib.sha256(self.raw).hexdigest()), 64)
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                self.assertIsInstance(case["question"], str)
                self.assertIsInstance(case["ground_truth"], str)
                self.assertNotIn("raw_response", case)
                self.assertTrue(
                    isinstance(case.get("miner_answer"), str)
                    ^ isinstance(case.get("miner_answer_repeat"), dict)
                )

    def test_fixture_digest_matches_release_evidence(self):
        fixture_evidence = self.evidence["fixture"]
        self.assertEqual(fixture_evidence["path"], "fixtures/wasm_scoring_cases.json")
        self.assertEqual(
            hashlib.sha256(self.raw).hexdigest(), fixture_evidence["sha256"]
        )

    def test_fatal_cases_pin_exact_zero_and_ordering_groups_are_well_formed(self):
        ordering_groups: dict[str, list[int]] = {}
        for case in self.cases:
            expected = case["expected_score"]
            self.assertTrue(
                set(expected) == {"exact"} or set(expected) == {"min", "max"}
            )
            if "exact" in expected:
                self.assertEqual(expected["exact"], 0.0)
            else:
                self.assertGreaterEqual(expected["min"], 0.0)
                self.assertLessEqual(expected["max"], 1.0)
                self.assertLessEqual(expected["min"], expected["max"])
            if "ordering_group" in case:
                ordering_groups.setdefault(case["ordering_group"], []).append(
                    case["quality_rank"]
                )

        self.assertTrue(ordering_groups)
        for group, ranks in ordering_groups.items():
            with self.subTest(group=group):
                self.assertGreaterEqual(len(set(ranks)), 2)


if __name__ == "__main__":
    unittest.main()
