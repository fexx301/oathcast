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
from typing import Any

from oathcast.registration import MinerRegistrationDeclaration, decimal_usdc_to_micro


ROOT = Path(__file__).resolve().parents[1]


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


def _output_mapping(lines: list[str]) -> dict[str, Any]:
    """Parse the deliberately small local on_chain.fields subset."""

    transform = _scalar(lines, r"^\s+transform:\s*(.*?)\s*$")
    fields: dict[str, list[dict[str, Any]]] = {}
    current_group: str | None = None
    current_item: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current_item
        if current_group is not None and current_item is not None:
            fields.setdefault(current_group, []).append(current_item)
        current_item = None

    in_on_chain = False
    in_fields = False
    for line in lines:
        if line.strip() == "on_chain:":
            in_on_chain = True
            continue
        if in_on_chain and line and not line[0].isspace():
            break
        if not in_on_chain:
            continue
        if line.strip() == "fields:":
            in_fields = True
            continue
        if in_fields and line.startswith("  ") and not line.startswith("    "):
            flush()
            in_fields = False
            continue
        if not in_fields:
            continue
        group_match = re.match(r"^\s{4}(strings|integers):\s*$", line)
        if group_match:
            flush()
            current_group = group_match.group(1)
            continue
        item_match = re.match(r"^\s+-\s+index:\s*(\d+)\s*$", line)
        if item_match:
            flush()
            current_item = {"index": int(item_match.group(1))}
            continue
        value_match = re.match(r"^\s{8}(name|source_path|multiplier):\s*(.*?)\s*$", line)
        if value_match and current_item is not None:
            key, value = value_match.groups()
            if key == "multiplier":
                current_item[key] = int(value)
            else:
                current_item[key] = value.strip("\"'")
    flush()
    if not transform or not fields:
        raise ValueError("canonical YAML has no parseable on_chain output mapping")
    return {"transform": transform, "fields": fields}


def build_registration_draft(yaml_path: Path) -> dict[str, Any]:
    yaml_path = yaml_path.resolve()
    if not yaml_path.exists():
        raise FileNotFoundError(yaml_path)
    raw_lines = yaml_path.read_text(encoding="utf-8").splitlines()
    slug = _scalar(raw_lines, r"^slug:\s*(.*?)\s*$")
    price = _scalar(raw_lines, r"^\s+min_price_usdc:\s*(.*?)\s*$")
    if not slug or not price:
        raise ValueError("canonical YAML must define slug and on_chain.min_price_usdc")
    intents = _supported_intents(raw_lines)
    if not intents:
        raise ValueError("canonical YAML must define at least one supported Intent")

    output_mapping = _output_mapping(raw_lines)
    declaration = MinerRegistrationDeclaration.from_yaml(
        yaml_path,
        miner_slug=slug,
        supported_intents=intents,
        min_price_micro_usdc=decimal_usdc_to_micro(price),
        output_mapping=output_mapping,
        source_authority="local_draft",
        confirmation_status="draft",
        schema_profile="unconfirmed_hackathon_1",
    )
    return {
        "artifact_type": "oathcast_miner_registration_dry_run",
        "artifact_version": 1,
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "path": str(yaml_path.relative_to(ROOT)),
            "yaml_sha256": declaration.yaml_sha256,
            "local_validator": "scripts/validate_miner_drafts.py",
        },
        "registration": declaration.to_dict(),
        "output_mapping": output_mapping,
        "official_registration": {
            "submitted": False,
            "registered": False,
            "transaction_hash": None,
            "blocked_on": [
                "Hackathon 1 integration-interface YAML freeze",
                "official portal validation",
                "official Base Sepolia registration flow and contract details",
            ],
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
    args = parser.parse_args()
    artifact = build_registration_draft(args.yaml)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "yaml_sha256": artifact["registration"]["yaml_sha256"]}
        )
    )


if __name__ == "__main__":
    main()
