from pathlib import Path
import tempfile
import unittest

from oathcast.registration import (
    BASE_SEPOLIA_NETWORK,
    MINIMUM_PRICE_MICRO_USDC,
    MinerRegistrationDeclaration,
    decimal_usdc_to_micro,
)
from scripts.validate_miner_drafts import (
    INTEGRATION_ID_PLACEHOLDER,
    validate_draft,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MINER = ROOT / "miners" / "oathcast-weather.yaml"


class RegistrationDeclarationTests(unittest.TestCase):
    def test_canonical_yaml_matches_service_contract_and_is_not_portal_validated(self):
        result = validate_draft(CANONICAL_MINER, canonical=True)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["validation_scope"], "draft_local")
        self.assertEqual(result["official_portal_validation"]["status"], "not_run")
        self.assertFalse(result["official_portal_validation"]["validated"])
        self.assertFalse(result["official_portal_validated"])
        self.assertEqual(result["id"], INTEGRATION_ID_PLACEHOLDER)
        self.assertTrue(any("placeholder" in warning for warning in result["warnings"]))

        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        for fragment in (
            "input_schema:\n",
            "output_schema:\n",
            "required:\n    - lat\n    - lon\n    - start\n    - end\n",
            "lat: { source: strings.2, type: float }",
            "probability_x10000",
            "multiplier: 10000",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, yaml_text)

    def test_canonical_local_validation_rejects_missing_schema(self):
        yaml_text = CANONICAL_MINER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "miner.yaml"
            path.write_text(yaml_text.replace("input_schema:\n", "# input_schema:\n", 1), encoding="utf-8")
            result = validate_draft(path, canonical=True)

        self.assertFalse(result["valid"])
        self.assertTrue(any("input_schema" in error for error in result["errors"]))
        self.assertEqual(result["official_portal_validation"]["status"], "not_run")

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
