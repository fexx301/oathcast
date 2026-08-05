from pathlib import Path
import tempfile
import unittest

from oathcast.registration import (
    BASE_SEPOLIA_NETWORK,
    MINIMUM_PRICE_MICRO_USDC,
    MinerRegistrationDeclaration,
    decimal_usdc_to_micro,
)


class RegistrationDeclarationTests(unittest.TestCase):
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
