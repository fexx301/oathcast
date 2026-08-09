"""Capability discovery and filtering for the Application router.

The live registry/explorer response shape is not locked in this preparation
phase, so this module accepts the common fields and keeps parsing isolated from
the routing policy. A local snapshot is only a development fixture, never a
claim that those records are live external Miners.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable


WEATHER_INTENTS = frozenset({"WEATHER_FORECAST", "WEATHER_CHECK", "WEATHER_RISK_ASSESSMENT"})


def _is_active(data: dict[str, Any]) -> bool:
    """Interpret the status variants used by local and live registry records."""

    value = data.get("active")
    if value is None:
        value = data.get("activation_status", data.get("status", "active"))
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"active", "online", "healthy", "ready"}


def _endpoint_path(data: dict[str, Any]) -> str:
    """Select the most useful weather endpoint from a live integrations record."""

    explicit = data.get("endpoint_path")
    if explicit:
        return str(explicit)

    endpoints = data.get("endpoints", [])
    if isinstance(endpoints, dict):
        endpoints = [endpoints]
    paths = [
        str(item.get("path"))
        for item in endpoints
        if isinstance(item, dict) and item.get("path")
    ] if isinstance(endpoints, list) else []
    for preferred in ("/predict", "/forecast", "/weather", "/current"):
        if preferred in paths:
            return preferred
    return paths[0] if paths else "/v1/forecast/point"


def _endpoint_name(data: dict[str, Any], path: str) -> str:
    explicit = data.get("endpoint_name", data.get("endpoint"))
    if explicit:
        return str(explicit).lstrip("/")
    return path.rstrip("/").rsplit("/", 1)[-1] or "predict"


def _price_to_micro_usdc(value: Any) -> int | None:
    """Convert a decimal USDC value from a YAML-style record to micro-USDC."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None
    if isinstance(value, str) and "." not in value and "e" not in value.lower():
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal_value.is_finite() or decimal_value < 0:
        return None
    scaled = decimal_value * Decimal(1_000_000)
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


def _explicit_micro_usdc(value: Any) -> int | None:
    """Parse only the integer field whose name explicitly declares its unit."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _min_price_micro_usdc(data: dict[str, Any]) -> int | None:
    """Parse live integer prices and YAML decimal prices without guessing."""

    price_data = data
    if not any(key in data for key in ("min_price_micro_usdc", "min_price_usdc")):
        on_chain = data.get("on_chain")
        if isinstance(on_chain, dict):
            price_data = on_chain

    if "min_price_micro_usdc" in price_data:
        return _explicit_micro_usdc(price_data.get("min_price_micro_usdc"))

    value = price_data.get("min_price_usdc")
    if isinstance(value, int) and not isinstance(value, bool):
        # Telegraph's live integrations response uses this field name for
        # integer micro-USDC amounts. YAML decimal values take the path below.
        return _explicit_micro_usdc(value)
    return _price_to_micro_usdc(value)


def _protocol_reliability(data: dict[str, Any]) -> float:
    """Use Telegraph's live average score, with legacy data as a fallback."""

    value = data.get("avg_score")
    if value is None:
        value = data.get("historical_reliability", 0.5)
    try:
        reliability = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("historical_reliability must be between 0 and 1") from exc
    if not 0 <= reliability <= 1:
        raise ValueError("historical_reliability must be between 0 and 1")
    return reliability


@dataclass(frozen=True)
class MinerCapability:
    miner_id: str
    slug: str
    name: str
    base_url: str
    intents: frozenset[str]
    active: bool = True
    endpoint_path: str = "/v1/forecast/point"
    owner: str | None = None
    historical_reliability: float = 0.5
    endpoint_name: str = "predict"
    min_price_micro_usdc: int | None = None
    registry_snapshot_sha256: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MinerCapability":
        semantics = data.get("semantics") if isinstance(data.get("semantics"), dict) else {}
        supported = data.get(
            "supported_intents",
            data.get("intents", semantics.get("supported_intents", [])),
        )
        if isinstance(supported, str):
            supported = [supported]
        if not isinstance(supported, list):
            supported = []
        reliability = _protocol_reliability(data)
        endpoint_path = _endpoint_path(data)
        min_price_micro_usdc = _min_price_micro_usdc(data)
        return cls(
            miner_id=str(data.get("id", data.get("miner_id", ""))),
            slug=str(data.get("slug", "")),
            name=str(data.get("name", data.get("slug", "Unnamed Miner"))),
            base_url=str(data.get("base_url", "")),
            intents=frozenset(str(item) for item in supported),
            active=_is_active(data),
            endpoint_path=endpoint_path,
            owner=data.get("owner"),
            historical_reliability=reliability,
            endpoint_name=_endpoint_name(data, endpoint_path),
            min_price_micro_usdc=min_price_micro_usdc,
            registry_snapshot_sha256=(
                data.get("registry_snapshot_sha256", data.get("snapshot_sha256"))
            ),
        )

    @property
    def supports_weather(self) -> bool:
        return bool(self.intents & WEATHER_INTENTS)


def discover_weather_miners(
    records: Iterable[dict[str, Any]],
    *,
    own_slugs: set[str] | None = None,
    own_ids: set[str] | None = None,
) -> list[MinerCapability]:
    """Return active weather-capable Miners excluding participant-owned records."""

    own_slugs = own_slugs or set()
    own_ids = own_ids or set()
    discovered: list[MinerCapability] = []
    seen: set[str] = set()
    for record in records:
        capability = MinerCapability.from_dict(record)
        if not capability.active or not capability.supports_weather:
            continue
        if capability.slug in own_slugs or capability.miner_id in own_ids:
            continue
        if not capability.slug or not capability.miner_id or capability.slug in seen:
            continue
        seen.add(capability.slug)
        discovered.append(capability)
    return discovered


def integration_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize the array/object wrappers used by integrations endpoints."""

    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        for key in ("integrations", "miners", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [record for record in value if isinstance(record, dict)]
    raise ValueError("integrations response must contain a JSON object array")


def load_registry_snapshot(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, list):
        raise ValueError("registry snapshot must be a JSON array")
    return [record for record in payload if isinstance(record, dict)]
