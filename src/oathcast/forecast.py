"""Provider-neutral weather forecast contracts.

This is an internal contract. It is deliberately stricter than Telegraph's
current schema-agnostic Weather Intent so that provider differences are
visible, testable, and never silently hidden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any


UTC = timezone.utc
SUPPORTED_EVENT_OPERATOR = ">"
SUPPORTED_EVENT_THRESHOLD_MM = 0.1


def ensure_utc(value: datetime, field_name: str) -> datetime:
    """Return an aware UTC timestamp or fail loudly."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def parse_timestamp(value: str | datetime, default_timezone: timezone = UTC) -> datetime:
    """Parse an ISO timestamp, treating provider-naive values as UTC by default."""

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=default_timezone).astimezone(UTC)
        return value.astimezone(UTC)

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    """Format timestamps consistently for the public text renderer and JSON."""

    return ensure_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ForecastQuestion:
    """A single binary event that can be resolved without probability aggregation."""

    event_id: str
    location_name: str
    latitude: float
    longitude: float
    horizon_start: datetime
    horizon_end: datetime
    forecast_cutoff: datetime
    threshold_mm: float = 0.1
    metric: str = "precipitation"
    precipitation_type: str = "measurable"
    operator: str = SUPPORTED_EVENT_OPERATOR
    timezone: str = "UTC"
    spatial_semantics: str = "point"

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.location_name.strip():
            raise ValueError("location_name must not be empty")
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if self.metric != "precipitation":
            raise ValueError("the first OathCast spike supports precipitation only")
        if self.precipitation_type != "measurable":
            raise ValueError("the first OathCast spike supports measurable precipitation only")
        if self.operator != SUPPORTED_EVENT_OPERATOR:
            raise ValueError(
                "the first OathCast spike supports only the provider-native > comparison"
            )
        if not math.isclose(
            self.threshold_mm,
            SUPPORTED_EVENT_THRESHOLD_MM,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "the first OathCast spike supports only the provider-native 0.1 mm threshold"
            )

        start = ensure_utc(self.horizon_start, "horizon_start")
        end = ensure_utc(self.horizon_end, "horizon_end")
        cutoff = ensure_utc(self.forecast_cutoff, "forecast_cutoff")
        if end <= start:
            raise ValueError("horizon_end must be after horizon_start")
        if end - start != timedelta(hours=1):
            raise ValueError(
                "the preparation spike only accepts one-hour windows; "
                "broader windows need provider-native probability semantics"
            )
        if cutoff >= start:
            raise ValueError("forecast_cutoff must be before horizon_start")

        object.__setattr__(self, "horizon_start", start)
        object.__setattr__(self, "horizon_end", end)
        object.__setattr__(self, "forecast_cutoff", cutoff)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ForecastQuestion":
        return cls(
            event_id=str(data["event_id"]),
            location_name=str(data["location_name"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            horizon_start=parse_timestamp(data["horizon_start"]),
            horizon_end=parse_timestamp(data["horizon_end"]),
            forecast_cutoff=parse_timestamp(data["forecast_cutoff"]),
            threshold_mm=float(data.get("threshold_mm", 0.1)),
            metric=str(data.get("metric", "precipitation")),
            precipitation_type=str(data.get("precipitation_type", "measurable")),
            operator=str(data.get("operator", SUPPORTED_EVENT_OPERATOR)),
            timezone=str(data.get("timezone", "UTC")),
            spatial_semantics=str(data.get("spatial_semantics", "point")),
        )

    @property
    def event_label(self) -> str:
        return f"measurable precipitation > {self.threshold_mm:g} mm"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field_name in ("horizon_start", "horizon_end", "forecast_cutoff"):
            data[field_name] = format_timestamp(data[field_name])
        return data


@dataclass(frozen=True)
class CanonicalForecast:
    """Normalized provider output used by the app, renderer, and local scorer."""

    event_id: str
    provider: str
    probability: float
    horizon_start: datetime
    horizon_end: datetime
    threshold_mm: float
    issued_at: datetime
    native_event_definition: str
    event_equivalence: str
    adapter_version: str
    provider_model: str | None = None
    retrieved_at: datetime | None = None
    raw_payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not math.isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise ValueError("probability must be a finite number in [0, 1]")
        if not math.isclose(
            self.threshold_mm,
            SUPPORTED_EVENT_THRESHOLD_MM,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "the first OathCast spike supports only the provider-native 0.1 mm threshold"
            )

        start = ensure_utc(self.horizon_start, "horizon_start")
        end = ensure_utc(self.horizon_end, "horizon_end")
        issued = ensure_utc(self.issued_at, "issued_at")
        retrieved = (
            None if self.retrieved_at is None else ensure_utc(self.retrieved_at, "retrieved_at")
        )
        if end - start != timedelta(hours=1):
            raise ValueError("CanonicalForecast currently supports one-hour windows only")

        object.__setattr__(self, "horizon_start", start)
        object.__setattr__(self, "horizon_end", end)
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "retrieved_at", retrieved)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field_name in (
            "horizon_start",
            "horizon_end",
            "issued_at",
            "retrieved_at",
        ):
            if data[field_name] is not None:
                data[field_name] = format_timestamp(data[field_name])
        return data
