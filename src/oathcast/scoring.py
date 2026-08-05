"""Leakage-safe local Brier benchmark.

This module is a domain-quality benchmark, not a claim about Telegraph's
current cosine/BM25/length semantic scorer.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


VALID_STATUS = "valid"
NON_VALID_STATUSES = frozenset({"late", "missing", "invalid", "abstained"})


def brier_loss(probability: float, outcome: int) -> float:
    """Return squared probability error; lower is better."""

    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("probability must be a finite number in [0, 1]")
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    return (probability - outcome) ** 2


def brier_skill_score(model_brier: float, baseline_brier: float) -> float | None:
    """Return skill against a baseline; negative values are intentionally preserved."""

    if baseline_brier < 0 or model_brier < 0:
        raise ValueError("Brier scores cannot be negative")
    if baseline_brier == 0:
        return None
    return 1 - (model_brier / baseline_brier)


@dataclass(frozen=True)
class BrierCase:
    case_id: str
    probability: float | None
    outcome: int | None
    climatology_probability: float
    status: str = VALID_STATUS

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not math.isfinite(self.climatology_probability) or not 0 <= self.climatology_probability <= 1:
            raise ValueError("climatology_probability must be a finite number in [0, 1]")
        if self.status not in {VALID_STATUS, *NON_VALID_STATUSES}:
            raise ValueError(f"unsupported case status: {self.status}")

    @property
    def is_valid(self) -> bool:
        return (
            self.status == VALID_STATUS
            and self.outcome in (0, 1)
            and self.probability is not None
            and math.isfinite(self.probability)
            and 0 <= self.probability <= 1
        )


@dataclass(frozen=True)
class BrierSummary:
    total_cases: int
    valid_cases: int
    coverage: float
    brier_score: float | None
    brier_quality: float | None
    baseline_brier_score: float | None
    brier_skill_score: float | None
    end_to_end_score: float
    invalid_cases_by_status: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "valid_cases": self.valid_cases,
            "coverage": self.coverage,
            "brier_score": self.brier_score,
            "brier_quality": self.brier_quality,
            "baseline_brier_score": self.baseline_brier_score,
            "brier_skill_score": self.brier_skill_score,
            "end_to_end_score": self.end_to_end_score,
            "invalid_cases_by_status": dict(sorted(self.invalid_cases_by_status.items())),
        }


def score_attempt(case: BrierCase) -> float:
    """Return a bounded per-attempt quality, assigning zero to non-valid attempts."""

    if not case.is_valid:
        return 0.0
    assert case.probability is not None
    assert case.outcome in (0, 1)
    return 1 - brier_loss(case.probability, case.outcome)


def evaluate_brier(cases: list[BrierCase]) -> BrierSummary:
    """Evaluate valid-case Brier, baseline skill, coverage, and end-to-end quality."""

    total = len(cases)
    valid = [case for case in cases if case.is_valid]
    invalid_counts: dict[str, int] = {}
    for case in cases:
        if not case.is_valid:
            invalid_counts[case.status] = invalid_counts.get(case.status, 0) + 1

    if valid:
        model_losses = [
            brier_loss(case.probability, case.outcome)  # type: ignore[arg-type]
            for case in valid
        ]
        baseline_losses = [
            brier_loss(case.climatology_probability, case.outcome)  # type: ignore[arg-type]
            for case in valid
        ]
        model_brier = sum(model_losses) / len(model_losses)
        baseline_brier = sum(baseline_losses) / len(baseline_losses)
        quality = 1 - model_brier
        skill = brier_skill_score(model_brier, baseline_brier)
    else:
        model_brier = None
        baseline_brier = None
        quality = None
        skill = None

    end_to_end = sum(score_attempt(case) for case in cases) / total if total else 0.0
    return BrierSummary(
        total_cases=total,
        valid_cases=len(valid),
        coverage=(len(valid) / total) if total else 0.0,
        brier_score=model_brier,
        brier_quality=quality,
        baseline_brier_score=baseline_brier,
        brier_skill_score=skill,
        end_to_end_score=end_to_end,
        invalid_cases_by_status=invalid_counts,
    )
