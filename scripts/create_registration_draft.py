#!/usr/bin/env python3
"""Create a non-submitting registration snapshot from the canonical YAML.

This is a local dry-run artifact. It records the exact raw-YAML digest and
the current candidate mapping, but never contacts Telegraph, signs a wallet,
or claims that a Miner is registered.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from oathcast.registration import (
    MINIMUM_PRICE_MICRO_USDC,
    MinerRegistrationDeclaration,
)

try:
    from scripts.validate_miner_drafts import validate_draft
except ModuleNotFoundError:
    from validate_miner_drafts import validate_draft


ROOT = Path(__file__).resolve().parents[1]
BASE_SEPOLIA_CHAIN_ID = 84532
MINER_REGISTRY_DIAMOND = "0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8"
REGISTER_MINER_SIGNATURE = "registerMiner(string,bytes32,address,uint256,string[])"
CANONICAL_INTENTS = ("WEATHER_FORECAST",)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_EVM_ADDRESS = "0x" + ("0" * 40)


def _scalar(lines: list[str], pattern: str) -> str | None:
    match_pattern = re.compile(pattern)
    for line in lines:
        match = match_pattern.match(line)
        if match:
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value
    return None


def _supported_intents(lines: list[str]) -> tuple[str, ...]:
    intents: list[str] = []
    in_intents = False
    for line in lines:
        if line.strip() == "supported_intents:":
            in_intents = True
            continue
        if in_intents and line and not line[0].isspace():
            break
        if in_intents:
            match = re.match(r"^\s+-\s+([A-Z][A-Z0-9_]*)\s*$", line)
            if match:
                intents.append(match.group(1))
    return tuple(dict.fromkeys(intents))


def _candidate_miner_id(lines: list[str]) -> str:
    miner_id = _scalar(lines, r"^id:\s*(.*?)\s*$")
    if miner_id is None or not miner_id.isdigit() or int(miner_id) <= 0:
        raise ValueError("canonical YAML must define a positive numeric candidate id")
    return miner_id


def _optional_yaml_uri(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value.startswith(("ipfs://", "https://")):
        raise ValueError("yaml_uri must use ipfs:// or https://")
    return value


def _optional_fee_address(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not EVM_ADDRESS_PATTERN.fullmatch(value) or value.lower() == ZERO_EVM_ADDRESS:
        raise ValueError("fee_address must be a nonzero EVM address")
    return value


def build_registration_draft(
    yaml_path: Path,
    *,
    min_price_micro_usdc: int = MINIMUM_PRICE_MICRO_USDC,
    yaml_uri: str | None = None,
    fee_address: str | None = None,
) -> dict[str, object]:
    yaml_path = yaml_path.resolve()
    if not yaml_path.exists():
        raise FileNotFoundError(yaml_path)
    local_validation = validate_draft(yaml_path, canonical=True)
    if not local_validation["valid"]:
        raise ValueError(
            "canonical YAML failed local validation: "
            + "; ".join(local_validation["errors"])
        )
    raw_lines = yaml_path.read_text(encoding="utf-8").splitlines()
    slug = _scalar(raw_lines, r"^slug:\s*(.*?)\s*$")
    if not slug:
        raise ValueError("canonical YAML must define slug")
    miner_id = _candidate_miner_id(raw_lines)
    intents = _supported_intents(raw_lines)
    if intents != CANONICAL_INTENTS:
        raise ValueError("canonical YAML must support exactly WEATHER_FORECAST")
    if (
        isinstance(min_price_micro_usdc, bool)
        or not isinstance(min_price_micro_usdc, int)
        or min_price_micro_usdc < MINIMUM_PRICE_MICRO_USDC
    ):
        raise ValueError("min_price_micro_usdc must be an integer at least 10000")
    yaml_uri = _optional_yaml_uri(yaml_uri)
    fee_address = _optional_fee_address(fee_address)

    declaration = MinerRegistrationDeclaration.from_yaml(
        yaml_path,
        miner_slug=slug,
        supported_intents=intents,
        min_price_micro_usdc=min_price_micro_usdc,
        yaml_uri=yaml_uri,
        miner_id=miner_id,
        fee_address=fee_address,
        source_authority="telegraph_live_docs_local_draft",
        confirmation_status="draft",
        schema_profile="telegraph_live_2026-08-13",
    )
    pending = [
        "consult the separate registration-readiness manifest for external portal/IPFS observations; this offline generator does not perform them",
        "live portal/node recheck that the candidate slug remains available; the numeric routing id is not the on-chain registrationId",
    ]
    if yaml_uri is None:
        pending.append("pin the exact final YAML bytes to stable IPFS or HTTPS")
    if fee_address is None:
        pending.append("provide and verify a nonzero EVM fee address")
    pending.append("fund the registering wallet with a small amount of Base Sepolia ETH for gas")
    pending.append("obtain action-time confirmation before wallet signature or submission")
    return {
        "artifact_type": "oathcast_miner_registration_dry_run",
        "artifact_version": 2,
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "path": str(yaml_path.relative_to(ROOT)),
            "yaml_sha256": declaration.yaml_sha256,
            "yaml_hash_bytes32": f"0x{declaration.yaml_sha256}",
            "local_validator": "scripts/validate_miner_drafts.py",
            "local_validation": {
                "status": "passed",
                "scope": local_validation["validation_scope"],
                "warnings": local_validation["warnings"],
            },
        },
        "registration": declaration.to_dict(),
        "registration_call": {
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "contract": MINER_REGISTRY_DIAMOND,
            "signature": REGISTER_MINER_SIGNATURE,
            "arguments": {
                "yaml_uri": yaml_uri,
                "yaml_hash_bytes32": f"0x{declaration.yaml_sha256}",
                "fee_address": fee_address,
                "min_price_micro_usdc": min_price_micro_usdc,
                "supported_intents": list(intents),
            },
            "ready_to_encode": False,
            "encoded_calldata": None,
        },
        "registration_input_sources": {
            "candidate_id": "canonical YAML id",
            "supported_intents": "canonical YAML semantics.supported_intents",
            "yaml_hash_bytes32": "SHA-256 of the exact raw YAML bytes",
            "yaml_uri": (
                "explicit --yaml-uri operator/portal input"
                if yaml_uri is not None
                else "operator/portal input; absent from this generated draft"
            ),
            "fee_address": (
                "explicit --fee-address operator input"
                if fee_address is not None
                else "operator/portal input; absent from this generated draft"
            ),
            "min_price_micro_usdc": "explicit generator input; protocol floor defaults to 10000",
        },
        "official_registration": {
            "status_scope": "local_generator_only",
            "status_scope_note": (
                "not_run means this offline generator did not contact Telegraph; "
                "external portal and IPFS observations, when available, are recorded "
                "in the separate registration-readiness manifest"
            ),
            "portal_validation_status": "not_run",
            "endpoint_sandbox_status": "not_run",
            "submitted": False,
            "registered": False,
            "transaction_hash": None,
            "pending": pending,
        },
        "claims": {
            "telegraph_registered": False,
            "paid_telegraph_traffic": False,
            "official_request_count": None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yaml",
        type=Path,
        default=ROOT / "miners" / "oathcast-weather.yaml",
        help="canonical Miner YAML draft",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "artifacts"
        / "registration-drafts"
        / "oathcast-weather-registration-draft.json",
    )
    parser.add_argument(
        "--min-price-micro-usdc",
        type=int,
        default=MINIMUM_PRICE_MICRO_USDC,
        help="registration transaction price; defaults to the 10000 micro-USDC floor",
    )
    parser.add_argument(
        "--yaml-uri",
        help="optional stable ipfs:// or https:// URI for the exact final YAML bytes",
    )
    parser.add_argument(
        "--fee-address",
        help="optional nonzero EVM fee address; omit for an unsigned incomplete draft",
    )
    args = parser.parse_args()
    artifact = build_registration_draft(
        args.yaml,
        min_price_micro_usdc=args.min_price_micro_usdc,
        yaml_uri=args.yaml_uri,
        fee_address=args.fee_address,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "yaml_sha256": artifact["registration"]["yaml_sha256"],
                "ready_to_encode": artifact["registration_call"]["ready_to_encode"],
            }
        )
    )


if __name__ == "__main__":
    main()
