"""Hackathon-safe Miner registration declarations.

The whitepaper describes a permissionless on-chain registration, but the
Hackathon 1 portal and current YAML reference remain the source of truth for
the exact transaction and schema.  This module therefore records an
immutable, typed *declaration* without attempting to submit or encode a
registration transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from oathcast.discovery import WEATHER_INTENTS


BASE_SEPOLIA_NETWORK = "eip155:84532"
MINIMUM_PRICE_MICRO_USDC = 10_000
REGISTRATION_STATUSES = frozenset(
    {"draft", "portal_validated", "submitted", "registered", "superseded"}
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVM_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_EVM_ADDRESS = "0x" + ("0" * 40)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def output_mapping_fingerprint(mapping: Mapping[str, Any] | None) -> str | None:
    if mapping is None:
        return None
    return _sha256_bytes(_canonical_json(dict(mapping)).encode("utf-8"))


def decimal_usdc_to_micro(value: str | Decimal) -> int:
    """Convert an explicitly decimal USDC value to integer micro-USDC."""

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("USDC price must be a finite decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("USDC price must be finite and non-negative")
    scaled = amount * Decimal(1_000_000)
    if scaled != scaled.to_integral_value():
        raise ValueError("USDC price has more than six decimal places")
    return int(scaled)


@dataclass(frozen=True)
class MinerRegistrationDeclaration:
    """One generation of a Miner registration draft or submitted record."""

    miner_slug: str
    generation: int
    supported_intents: tuple[str, ...]
    min_price_micro_usdc: int
    yaml_sha256: str
    yaml_uri: str | None = None
    miner_id: str | None = None
    fee_address: str | None = None
    output_mapping_sha256: str | None = None
    chain: str = BASE_SEPOLIA_NETWORK
    source_authority: str = "local_draft"
    confirmation_status: str = "draft"
    schema_profile: str = "unconfirmed_hackathon_1"
    evidence_uri: str | None = None

    def __post_init__(self) -> None:
        if not self.miner_slug.strip():
            raise ValueError("miner_slug is required")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise ValueError("generation must be an integer")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if not self.supported_intents or any(
            not isinstance(item, str) or not item.strip()
            for item in self.supported_intents
        ):
            raise ValueError("at least one supported Intent is required")
        unsupported_intents = sorted(
            set(self.supported_intents).difference(WEATHER_INTENTS)
        )
        if unsupported_intents:
            raise ValueError(
                f"unsupported weather Intent(s): {', '.join(unsupported_intents)}"
            )
        if len(set(self.supported_intents)) != len(self.supported_intents):
            raise ValueError("supported Intents must not contain duplicates")
        if isinstance(self.min_price_micro_usdc, bool) or not isinstance(
            self.min_price_micro_usdc, int
        ):
            raise ValueError("min_price_micro_usdc must be an integer")
        if self.min_price_micro_usdc < MINIMUM_PRICE_MICRO_USDC:
            raise ValueError("Miner price is below the 0.01 USDC floor")
        if not SHA256_PATTERN.fullmatch(self.yaml_sha256):
            raise ValueError("yaml_sha256 must be a lowercase SHA-256 digest")
        if self.output_mapping_sha256 is not None and not SHA256_PATTERN.fullmatch(
            self.output_mapping_sha256
        ):
            raise ValueError("output_mapping_sha256 must be a lowercase SHA-256 digest")
        if self.fee_address is not None:
            if not isinstance(self.fee_address, str) or (
                not EVM_ADDRESS_PATTERN.fullmatch(self.fee_address)
                or self.fee_address.lower() == ZERO_EVM_ADDRESS
            ):
                raise ValueError("fee_address must be a nonzero EVM address")
        if self.confirmation_status not in REGISTRATION_STATUSES:
            raise ValueError(f"unsupported registration status: {self.confirmation_status}")
        if not self.chain.strip():
            raise ValueError("chain profile is required")
        if not self.source_authority.strip():
            raise ValueError("source_authority is required")

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        miner_slug: str,
        supported_intents: tuple[str, ...] | list[str],
        min_price_micro_usdc: int,
        yaml_uri: str | None = None,
        miner_id: str | None = None,
        fee_address: str | None = None,
        output_mapping: Mapping[str, Any] | None = None,
        chain: str = BASE_SEPOLIA_NETWORK,
        source_authority: str = "local_draft",
        confirmation_status: str = "draft",
        schema_profile: str = "unconfirmed_hackathon_1",
        evidence_uri: str | None = None,
        generation: int = 1,
    ) -> "MinerRegistrationDeclaration":
        raw = Path(path).read_bytes()
        return cls(
            miner_slug=miner_slug,
            generation=generation,
            supported_intents=tuple(dict.fromkeys(str(item) for item in supported_intents)),
            min_price_micro_usdc=min_price_micro_usdc,
            yaml_sha256=_sha256_bytes(raw),
            yaml_uri=yaml_uri,
            miner_id=miner_id,
            fee_address=fee_address,
            output_mapping_sha256=output_mapping_fingerprint(output_mapping),
            chain=chain,
            source_authority=source_authority,
            confirmation_status=confirmation_status,
            schema_profile=schema_profile,
            evidence_uri=evidence_uri,
        )

    def next_generation(self, **changes: Any) -> "MinerRegistrationDeclaration":
        """Return a new declaration; a submitted generation is never mutated."""

        # Portal validation is bound to the exact YAML digest.  Any new
        # generation, including one created from a portal-validated draft,
        # must therefore begin unvalidated.
        changes["confirmation_status"] = "draft"
        changes["generation"] = self.generation + 1
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "miner_slug": self.miner_slug,
            "generation": self.generation,
            "supported_intents": list(self.supported_intents),
            "min_price_micro_usdc": self.min_price_micro_usdc,
            "min_price_usdc": f"{self.min_price_micro_usdc / 1_000_000:.6f}",
            "yaml_sha256": self.yaml_sha256,
            "yaml_uri": self.yaml_uri,
            "miner_id": self.miner_id,
            "fee_address": self.fee_address,
            "output_mapping_sha256": self.output_mapping_sha256,
            "chain": self.chain,
            "source_authority": self.source_authority,
            "confirmation_status": self.confirmation_status,
            "schema_profile": self.schema_profile,
            "evidence_uri": self.evidence_uri,
        }
