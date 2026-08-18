import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from oathcast import (
    backtest as backtest_module,
    leaderboard,
    reference_evaluator,
    render,
    scoring,
    script_benchmark,
)
from oathcast.application import (
    TelegraphMinerClient,
    extract_probability as application_probability,
)
from oathcast.backtest import (
    ChronologicalCase,
    ProviderForecast,
    load_chronological_cases,
    run_chronological_backtest,
)
from oathcast.demand import DemandLedger
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion
from oathcast.ground_truth import PrecipitationObservation, resolve_precipitation
from oathcast.miner_adapters import (
    GenericMinerAdapter,
    ZeusMinerAdapter,
    extract_generic_probability,
)
from oathcast.probability import extract_probability
from oathcast.protocol import ProtocolResultEnvelope
from oathcast.registration import MINIMUM_PRICE_MICRO_USDC, MinerRegistrationDeclaration


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


class CorrectnessSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="correctness-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=UTC),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=UTC),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

    def test_probability_consumers_share_one_extractor_and_semantics(self):
        self.assertIs(application_probability, extract_probability)
        self.assertIs(extract_generic_probability, extract_probability)
        self.assertIs(script_benchmark.extract_probability, extract_probability)

        self.assertIsNone(extract_probability({"probability": 70}))
        self.assertAlmostEqual(
            extract_probability({"precipitation_probability": 70}),
            0.7,
        )
        self.assertAlmostEqual(
            extract_probability({"choices": [{"message": {"content": "Rain: 42%"}}]}),
            0.42,
        )

    def test_backtest_rejects_malformed_valid_probabilities(self):
        for value in (None, -0.01, 1.01, float("nan"), True, "0.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProviderForecast(value)

        explicitly_invalid = ProviderForecast(1.2, status="invalid")
        self.assertEqual(explicitly_invalid.probability, 1.2)

    def test_chronological_case_rejects_outcome_coercion(self):
        raw_case = json.loads(
            (ROOT / "fixtures" / "brier_cases.json").read_text(encoding="utf-8")
        )[0]
        for value in (True, 0.9, "1"):
            malformed = copy.deepcopy(raw_case)
            malformed["outcome"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "outcome must be the integer 0 or 1 or None"
            ):
                ChronologicalCase.from_dict(malformed)

    def test_new_registration_generation_is_never_still_portal_validated(self):
        declaration = MinerRegistrationDeclaration(
            miner_slug="oathcast-weather",
            generation=1,
            supported_intents=("WEATHER_FORECAST",),
            min_price_micro_usdc=MINIMUM_PRICE_MICRO_USDC,
            yaml_sha256="a" * 64,
            confirmation_status="portal_validated",
        )

        next_declaration = declaration.next_generation(
            yaml_sha256="b" * 64,
            confirmation_status="portal_validated",
        )

        self.assertEqual(next_declaration.generation, 2)
        self.assertEqual(next_declaration.confirmation_status, "draft")

    def test_registration_rejects_unknown_intents_and_invalid_fee_addresses(self):
        base = {
            "miner_slug": "oathcast-weather",
            "generation": 1,
            "supported_intents": ("WEATHER_FORECAST",),
            "min_price_micro_usdc": MINIMUM_PRICE_MICRO_USDC,
            "yaml_sha256": "a" * 64,
        }
        invalid_changes = (
            {"supported_intents": ("WEATHER_FORCAST_TYPO",)},
            {"fee_address": "not-an-address"},
            {"fee_address": "0x" + ("0" * 40)},
            {"fee_address": 123},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                MinerRegistrationDeclaration(**(base | changes))

    def test_unresolved_case_preserves_the_provider_attempt_status(self):
        raw_case = json.loads(
            (ROOT / "fixtures" / "brier_cases.json").read_text(encoding="utf-8")
        )[0]
        unresolved = replace(
            ChronologicalCase.from_dict(raw_case),
            outcome=None,
            resolved_at=None,
        )

        attempt = unresolved.to_brier_case("open_meteo")

        self.assertEqual(attempt.status, "valid")
        self.assertFalse(attempt.is_valid)

    def test_provider_selection_is_computed_once_per_timestamp_batch(self):
        cases, _ = load_chronological_cases(ROOT / "fixtures" / "brier_cases.json")
        first = replace(cases[0], case_id="dev-000")
        second = replace(cases[1], issued_at=first.issued_at)
        batched_cases = [first, second, *cases[2:]]

        with patch.object(
            backtest_module,
            "_choose_provider",
            wraps=backtest_module._choose_provider,
        ) as choose_provider:
            run_chronological_backtest(
                batched_cases,
                warmup_cases=4,
                min_history_valid_cases=2,
            )

        self.assertEqual(
            choose_provider.call_count,
            len({case.issued_at for case in batched_cases}),
        )

    def test_telegraph_client_builds_one_protocol_envelope(self):
        capability = MinerCapability(
            "999",
            "envelope-test",
            "Envelope Test",
            "https://dispatcher.example",
            frozenset({"WEATHER_FORECAST"}),
            endpoint_name="forecast",
        )

        class Response:
            status = 200
            settlement_verification = "verified"
            body = {"probability": 0.4}

        class PaymentClient:
            def request_miner(self, miner_id, endpoint, params):
                return Response()

        ledger = DemandLedger(":memory:")
        with patch.object(
            ProtocolResultEnvelope,
            "from_payment_response",
            wraps=ProtocolResultEnvelope.from_payment_response,
        ) as build_envelope:
            result = TelegraphMinerClient(
                capability,
                PaymentClient(),
                demand_ledger=ledger,
            )(self.question)

        self.assertEqual(result["probability"], 0.4)
        self.assertEqual(build_envelope.call_count, 1)

    def test_zeus_rejects_unsupported_horizon_and_accepts_percent_probability(self):
        too_long = replace(
            self.question,
            forecast_cutoff=self.question.forecast_cutoff - timedelta(hours=21),
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 24"):
            ZeusMinerAdapter().build_params(too_long)

        result = ZeusMinerAdapter().parse_response(
            {
                "hourly": {
                    "time": ["2026-08-17T15:00:00Z"],
                    "precipitation_probability": [70],
                }
            },
            self.question,
        )
        self.assertTrue(result.probability_comparable)
        self.assertAlmostEqual(result.probability, 0.7)

    def test_zeus_candidate_precedence_does_not_depend_on_json_key_order(self):
        target = ["2026-08-17T15:00:00Z"]
        first = {
            "hourly": {
                "timestamp": ["2026-08-17T14:00:00Z"],
                "chance_of_rain": [20],
                "precipitation_probability": [70],
                "time": target,
            }
        }
        second = {
            "hourly": {
                "time": target,
                "precipitation_probability": [70],
                "chance_of_rain": [20],
                "timestamp": ["2026-08-17T14:00:00Z"],
            }
        }

        left = ZeusMinerAdapter().parse_response(first, self.question)
        right = ZeusMinerAdapter().parse_response(second, self.question)

        self.assertAlmostEqual(left.probability, 0.7)
        self.assertAlmostEqual(right.probability, 0.7)

    def test_ground_truth_uses_the_question_threshold(self):
        question = replace(self.question)
        object.__setattr__(question, "threshold_mm", 0.2)

        def observation(precipitation_mm: float) -> PrecipitationObservation:
            return PrecipitationObservation(
                event_id=question.event_id,
                latitude=question.latitude,
                longitude=question.longitude,
                window_start=question.horizon_start,
                window_end=question.horizon_end,
                precipitation_mm=precipitation_mm,
                source="threshold-test",
                observation_id=f"obs-{precipitation_mm}",
                observed_at=datetime(2026, 8, 17, 17, tzinfo=UTC),
            )

        below = resolve_precipitation(
            question,
            observation(0.15),
            resolved_at=datetime(2026, 8, 17, 17, tzinfo=UTC),
        )
        above = resolve_precipitation(
            question,
            observation(0.201),
            resolved_at=datetime(2026, 8, 17, 17, tzinfo=UTC),
        )
        self.assertEqual(below.outcome, 0)
        self.assertEqual(above.outcome, 1)

    def test_scorer_rationale_is_explicitly_unverified(self):
        for module in (render, scoring, leaderboard, reference_evaluator):
            with self.subTest(module=module.__name__):
                doc = " ".join((module.__doc__ or "").lower().split())
                self.assertIn("pre-launch", doc)
                self.assertIn("not been verified", doc)

    def test_benchmark_lanes_share_text_contract_and_bound_fixture_expansion(self):
        self.assertIs(
            script_benchmark.TOKEN_PATTERN,
            reference_evaluator.TOKEN_PATTERN,
        )
        self.assertEqual(
            script_benchmark.DEFAULT_MAX_RESPONSE_CHARS,
            reference_evaluator.DEFAULT_MAX_RESPONSE_CHARS,
        )
        with self.assertRaisesRegex(ValueError, "expanded response exceeds"):
            script_benchmark.ScriptBenchmarkCase.from_dict(
                {
                    "case_id": "oversized-repeat",
                    "question": "Will it rain?",
                    "ground_truth": "No.",
                    "case_class": "invalid",
                    "raw_response": {
                        "_fixture_repeat": {
                            "value": "x" * 1024,
                            "count": 1025,
                        }
                    },
                }
            )

    def test_dead_generic_url_surface_is_not_part_of_the_adapter(self):
        self.assertFalse(hasattr(GenericMinerAdapter(), "build_url"))


if __name__ == "__main__":
    unittest.main()
