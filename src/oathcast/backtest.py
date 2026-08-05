"""Leakage-safe chronological weather-provider backtesting.

This module evaluates development fixtures in time order.  It deliberately
keeps the warmup prefix separate from the later holdout and makes any
prequential provider choice using only outcomes whose resolution was visible
by the current issue time.  It batches simultaneous issue times.  It is not a
source of live-provider performance claims and it does not implement
Telegraph's Script Author scorer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from oathcast.forecast import format_timestamp, parse_timestamp
from oathcast.scoring import (
    BrierCase,
    NON_VALID_STATUSES,
    VALID_STATUS,
    evaluate_brier,
    score_attempt,
)


BACKTEST_VERSION = "chronological_provider_backtest_v1"
OFFICIAL_STATUS = "development_only_synthetic_not_live_provider_performance"


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


@dataclass(frozen=True)
class ProviderForecast:
    """One provider attempt attached to a chronological case."""

    probability: float | None
    status: str = VALID_STATUS

    def __post_init__(self) -> None:
        if self.status not in {VALID_STATUS, *NON_VALID_STATUSES}:
            raise ValueError(f"unsupported provider forecast status: {self.status}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderForecast":
        return cls(
            probability=(
                None
                if data.get("probability") is None
                else float(data["probability"])
            ),
            status=str(data.get("status", VALID_STATUS)),
        )


@dataclass(frozen=True)
class ChronologicalCase:
    """A timestamped binary event and the provider forecasts for it."""

    case_id: str
    issued_at: datetime
    forecast_cutoff: datetime
    horizon_start: datetime
    horizon_end: datetime
    outcome: int | None
    resolved_at: datetime | None
    climatology_probability: float
    forecasts: Mapping[str, ProviderForecast]

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if self.outcome is not None and (
            type(self.outcome) is not int or self.outcome not in (0, 1)
        ):
            raise ValueError("outcome must be the integer 0 or 1 or None")
        if (
            not math.isfinite(self.climatology_probability)
            or not 0 <= self.climatology_probability <= 1
        ):
            raise ValueError("climatology_probability must be finite and in [0, 1]")
        if not self.forecasts:
            raise ValueError("at least one provider forecast is required")
        for provider in self.forecasts:
            if not str(provider).strip():
                raise ValueError("provider names must not be empty")

        issued_at = parse_timestamp(self.issued_at)
        forecast_cutoff = parse_timestamp(self.forecast_cutoff)
        horizon_start = parse_timestamp(self.horizon_start)
        horizon_end = parse_timestamp(self.horizon_end)
        resolved_at = (
            None if self.resolved_at is None else parse_timestamp(self.resolved_at)
        )
        if issued_at > forecast_cutoff:
            raise ValueError("issued_at cannot be after forecast_cutoff")
        if forecast_cutoff >= horizon_start:
            raise ValueError("forecast_cutoff must be before horizon_start")
        if horizon_end <= horizon_start:
            raise ValueError("horizon_end must be after horizon_start")
        if (self.outcome is None) != (resolved_at is None):
            raise ValueError("outcome and resolved_at must be both present or both None")
        if resolved_at is not None and resolved_at < horizon_end:
            raise ValueError("resolved_at cannot be before horizon_end")

        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "forecast_cutoff", forecast_cutoff)
        object.__setattr__(self, "horizon_start", horizon_start)
        object.__setattr__(self, "horizon_end", horizon_end)
        object.__setattr__(self, "resolved_at", resolved_at)
        object.__setattr__(self, "forecasts", dict(self.forecasts))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChronologicalCase":
        raw_forecasts = data.get("forecasts")
        if not isinstance(raw_forecasts, Mapping):
            raise ValueError(f"{data.get('case_id', '<unknown>')} forecasts must be an object")
        return cls(
            case_id=str(data["case_id"]),
            issued_at=parse_timestamp(data["issued_at"]),
            forecast_cutoff=parse_timestamp(data["forecast_cutoff"]),
            horizon_start=parse_timestamp(data["horizon_start"]),
            horizon_end=parse_timestamp(data["horizon_end"]),
            outcome=(None if data.get("outcome") is None else int(data["outcome"])),
            resolved_at=(
                None
                if data.get("resolved_at") is None
                else parse_timestamp(data["resolved_at"])
            ),
            climatology_probability=float(data["climatology_probability"]),
            forecasts={
                str(provider): ProviderForecast.from_dict(forecast)
                for provider, forecast in raw_forecasts.items()
            },
        )

    def to_brier_case(self, provider: str) -> BrierCase:
        attempt = self.forecasts.get(provider, ProviderForecast(None, status="missing"))
        status = attempt.status
        if self.outcome is None and status == VALID_STATUS:
            status = "missing"
        return BrierCase(
            case_id=self.case_id,
            probability=attempt.probability,
            outcome=self.outcome,
            climatology_probability=self.climatology_probability,
            status=status,
        )


def validate_chronology(cases: Sequence[ChronologicalCase]) -> None:
    """Reject duplicate IDs, unsorted input, or mixed forecast horizons.

    Equal issued timestamps are allowed only in case-ID order.  The runner
    batches those cases so they cannot influence one another.
    """

    if not cases:
        raise ValueError("chronological backtest requires at least one case")
    seen: set[str] = set()
    previous_issued_at: datetime | None = None
    previous_case_id: str | None = None
    horizon_duration: timedelta | None = None
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate chronological case_id: {case.case_id}")
        seen.add(case.case_id)
        duration = case.horizon_end - case.horizon_start
        if horizon_duration is None:
            horizon_duration = duration
        elif duration != horizon_duration:
            raise ValueError("mixed forecast horizons are not supported")
        if previous_issued_at is not None and case.issued_at < previous_issued_at:
            raise ValueError("cases must be ordered by nondecreasing issued_at")
        if (
            previous_issued_at is not None
            and case.issued_at == previous_issued_at
            and previous_case_id is not None
            and case.case_id <= previous_case_id
        ):
            raise ValueError("equal-issued-at cases must be ordered by case_id")
        previous_issued_at = case.issued_at
        previous_case_id = case.case_id


def _provider_names(cases: Sequence[ChronologicalCase]) -> list[str]:
    return sorted({provider for case in cases for provider in case.forecasts})


def _resolved_cases(cases: Sequence[ChronologicalCase]) -> list[ChronologicalCase]:
    return [case for case in cases if case.resolved_at is not None]


def _provider_summary(
    cases: Sequence[ChronologicalCase],
    provider: str,
    common_cases: Sequence[ChronologicalCase],
) -> dict[str, Any]:
    summary = evaluate_brier(
        [case.to_brier_case(provider) for case in _resolved_cases(cases)]
    ).to_dict()
    common_summary = evaluate_brier(
        [case.to_brier_case(provider) for case in common_cases]
    ).to_dict()
    summary["common_case_brier"] = common_summary["brier_score"]
    summary["common_case_count"] = len(common_cases)
    return _rounded(summary)


def _prior_profile(
    history: Sequence[BrierCase],
    *,
    min_history_valid_cases: int,
) -> dict[str, Any] | None:
    valid = [case for case in history if case.is_valid]
    if len(valid) < min_history_valid_cases:
        return None
    summary = evaluate_brier(list(history)).to_dict()
    return _rounded(
        {
            "total_cases": len(history),
            "valid_cases": len(valid),
            "coverage": len(valid) / len(history),
            "brier_score": summary["brier_score"],
            "end_to_end_score": summary["end_to_end_score"],
        }
    )


def _choose_provider(
    histories: Mapping[str, Sequence[BrierCase]],
    *,
    min_history_valid_cases: int,
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    profiles = {
        provider: profile
        for provider, history in histories.items()
        if (profile := _prior_profile(history, min_history_valid_cases=min_history_valid_cases))
        is not None
    }
    if not profiles:
        return None, {}

    # Higher prior end-to-end quality rewards both accuracy and coverage.  The
    # lower prior Brier score and provider name are deterministic tie-breakers.
    selected = min(
        profiles,
        key=lambda provider: (
            -float(profiles[provider]["end_to_end_score"]),
            float(profiles[provider]["brier_score"]),
            provider,
        ),
    )
    return selected, profiles


def _attempt_dict(attempt: BrierCase | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {
        "case_id": attempt.case_id,
        "probability": attempt.probability,
        "outcome": attempt.outcome,
        "status": attempt.status,
        "valid": attempt.is_valid,
        "score": round(score_attempt(attempt), 6),
    }


def run_chronological_backtest(
    cases: Sequence[ChronologicalCase],
    *,
    warmup_cases: int = 3,
    min_history_valid_cases: int = 3,
) -> dict[str, Any]:
    """Run provider summaries and a prior-only prequential selector."""

    validate_chronology(cases)
    total = len(cases)
    if not 0 <= warmup_cases < total:
        raise ValueError("warmup_cases must leave at least one holdout case")
    if min_history_valid_cases < 1:
        raise ValueError("min_history_valid_cases must be at least 1")

    provider_names = _provider_names(cases)
    warmup = list(cases[:warmup_cases])
    holdout = list(cases[warmup_cases:])
    selection_trace: list[dict[str, Any]] = []
    selected_holdout_attempts: list[BrierCase] = []
    selected_provider_counts: Counter[str] = Counter()

    selected_holdout_decisions = 0
    index = 0
    while index < total:
        issued_at = cases[index].issued_at
        batch_end = index + 1
        while batch_end < total and cases[batch_end].issued_at == issued_at:
            batch_end += 1

        # A prior forecast is available to the selector only after its outcome
        # was resolved, and only if that resolution was visible by this batch's
        # issue time.  Same-time cases are intentionally not included here.
        available_prior = [
            prior
            for prior in cases[:index]
            if prior.resolved_at is not None and prior.resolved_at <= issued_at
        ]
        histories = {
            provider: [prior.to_brier_case(provider) for prior in available_prior]
            for provider in provider_names
        }
        for case_index in range(index, batch_end):
            case = cases[case_index]
            selected, profiles = _choose_provider(
                histories,
                min_history_valid_cases=min_history_valid_cases,
            )
            selected_attempt = None if selected is None else case.to_brier_case(selected)
            is_holdout = case_index >= warmup_cases
            if is_holdout and selected is not None:
                selected_holdout_decisions += 1
                if case.resolved_at is not None:
                    selected_holdout_attempts.append(selected_attempt)  # type: ignore[arg-type]
                    selected_provider_counts[selected] += 1

            selection_trace.append(
                {
                    "case_id": case.case_id,
                    "issued_at": format_timestamp(case.issued_at),
                    "phase": "holdout" if is_holdout else "warmup",
                    "history_cases_before_decision": len(available_prior),
                    "available_history_through_resolved_at": (
                        None
                        if not available_prior
                        else format_timestamp(
                            max(prior.resolved_at for prior in available_prior)  # type: ignore[arg-type]
                        )
                    ),
                    "simultaneous_timestamp_batch_size": batch_end - index,
                    "eligible_providers": sorted(profiles),
                    "prior_profiles": profiles,
                    "selected_provider": selected,
                    "selected_attempt_after_resolution": _attempt_dict(selected_attempt),
                }
            )
        index = batch_end

    selection_summary = evaluate_brier(selected_holdout_attempts).to_dict()
    selection_summary = _rounded(selection_summary)
    resolved_warmup = _resolved_cases(warmup)
    resolved_holdout = _resolved_cases(holdout)
    common_warmup = [
        case
        for case in resolved_warmup
        if all(case.to_brier_case(provider).is_valid for provider in provider_names)
    ]
    common_holdout = [
        case
        for case in resolved_holdout
        if all(case.to_brier_case(provider).is_valid for provider in provider_names)
    ]
    common_all = [
        case
        for case in _resolved_cases(cases)
        if all(case.to_brier_case(provider).is_valid for provider in provider_names)
    ]
    return {
        "backtest_version": BACKTEST_VERSION,
        "official_status": OFFICIAL_STATUS,
        "selection_policy": {
            "warmup_cases": warmup_cases,
            "min_history_valid_cases": min_history_valid_cases,
            "primary_metric": "prior_end_to_end_score",
            "tie_breakers": ["lower_prior_brier_score", "provider_name_ascending"],
            "history_rule": "resolved_at <= current issued_at",
            "simultaneous_timestamp_batching": True,
            "policy_frozen_before_holdout": True,
            "unresolved_case_policy": "exclude from scoring and future history until resolved",
            "current_outcome_used_for_current_selection": False,
        },
        "metric_definitions": {
            "conditional_brier": "mean((probability - outcome)^2) over valid attempts only; lower is better",
            "coverage": "valid attempts divided by resolved eligible cases",
            "end_to_end_score": "sum(valid * (1 - brier_loss)) divided by all resolved eligible cases; invalid, late, missing, and abstained attempts contribute zero utility",
            "common_case_brier": "conditional Brier over the same cases where every provider has a valid forecast",
        },
        "dataset": {
            "total_cases": total,
            "warmup_cases": len(warmup),
            "holdout_cases": len(holdout),
            "resolved_cases": len(_resolved_cases(cases)),
            "unresolved_cases": total - len(_resolved_cases(cases)),
            "resolved_holdout_cases": len(resolved_holdout),
            "providers": provider_names,
            "first_issued_at": format_timestamp(cases[0].issued_at),
            "last_issued_at": format_timestamp(cases[-1].issued_at),
            "holdout_first_issued_at": format_timestamp(holdout[0].issued_at),
            "horizon_duration_hours": round(
                (cases[0].horizon_end - cases[0].horizon_start).total_seconds() / 3600,
                6,
            ),
        },
        "no_leakage_checks": {
            "nondecreasing_input_chronology": True,
            "unique_case_ids": True,
            "mixed_horizons_rejected": True,
            "resolution_timestamps_enforced": True,
            "simultaneous_issued_at_batched": True,
            "holdout_scored_separately": True,
            "provider_selection_uses_prior_resolved_cases_only": True,
            "unresolved_events_excluded_from_scoring_and_history": True,
            "current_outcome_used_for_current_selection": False,
        },
        "provider_summaries": {
            provider: {
                "warmup": _provider_summary(warmup, provider, common_warmup),
                "holdout": _provider_summary(holdout, provider, common_holdout),
                "all_cases_reference_only": _provider_summary(
                    list(cases), provider, common_all
                ),
            }
            for provider in provider_names
        },
        "prequential_selection": {
            "holdout_cases": len(holdout),
            "resolved_holdout_cases": len(resolved_holdout),
            "selected_decisions": selected_holdout_decisions,
            "selected_resolved_cases": len(selected_holdout_attempts),
            "selected_provider_counts": dict(sorted(selected_provider_counts.items())),
            "selected_holdout_summary": selection_summary,
        },
        "selection_trace": selection_trace,
    }


def load_chronological_cases(path: str) -> tuple[list[ChronologicalCase], str]:
    """Load and hash a JSON fixture without silently sorting it."""

    raw = Path(path).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("chronological backtest fixture must be a JSON list")
    cases = [ChronologicalCase.from_dict(item) for item in data]
    validate_chronology(cases)
    return cases, hashlib.sha256(raw).hexdigest()
