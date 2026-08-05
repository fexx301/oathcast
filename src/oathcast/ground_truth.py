"""Independent observation and resolution contracts for OathCast cases.

This module intentionally stops at a provider-neutral observation boundary.
The future live source can be a station/archive adapter, but every source must
return the exact UTC one-hour window before it can resolve a forecast. Missing
or malformed observations are explicit outcomes, never silently coerced into a
negative event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Protocol

from oathcast.forecast import ForecastQuestion, ensure_utc, format_timestamp, parse_timestamp


UTC = timezone.utc
GROUND_TRUTH_STATUSES = frozenset({"resolved", "missing", "invalid"})
PRECIPITATION_THRESHOLD_MICROMETRES = 100


class ObservationSource(Protocol):
    def observe(self, question: ForecastQuestion) -> "PrecipitationObservation | None":
        """Return the exact observation window, or None when it is unavailable."""


@dataclass(frozen=True)
class PrecipitationObservation:
    """An independently obtained one-hour precipitation observation."""

    event_id: str
    latitude: float
    longitude: float
    window_start: datetime
    window_end: datetime
    precipitation_mm: float
    source: str
    observation_id: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("observation event_id must not be empty")
        if not -90 <= self.latitude <= 90:
            raise ValueError("observation latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("observation longitude must be between -180 and 180")
        if not math.isfinite(self.precipitation_mm) or self.precipitation_mm < 0:
            raise ValueError("precipitation_mm must be finite and non-negative")
        if not self.source.strip():
            raise ValueError("observation source must not be empty")
        if not self.observation_id.strip():
            raise ValueError("observation_id must not be empty")

        start = ensure_utc(self.window_start, "window_start")
        end = ensure_utc(self.window_end, "window_end")
        observed_at = ensure_utc(self.observed_at, "observed_at")
        if end - start != timedelta(hours=1):
            raise ValueError("ground-truth observations must cover exactly one hour")
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "window_end", end)
        object.__setattr__(self, "observed_at", observed_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PrecipitationObservation":
        return cls(
            event_id=str(data["event_id"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            window_start=parse_timestamp(data["window_start"]),
            window_end=parse_timestamp(data["window_end"]),
            precipitation_mm=float(data["precipitation_mm"]),
            source=str(data["source"]),
            observation_id=str(data["observation_id"]),
            observed_at=parse_timestamp(data["observed_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "window_start": format_timestamp(self.window_start),
            "window_end": format_timestamp(self.window_end),
            "precipitation_mm": self.precipitation_mm,
            "precipitation_micrometres": self.precipitation_micrometres,
            "source": self.source,
            "observation_id": self.observation_id,
            "observed_at": format_timestamp(self.observed_at),
        }

    @property
    def precipitation_micrometres(self) -> int:
        """Return the stored measurement at the resolver's integer precision."""

        return int(round(self.precipitation_mm * 1000))


@dataclass(frozen=True)
class GroundTruthResult:
    event_id: str
    status: str
    outcome: int | None
    source: str | None
    observation_id: str | None
    precipitation_mm: float | None
    resolved_at: datetime
    issue: str | None = None
    precipitation_micrometres: int | None = None

    def __post_init__(self) -> None:
        if self.status not in GROUND_TRUTH_STATUSES:
            raise ValueError(f"unsupported ground-truth status: {self.status}")
        if self.status == "resolved" and self.outcome not in (0, 1):
            raise ValueError("resolved ground truth must have a binary outcome")
        if self.status != "resolved" and self.outcome is not None:
            raise ValueError("unresolved ground truth must not have an outcome")
        if self.precipitation_micrometres is not None and self.precipitation_micrometres < 0:
            raise ValueError("precipitation_micrometres must be non-negative")
        if self.status == "missing" and self.issue != "observation_missing":
            raise ValueError("missing ground truth must identify observation_missing")
        if not self.event_id.strip():
            raise ValueError("ground-truth event_id must not be empty")
        object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at, "resolved_at"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroundTruthResult":
        return cls(
            event_id=str(data["event_id"]),
            status=str(data["status"]),
            outcome=None if data.get("outcome") is None else int(data["outcome"]),
            source=data.get("source"),
            observation_id=data.get("observation_id"),
            precipitation_mm=(
                None
                if data.get("precipitation_mm") is None
                else float(data["precipitation_mm"])
            ),
            resolved_at=parse_timestamp(data["resolved_at"]),
            issue=data.get("issue"),
            precipitation_micrometres=(
                None
                if data.get("precipitation_micrometres") is None
                else int(data["precipitation_micrometres"])
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "outcome": self.outcome,
            "source": self.source,
            "observation_id": self.observation_id,
            "precipitation_mm": self.precipitation_mm,
            "precipitation_micrometres": self.precipitation_micrometres,
            "resolved_at": format_timestamp(self.resolved_at),
            "issue": self.issue,
        }


def _result(
    question: ForecastQuestion,
    *,
    status: str,
    outcome: int | None,
    source: str | None,
    observation_id: str | None,
    precipitation_mm: float | None,
    precipitation_micrometres: int | None,
    resolved_at: datetime,
    issue: str | None,
) -> GroundTruthResult:
    return GroundTruthResult(
        event_id=question.event_id,
        status=status,
        outcome=outcome,
        source=source,
        observation_id=observation_id,
        precipitation_mm=precipitation_mm,
        precipitation_micrometres=precipitation_micrometres,
        resolved_at=resolved_at,
        issue=issue,
    )


def resolve_precipitation(
    question: ForecastQuestion,
    observation: PrecipitationObservation | None,
    *,
    resolved_at: datetime,
) -> GroundTruthResult:
    """Resolve the exact provider-native event without guessing or leakage."""

    resolved_at_utc = ensure_utc(resolved_at, "resolved_at")
    if observation is None:
        return _result(
            question,
            status="missing",
            outcome=None,
            source=None,
            observation_id=None,
            precipitation_mm=None,
            precipitation_micrometres=None,
            resolved_at=resolved_at_utc,
            issue="observation_missing",
        )

    if observation.event_id != question.event_id:
        issue = "event_id_mismatch"
    elif abs(observation.latitude - question.latitude) > 1e-4:
        issue = "latitude_mismatch"
    elif abs(observation.longitude - question.longitude) > 1e-4:
        issue = "longitude_mismatch"
    elif observation.window_start != question.horizon_start:
        issue = "window_start_mismatch"
    elif observation.window_end != question.horizon_end:
        issue = "window_end_mismatch"
    elif observation.observed_at < question.horizon_end:
        issue = "observation_predates_event_end"
    else:
        issue = None

    if issue is not None:
        return _result(
            question,
            status="invalid",
            outcome=None,
            source=observation.source,
            observation_id=observation.observation_id,
            precipitation_mm=None,
            precipitation_micrometres=None,
            resolved_at=resolved_at_utc,
            issue=issue,
        )

    precipitation_micrometres = observation.precipitation_micrometres
    return _result(
        question,
        status="resolved",
        outcome=int(precipitation_micrometres > PRECIPITATION_THRESHOLD_MICROMETRES),
        source=observation.source,
        observation_id=observation.observation_id,
        precipitation_mm=observation.precipitation_mm,
        precipitation_micrometres=precipitation_micrometres,
        resolved_at=resolved_at_utc,
        issue=None,
    )


class MappingObservationSource:
    """Deterministic development source; never presented as live ground truth."""

    def __init__(self, observations: dict[str, PrecipitationObservation]) -> None:
        self.observations = dict(observations)

    def observe(self, question: ForecastQuestion) -> PrecipitationObservation | None:
        return self.observations.get(question.event_id)
