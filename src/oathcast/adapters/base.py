"""Shared adapter helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from oathcast.forecast import CanonicalForecast, ForecastQuestion, UTC, ensure_utc, parse_timestamp


class AdapterError(ValueError):
    """Raised when a provider response cannot be mapped without guessing."""


@dataclass(frozen=True)
class HourPoint:
    timestamp: datetime
    probability: float


class ProviderAdapter(Protocol):
    provider: str
    adapter_version: str

    def build_url(self, question: ForecastQuestion, api_key: str | None = None) -> str:
        ...

    def parse(
        self,
        payload: dict[str, Any],
        question: ForecastQuestion,
        issued_at: datetime,
        retrieved_at: datetime | None = None,
    ) -> CanonicalForecast:
        ...


def select_exact_point(points: list[HourPoint], target: datetime, provider: str) -> HourPoint:
    """Select one provider-native hour; never silently aggregate or choose nearest."""

    target_utc = ensure_utc(target, "target")
    for point in points:
        if ensure_utc(point.timestamp, "point.timestamp") == target_utc:
            return point
    raise AdapterError(
        f"{provider} did not return an exact native forecast point for {target_utc.isoformat()}; "
        "refusing to aggregate or choose a nearest hour"
    )


def probability_from_percent(value: Any, provider: str) -> float:
    if isinstance(value, bool):
        raise AdapterError(f"{provider} returned a non-numeric percentage")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{provider} returned a non-numeric percentage") from exc
    if not 0 <= numeric <= 100:
        raise AdapterError(f"{provider} returned a percentage outside [0, 100]")
    return numeric / 100


def probability_from_unit_interval(value: Any, provider: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{provider} returned a non-numeric probability") from exc
    if not 0 <= numeric <= 1:
        raise AdapterError(f"{provider} returned a probability outside [0, 1]")
    return numeric


def parse_provider_time(value: str | datetime) -> datetime:
    return parse_timestamp(value, default_timezone=UTC)


def canonical_forecast(
    *,
    provider: str,
    adapter_version: str,
    question: ForecastQuestion,
    probability: float,
    issued_at: datetime,
    native_event_definition: str,
    event_equivalence: str,
    provider_model: str | None = None,
    retrieved_at: datetime | None = None,
) -> CanonicalForecast:
    return CanonicalForecast(
        event_id=question.event_id,
        provider=provider,
        probability=probability,
        horizon_start=question.horizon_start,
        horizon_end=question.horizon_end,
        threshold_mm=question.threshold_mm,
        issued_at=issued_at,
        native_event_definition=native_event_definition,
        event_equivalence=event_equivalence,
        adapter_version=adapter_version,
        provider_model=provider_model,
        retrieved_at=retrieved_at,
    )
