from pathlib import Path
import tempfile
import unittest

from oathcast.registration import (
    BASE_SEPOLIA_NETWORK,
    MINIMUM_PRICE_MICRO_USDC,
    MinerRegistrationDeclaration,
    decimal_usdc_to_micro,
)
from scripts.create_registration_draft import (
    BASE_SEPOLIA_CHAIN_ID,
    MINER_REGISTRY_DIAMOND,
    REGISTER_MINER_SIGNATURE,
    build_registration_draft,
)
from scripts.read_leaderboard import DECLARED_INTENTS
from scripts.validate_miner_drafts import validate_draft


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MINER = ROOT / "miners" / "oathcast-weather.yaml"


class RegistrationDeclarationTests(unittest.TestCase):
    def _mutated_canonical(self, old: str, new: str) -> dict[str, object]:
        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        self.assertIn(old, yaml_text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "miner.yaml"
            path.write_text(yaml_text.replace(old, new, 1), encoding="utf-8")
            return validate_draft(path, canonical=True)

    def test_canonical_yaml_matches_service_contract_and_is_not_portal_validated(self):
        result = validate_draft(CANONICAL_MINER, canonical=True)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["validation_scope"], "draft_local")
        self.assertEqual(result["official_portal_validation"]["status"], "not_run")
        self.assertFalse(result["official_portal_validation"]["validated"])
        self.assertFalse(result["official_portal_validated"])
        self.assertGreater(int(result["id"]), 0)
        self.assertTrue(any("routing candidate" in warning for warning in result["warnings"]))

        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        for fragment in (
            "input_schema:\n",
            "output_schema:\n",
            "required:\n    - lat\n    - lon\n    - start\n    - end\n",
            "intents: [WEATHER_FORECAST]",
            "params:\n      query:\n        required:\n",
            "label_field: content",
            "confidence_field: probability",
            "reason_field: content",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, yaml_text)
        self.assertNotIn("WEATHER_CHECK", yaml_text)
        self.assertNotIn("on_chain:", yaml_text)
        self.assertEqual(DECLARED_INTENTS, ("WEATHER_FORECAST",))

    def test_canonical_local_validation_rejects_missing_schema(self):
        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "miner.yaml"
            path.write_text(yaml_text.replace("input_schema:\n", "# input_schema:\n", 1), encoding="utf-8")
            result = validate_draft(path, canonical=True)

        self.assertFalse(result["valid"])
        self.assertTrue(any("input_schema" in error for error in result["errors"]))
        self.assertEqual(result["official_portal_validation"]["status"], "not_run")

    def test_canonical_requires_positive_numeric_candidate_id(self):
        result = self._mutated_canonical("id: 64173", "id: 0")
        self.assertFalse(result["valid"])
        self.assertIn(
            "canonical id must be a positive numeric routing ID", result["errors"]
        )

        result = self._mutated_canonical("id: 64173", "id: candidate")
        self.assertFalse(result["valid"])
        self.assertIn("id must be numeric", result["errors"])

    def test_canonical_requires_exact_forecast_semantics_and_signal_mapping(self):
        result = self._mutated_canonical(
            "    - WEATHER_FORECAST\n", "    - WEATHER_FORECAST\n    - WEATHER_CHECK\n"
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("supported_intents" in error for error in result["errors"]))

        result = self._mutated_canonical("    label_field: content\n", "")
        self.assertFalse(result["valid"])
        self.assertTrue(any("signal_mapping" in error for error in result["errors"]))

    def test_canonical_requires_endpoint_intents_and_exact_query_contract(self):
        result = self._mutated_canonical("    intents: [WEATHER_FORECAST]\n", "")
        self.assertFalse(result["valid"])
        self.assertTrue(any("endpoint intents" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "          - name: lat\n", "          - name: latitude\n"
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("required query params" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "          - name: cutoff\n", "          - name: deadline\n"
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("optional query params" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "            intents: [WEATHER_FORECAST]\n",
            "            intents: [WEATHER_CHECK]\n",
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("query param lat intents" in error for error in result["errors"]))

        result = self._mutated_canonical("    params:\n", "    arguments:\n")
        self.assertFalse(result["valid"])
        self.assertTrue(any("params mapping" in error for error in result["errors"]))

        result = self._mutated_canonical("      query:\n", "      inputs:\n")
        self.assertFalse(result["valid"])
        self.assertTrue(any("query mapping" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "        optional:\n", "        optional:\n        optional:\n"
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("optional group" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "          - name: lon\n", "          - name: lat\n"
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("required query params" in error for error in result["errors"]))

    def test_canonical_signal_mapping_rejects_extra_and_duplicate_keys(self):
        result = self._mutated_canonical(
            "    reason_field: content\n",
            "    reason_field: content\n    extra_field: content\n",
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("signal_mapping" in error for error in result["errors"]))

        result = self._mutated_canonical(
            "    reason_field: content\n",
            "    reason_field: content\n    reason_field: content\n",
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("signal_mapping" in error for error in result["errors"]))

    def test_registration_draft_uses_live_contract_inputs_without_on_chain_yaml(self):
        artifact = build_registration_draft(CANONICAL_MINER)
        registration = artifact["registration"]
        call = artifact["registration_call"]
        self.assertEqual(artifact["artifact_version"], 2)
        self.assertEqual(registration["miner_id"], "64173")
        self.assertEqual(registration["supported_intents"], ["WEATHER_FORECAST"])
        self.assertEqual(registration["min_price_micro_usdc"], 10_000)
        self.assertIsNone(registration["output_mapping_sha256"])
        self.assertEqual(call["chain_id"], BASE_SEPOLIA_CHAIN_ID)
        self.assertEqual(call["contract"], MINER_REGISTRY_DIAMOND)
        self.assertEqual(call["signature"], REGISTER_MINER_SIGNATURE)
        self.assertTrue(call["arguments"]["yaml_hash_bytes32"].startswith("0x"))
        self.assertEqual(len(call["arguments"]["yaml_hash_bytes32"]), 66)
        self.assertFalse(call["ready_to_encode"])
        self.assertEqual(
            artifact["official_registration"]["portal_validation_status"], "not_run"
        )
        self.assertEqual(
            artifact["official_registration"]["status_scope"],
            "local_generator_only",
        )
        self.assertTrue(
            any(
                "Base Sepolia ETH" in item
                for item in artifact["official_registration"]["pending"]
            )
        )
        self.assertIn(
            "separate registration-readiness manifest",
            artifact["official_registration"]["status_scope_note"],
        )

    def test_local_validator_pending_text_distinguishes_slug_from_routing_id(self):
        result = validate_draft(CANONICAL_MINER, canonical=True)
        pending = result["official_portal_validation"]["pending"]
        self.assertTrue(any("slug availability" in item for item in pending))
        self.assertTrue(any("not the on-chain registrationId" in item for item in pending))
        self.assertFalse(any("ID and slug are unique" in item for item in pending))

    def test_registration_draft_validates_operator_supplied_inputs(self):
        artifact = build_registration_draft(
            CANONICAL_MINER,
            yaml_uri="ipfs://bafy-draft",
            fee_address="0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(artifact["registration"]["yaml_uri"], "ipfs://bafy-draft")
        self.assertEqual(
            artifact["registration"]["fee_address"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertFalse(artifact["registration_call"]["ready_to_encode"])
        self.assertEqual(
            artifact["registration_input_sources"]["yaml_uri"],
            "explicit --yaml-uri operator/portal input",
        )
        with self.assertRaises(ValueError):
            build_registration_draft(CANONICAL_MINER, min_price_micro_usdc=9_999)
        with self.assertRaises(ValueError):
            build_registration_draft(CANONICAL_MINER, yaml_uri="file:///tmp/miner.yaml")
        with self.assertRaises(ValueError):
            build_registration_draft(CANONICAL_MINER, fee_address="0x" + ("0" * 40))

    def test_registration_draft_refuses_locally_invalid_canonical_yaml(self):
        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "miner.yaml"
            path.write_text(
                yaml_text.replace("    label_field: content\n", "", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "failed local validation"):
                build_registration_draft(path)

    def test_decimal_price_conversion_is_explicit(self):
        self.assertEqual(decimal_usdc_to_micro("0.01"), MINIMUM_PRICE_MICRO_USDC)
        with self.assertRaises(ValueError):
            decimal_usdc_to_micro("0.0000001")

    def test_yaml_digest_and_output_mapping_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "miner.yaml"
            path.write_text("version: '1'\nslug: oathcast-weather\n", encoding="utf-8")
            declaration = MinerRegistrationDeclaration.from_yaml(
                path,
                miner_slug="oathcast-weather",
                supported_intents=("WEATHER_FORECAST",),
                min_price_micro_usdc=MINIMUM_PRICE_MICRO_USDC,
                yaml_uri="ipfs://draft",
                output_mapping={"strings": [{"index": 0, "source_path": "content"}]},
            )
        self.assertEqual(declaration.chain, BASE_SEPOLIA_NETWORK)
        self.assertEqual(declaration.confirmation_status, "draft")
        self.assertEqual(len(declaration.yaml_sha256), 64)
        self.assertEqual(len(declaration.output_mapping_sha256), 64)

    def test_submitted_generation_is_not_mutated(self):
        declaration = MinerRegistrationDeclaration(
            miner_slug="oathcast-weather",
            generation=1,
            supported_intents=("WEATHER_FORECAST",),
            min_price_micro_usdc=MINIMUM_PRICE_MICRO_USDC,
            yaml_sha256="a" * 64,
            confirmation_status="submitted",
        )
        next_declaration = declaration.next_generation(
            yaml_sha256="b" * 64,
            min_price_micro_usdc=20_000,
        )
        self.assertEqual(declaration.generation, 1)
        self.assertEqual(declaration.confirmation_status, "submitted")
        self.assertEqual(next_declaration.generation, 2)
        self.assertEqual(next_declaration.confirmation_status, "draft")
        self.assertEqual(next_declaration.min_price_micro_usdc, 20_000)

    def test_price_floor_is_enforced_in_integer_micro_units(self):
        with self.assertRaises(ValueError):
            MinerRegistrationDeclaration(
                miner_slug="too-cheap",
                generation=1,
                supported_intents=("WEATHER_FORECAST",),
                min_price_micro_usdc=9999,
                yaml_sha256="a" * 64,
            )


if __name__ == "__main__":
    unittest.main()
