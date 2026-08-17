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
WINDOW_PROBABILITY_SEMANTICS = (
    "maximum_one_hour_precipitation_probability_within_requested_window"
)


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


@dataclass(frozen=True)
class ForecastWindowRequest:
    """A UTC whole-hour forecast window kept separate from the binary event model."""

    event_id: str
    location_name: str
    latitude: float
    longitude: float
    horizon_start: datetime
    horizon_end: datetime
    forecast_cutoff: datetime
    threshold_mm: float = SUPPORTED_EVENT_THRESHOLD_MM
    operator: str = SUPPORTED_EVENT_OPERATOR
    timezone: str = "UTC"
    spatial_semantics: str = "point"

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.location_name.strip():
            raise ValueError("location_name must not be empty")
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be a finite number between -90 and 90")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be a finite number between -180 and 180")
        if self.timezone != "UTC":
            raise ValueError("forecast windows support UTC only")
        if self.spatial_semantics != "point":
            raise ValueError("forecast windows support point locations only")
        if self.operator != SUPPORTED_EVENT_OPERATOR:
            raise ValueError("forecast windows support only the provider-native > comparison")
        if not math.isclose(
            self.threshold_mm,
            SUPPORTED_EVENT_THRESHOLD_MM,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "forecast windows support only the provider-native 0.1 mm threshold"
            )

        start = ensure_utc(self.horizon_start, "horizon_start")
        end = ensure_utc(self.horizon_end, "horizon_end")
        cutoff = ensure_utc(self.forecast_cutoff, "forecast_cutoff")
        for field_name, value in (("horizon_start", start), ("horizon_end", end)):
            if value.minute or value.second or value.microsecond:
                raise ValueError(f"{field_name} must be aligned to a whole UTC hour")
        duration = end - start
        if duration < timedelta(hours=1) or duration > timedelta(hours=24):
            raise ValueError("forecast window duration must be between 1 and 24 hours")
        if duration.total_seconds() % 3600 != 0:
            raise ValueError("forecast window duration must be a whole number of hours")
        if cutoff >= start:
            raise ValueError("forecast_cutoff must be before horizon_start")

        object.__setattr__(self, "horizon_start", start)
        object.__setattr__(self, "horizon_end", end)
        object.__setattr__(self, "forecast_cutoff", cutoff)

    @property
    def duration_hours(self) -> int:
        return int((self.horizon_end - self.horizon_start).total_seconds() // 3600)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ForecastWindowRequest":
        return cls(
            event_id=str(data["event_id"]),
            location_name=str(data["location_name"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            horizon_start=parse_timestamp(data["horizon_start"]),
            horizon_end=parse_timestamp(data["horizon_end"]),
            forecast_cutoff=parse_timestamp(data["forecast_cutoff"]),
            threshold_mm=float(data.get("threshold_mm", SUPPORTED_EVENT_THRESHOLD_MM)),
            operator=str(data.get("operator", SUPPORTED_EVENT_OPERATOR)),
            timezone=str(data.get("timezone", "UTC")),
            spatial_semantics=str(data.get("spatial_semantics", "point")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field_name in ("horizon_start", "horizon_end", "forecast_cutoff"):
            data[field_name] = format_timestamp(data[field_name])
        return data


@dataclass(frozen=True)
class HourlyWindowForecast:
    """One complete provider-native UTC hour in a window forecast."""

    interval_start: datetime
    interval_end: datetime
    temperature_2m_c: float
    precipitation_probability: float

    def __post_init__(self) -> None:
        start = ensure_utc(self.interval_start, "interval_start")
        end = ensure_utc(self.interval_end, "interval_end")
        if end - start != timedelta(hours=1):
            raise ValueError("hourly forecast intervals must be exactly one hour")
        if start.minute or start.second or start.microsecond:
            raise ValueError("hourly forecast intervals must start on a whole UTC hour")
        if not math.isfinite(self.temperature_2m_c):
            raise ValueError("temperature_2m_c must be finite")
        if (
            not math.isfinite(self.precipitation_probability)
            or not 0 <= self.precipitation_probability <= 1
        ):
            raise ValueError("precipitation_probability must be finite and in [0, 1]")

        object.__setattr__(self, "interval_start", start)
        object.__setattr__(self, "interval_end", end)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HourlyWindowForecast":
        return cls(
            interval_start=parse_timestamp(data["interval_start"]),
            interval_end=parse_timestamp(data["interval_end"]),
            temperature_2m_c=float(data["temperature_2m_c"]),
            precipitation_probability=float(data["precipitation_probability"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_start": format_timestamp(self.interval_start),
            "interval_end": format_timestamp(self.interval_end),
            "temperature_2m_c": self.temperature_2m_c,
            "precipitation_probability": self.precipitation_probability,
        }


@dataclass(frozen=True)
class CanonicalWindowForecast:
    """Complete normalized hourly coverage for one 1-to-24-hour request."""

    event_id: str
    provider: str
    horizon_start: datetime
    horizon_end: datetime
    issued_at: datetime
    hours: tuple[HourlyWindowForecast, ...]
    temperature_native_definition: str
    precipitation_native_definition: str
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

        start = ensure_utc(self.horizon_start, "horizon_start")
        end = ensure_utc(self.horizon_end, "horizon_end")
        issued = ensure_utc(self.issued_at, "issued_at")
        retrieved = (
            None if self.retrieved_at is None else ensure_utc(self.retrieved_at, "retrieved_at")
        )
        for field_name, value in (("horizon_start", start), ("horizon_end", end)):
            if value.minute or value.second or value.microsecond:
                raise ValueError(
                    f"canonical forecast window {field_name} must align to a whole UTC hour"
                )
        duration = end - start
        if duration < timedelta(hours=1) or duration > timedelta(hours=24):
            raise ValueError("canonical forecast window must be between 1 and 24 hours")
        if duration.total_seconds() % 3600 != 0:
            raise ValueError("canonical forecast window duration must be a whole number of hours")
        expected_count = int(duration.total_seconds() // 3600)
        hours = tuple(self.hours)
        if len(hours) != expected_count:
            raise ValueError("canonical forecast window must contain complete hourly coverage")
        for index, hour in enumerate(hours):
            expected_start = start + timedelta(hours=index)
            if (
                hour.interval_start != expected_start
                or hour.interval_end != expected_start + timedelta(hours=1)
            ):
                raise ValueError("canonical forecast window hours must be unique and contiguous")

        object.__setattr__(self, "horizon_start", start)
        object.__setattr__(self, "horizon_end", end)
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "retrieved_at", retrieved)
        object.__setattr__(self, "hours", hours)

    @property
    def minimum_hourly_temperature_c(self) -> float:
        return min(hour.temperature_2m_c for hour in self.hours)

    @property
    def maximum_hourly_temperature_c(self) -> float:
        return max(hour.temperature_2m_c for hour in self.hours)

    @property
    def peak_precipitation_hour(self) -> HourlyWindowForecast:
        return max(
            self.hours,
            key=lambda hour: (
                hour.precipitation_probability,
                -int(hour.interval_start.timestamp()),
            ),
        )

    @property
    def probability(self) -> float:
        return self.peak_precipitation_hour.precipitation_probability

    @property
    def probability_semantics(self) -> str:
        return WINDOW_PROBABILITY_SEMANTICS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalWindowForecast":
        raw_hours = data["hours"]
        if not isinstance(raw_hours, list):
            raise ValueError("canonical forecast window hours must be a list")
        return cls(
            event_id=str(data["event_id"]),
            provider=str(data["provider"]),
            horizon_start=parse_timestamp(data["horizon_start"]),
            horizon_end=parse_timestamp(data["horizon_end"]),
            issued_at=parse_timestamp(data["issued_at"]),
            hours=tuple(HourlyWindowForecast.from_dict(item) for item in raw_hours),
            temperature_native_definition=str(data["temperature_native_definition"]),
            precipitation_native_definition=str(data["precipitation_native_definition"]),
            event_equivalence=str(data["event_equivalence"]),
            adapter_version=str(data["adapter_version"]),
            provider_model=data.get("provider_model"),
            retrieved_at=(
                None
                if data.get("retrieved_at") is None
                else parse_timestamp(data["retrieved_at"])
            ),
            raw_payload_sha256=data.get("raw_payload_sha256"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "horizon_start": format_timestamp(self.horizon_start),
            "horizon_end": format_timestamp(self.horizon_end),
            "issued_at": format_timestamp(self.issued_at),
            "hours": [hour.to_dict() for hour in self.hours],
            "temperature_native_definition": self.temperature_native_definition,
            "precipitation_native_definition": self.precipitation_native_definition,
            "event_equivalence": self.event_equivalence,
            "adapter_version": self.adapter_version,
            "provider_model": self.provider_model,
            "retrieved_at": (
                None if self.retrieved_at is None else format_timestamp(self.retrieved_at)
            ),
            "raw_payload_sha256": self.raw_payload_sha256,
        }


@dataclass(frozen=True)
class TemperatureWindowRequest:
    """Telegraph's next-N-hour 2 metre temperature request contract."""

    event_id: str
    location_name: str
    latitude: float
    longitude: float
    forecast_hours: int
    reference_time: datetime
    timezone: str = "UTC"
    spatial_semantics: str = "point"

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.location_name.strip():
            raise ValueError("location_name must not be empty")
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be a finite number between -90 and 90")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be a finite number between -180 and 180")
        if (
            isinstance(self.forecast_hours, bool)
            or not isinstance(self.forecast_hours, int)
            or not 1 <= self.forecast_hours <= 24
        ):
            raise ValueError("forecast_hours must be a whole number between 1 and 24")
        if self.timezone != "UTC":
            raise ValueError("temperature forecast windows support UTC only")
        if self.spatial_semantics != "point":
            raise ValueError("temperature forecast windows support point locations only")
        reference = ensure_utc(self.reference_time, "reference_time")
        if reference.minute or reference.second or reference.microsecond:
            raise ValueError("reference_time must be aligned to a whole UTC hour")
        object.__setattr__(self, "reference_time", reference)

    @property
    def horizon_start(self) -> datetime:
        return self.reference_time + timedelta(hours=1)

    @property
    def horizon_end(self) -> datetime:
        return self.horizon_start + timedelta(hours=self.forecast_hours)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemperatureWindowRequest":
        forecast_hours = data["forecast_hours"]
        if isinstance(forecast_hours, bool) or not isinstance(forecast_hours, int):
            raise ValueError("forecast_hours must be a whole number between 1 and 24")
        request = cls(
            event_id=str(data["event_id"]),
            location_name=str(data["location_name"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            forecast_hours=forecast_hours,
            reference_time=parse_timestamp(data["reference_time"]),
            timezone=str(data.get("timezone", "UTC")),
            spatial_semantics=str(data.get("spatial_semantics", "point")),
        )
        if "horizon_start" in data and parse_timestamp(data["horizon_start"]) != request.horizon_start:
            raise ValueError("temperature window horizon_start does not match reference_time")
        if "horizon_end" in data and parse_timestamp(data["horizon_end"]) != request.horizon_end:
            raise ValueError("temperature window horizon_end does not match forecast_hours")
        return request

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reference_time"] = format_timestamp(self.reference_time)
        data["horizon_start"] = format_timestamp(self.horizon_start)
        data["horizon_end"] = format_timestamp(self.horizon_end)
        return data


@dataclass(frozen=True)
class HourlyTemperatureForecast:
    interval_start: datetime
    temperature_2m_c: float

    def __post_init__(self) -> None:
        start = ensure_utc(self.interval_start, "interval_start")
        if start.minute or start.second or start.microsecond:
            raise ValueError("hourly temperatures must start on a whole UTC hour")
        if not math.isfinite(self.temperature_2m_c):
            raise ValueError("temperature_2m_c must be finite")
        object.__setattr__(self, "interval_start", start)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HourlyTemperatureForecast":
        return cls(
            interval_start=parse_timestamp(data["interval_start"]),
            temperature_2m_c=float(data["temperature_2m_c"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_start": format_timestamp(self.interval_start),
            "temperature_2m_c": self.temperature_2m_c,
        }


@dataclass(frozen=True)
class CanonicalTemperatureWindowForecast:
    event_id: str
    provider: str
    reference_time: datetime
    issued_at: datetime
    hours: tuple[HourlyTemperatureForecast, ...]
    temperature_native_definition: str
    adapter_version: str
    provider_model: str | None = None
    retrieved_at: datetime | None = None
    raw_payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        reference = ensure_utc(self.reference_time, "reference_time")
        issued = ensure_utc(self.issued_at, "issued_at")
        retrieved = (
            None if self.retrieved_at is None else ensure_utc(self.retrieved_at, "retrieved_at")
        )
        if reference.minute or reference.second or reference.microsecond:
            raise ValueError("temperature forecast reference_time must align to a whole UTC hour")
        hours = tuple(self.hours)
        if not 1 <= len(hours) <= 24:
            raise ValueError("temperature forecast must contain between 1 and 24 hours")
        for index, hour in enumerate(hours):
            if hour.interval_start != reference + timedelta(hours=index + 1):
                raise ValueError("temperature forecast hours must be unique and contiguous")
        object.__setattr__(self, "reference_time", reference)
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "retrieved_at", retrieved)
        object.__setattr__(self, "hours", hours)

    @property
    def minimum_temperature_2m_c(self) -> float:
        return min(hour.temperature_2m_c for hour in self.hours)

    @property
    def maximum_temperature_2m_c(self) -> float:
        return max(hour.temperature_2m_c for hour in self.hours)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalTemperatureWindowForecast":
        raw_hours = data["hours"]
        if not isinstance(raw_hours, list):
            raise ValueError("temperature forecast hours must be a list")
        return cls(
            event_id=str(data["event_id"]),
            provider=str(data["provider"]),
            reference_time=parse_timestamp(data["reference_time"]),
            issued_at=parse_timestamp(data["issued_at"]),
            hours=tuple(HourlyTemperatureForecast.from_dict(item) for item in raw_hours),
            temperature_native_definition=str(data["temperature_native_definition"]),
            adapter_version=str(data["adapter_version"]),
            provider_model=data.get("provider_model"),
            retrieved_at=(
                None
                if data.get("retrieved_at") is None
                else parse_timestamp(data["retrieved_at"])
            ),
            raw_payload_sha256=data.get("raw_payload_sha256"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "reference_time": format_timestamp(self.reference_time),
            "issued_at": format_timestamp(self.issued_at),
            "hours": [hour.to_dict() for hour in self.hours],
            "temperature_native_definition": self.temperature_native_definition,
            "adapter_version": self.adapter_version,
            "provider_model": self.provider_model,
            "retrieved_at": (
                None if self.retrieved_at is None else format_timestamp(self.retrieved_at)
            ),
            "raw_payload_sha256": self.raw_payload_sha256,
        }
