import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "wasm_scoring_cases.json"
EVIDENCE = ROOT / "scoring-modules" / "oathcast-weather" / "release-evidence.json"
REQUIRED_FACTUAL_CATEGORIES = {
    "acronym_case_binding",
    "acronym_abbreviation",
    "acronym_repetition",
    "ambiguous_correct_wrong_anchors",
    "benign_both",
    "benign_negation",
    "clause_aware_negation",
    "conjunctive_ambiguity",
    "contrast_relation_binding",
    "entity_name_recombination",
    "factual_wording_collision",
    "negative_factual_truth",
    "negated_correct_anchor",
    "numeric_non_regression",
    "partial_multiword_entity",
    "relation_entity_binding",
    "repeated_anchor_stuffing",
    "unrelated_shared_token",
    "verbose_truth_terse_answer",
    "weather_lexeme_collision",
    "weather_non_regression",
    "heldout_weather_lexeme_collision",
    "heldout_acronym_inference",
    "heldout_anchor_refutation",
    "heldout_directed_relation_binding",
    "heldout_directed_relation_ellipsis",
    "heldout_lowercase_context_binding",
    "heldout_name_alias_binding",
    "heldout_open_question_binding",
    "heldout_predicate_family_binding",
    "heldout_pre_anchor_refutation",
    "heldout_punctuation_binding",
    "heldout_source_attribution",
    "heldout_weather_context_omission",
}


class WasmScoringFixtureTests(unittest.TestCase):
    def setUp(self):
        self.raw = FIXTURE.read_bytes()
        self.fixture = json.loads(self.raw.decode("utf-8"))
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.cases = self.fixture["cases"]
        self.factual_pairs = self.fixture["factual_pairs"]

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

    def test_synthetic_ordinal_factual_pairs_cover_adversarial_categories(self):
        self.assertEqual(
            len(self.factual_pairs),
            self.evidence["verification"]["synthetic_factual_pair_count"],
        )
        self.assertEqual(
            self.fixture["factual_pair_policy"]["status"],
            "synthetic ordinal development proxy, not Telegraph validator fixtures",
        )
        self.assertEqual(
            self.fixture["factual_pair_policy"]["reported_minimum_margin"], 0.15
        )
        self.assertGreaterEqual(
            self.fixture["factual_pair_policy"]["default_local_minimum_margin"],
            self.fixture["factual_pair_policy"]["reported_minimum_margin"],
        )
        pair_ids = set()
        splits = set()
        categories = set()
        for pair in self.factual_pairs:
            with self.subTest(pair_id=pair["pair_id"]):
                self.assertNotIn(pair["pair_id"], pair_ids)
                pair_ids.add(pair["pair_id"])
                self.assertIn(
                    pair["split"], {"development", "secondary_synthetic"}
                )
                splits.add(pair["split"])
                category = pair.get("category")
                if category is not None:
                    self.assertIsInstance(category, str)
                    self.assertTrue(category.strip())
                    categories.add(category)
                for field in (
                    "question",
                    "ground_truth",
                    "good_answer",
                    "bad_answer",
                ):
                    self.assertIsInstance(pair[field], str)
                    self.assertTrue(pair[field].strip())
                self.assertIsInstance(pair["minimum_margin"], (int, float))
                self.assertNotIsInstance(pair["minimum_margin"], bool)
                self.assertGreaterEqual(pair["minimum_margin"], 0.15)
                maximum_bad_score = pair.get("maximum_bad_score")
                if maximum_bad_score is not None:
                    self.assertIsInstance(maximum_bad_score, (int, float))
                    self.assertNotIsInstance(maximum_bad_score, bool)
                    self.assertGreaterEqual(maximum_bad_score, 0.0)
                    self.assertLessEqual(maximum_bad_score, 1.0)
                pre_tuning_fields = {
                    "pre_tuning_good_score",
                    "pre_tuning_bad_score",
                    "pre_tuning_margin",
                    "pre_tuning_score_precision",
                }
                present_pre_tuning_fields = pre_tuning_fields.intersection(pair)
                self.assertIn(
                    len(present_pre_tuning_fields),
                    {0, len(pre_tuning_fields)},
                    "pre-tuning scores must be recorded as one complete tuple",
                )
                if present_pre_tuning_fields:
                    for field in pre_tuning_fields:
                        self.assertIsInstance(pair[field], (int, float))
                        self.assertNotIsInstance(pair[field], bool)
                    self.assertGreaterEqual(pair["pre_tuning_good_score"], 0.0)
                    self.assertLessEqual(pair["pre_tuning_good_score"], 1.0)
                    self.assertGreaterEqual(pair["pre_tuning_bad_score"], 0.0)
                    self.assertLessEqual(pair["pre_tuning_bad_score"], 1.0)
                    self.assertEqual(pair["pre_tuning_score_precision"], 4)
                    self.assertAlmostEqual(
                        pair["pre_tuning_good_score"]
                        - pair["pre_tuning_bad_score"],
                        pair["pre_tuning_margin"],
                        places=4,
                    )

        self.assertEqual(splits, {"development", "secondary_synthetic"})
        self.assertTrue(REQUIRED_FACTUAL_CATEGORIES.issubset(categories))


if __name__ == "__main__":
    unittest.main()
