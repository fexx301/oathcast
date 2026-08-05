"""Development-only Script Author benchmark and adversarial corpus runner.

This module deliberately does not emulate or claim Telegraph's Canonical
Script.  It compares the current local semantic proxy with a stricter,
transparent candidate on a fixed corpus so we can measure local improvement,
robustness, and failure modes before the official WASM harness is released.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from oathcast.reference_evaluator import ReferenceEvaluation, evaluate_reference, normalize_response


BENCHMARK_VERSION = "script_author_adversarial_benchmark_v1"
ROBUST_EVALUATOR_VERSION = "development_robust_semantic_proxy_v1"
DEFAULT_GOOD_SCORE_THRESHOLD = 0.55
DEFAULT_MAX_RESPONSE_CHARS = 4096
TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?", re.IGNORECASE)
TIME_PATTERN = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "be",
        "between",
        "by",
        "did",
        "during",
        "for",
        "from",
        "greater",
        "in",
        "is",
        "it",
        "less",
        "mm",
        "no",
        "occur",
        "occurred",
        "of",
        "on",
        "or",
        "over",
        "probability",
        "requested",
        "the",
        "to",
        "was",
        "weather",
        "will",
        "yes",
    }
)


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def _semantic_tokens(text: str) -> set[str]:
    return {token for token in _tokens(text) if token not in STOPWORDS}


def _probability(raw_response: Any, text: str) -> float | None:
    if isinstance(raw_response, Mapping):
        for key in (
            "probability",
            "precipitation_probability",
            "probability_of_precipitation",
            "rain_probability",
            "pop",
        ):
            value = raw_response.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if 0 <= number <= 1:
                    return number
                if 1 < number <= 100:
                    return number / 100
    match = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%", text)
    if match is None:
        return None
    number = float(match.group(1))
    return number / 100 if 0 <= number <= 100 else None


def _explicit_polarity(text: str) -> int | None | str:
    """Return 1/0, None when unknown, or a contradiction marker."""

    lowered = text.lower()
    negative = bool(
        re.search(
            r"\bno\b|\bdid\s+not\b|\bdidn['’]t\b|\bwill\s+not\b|\bwon['’]t\b|"
            r"\bnot\s+occur\w*\b|\bunlikely\b|\bfalse\b",
            lowered,
        )
    )
    positive = bool(
        re.search(
            r"\byes\b|\btrue\b|\bwill\s+occur\w*\b|\blikely\b|\boccurred\b",
            lowered,
        )
    )
    # “No ... occurred” is negative, not contradictory.  An explicit Yes/No
    # pair or a positive future assertion plus a negative assertion is a
    # contradiction that should not receive semantic credit.
    if negative and positive:
        explicit_positive = bool(
            re.search(r"\byes\b|\btrue\b|\bwill\s+occur\w*\b|\blikely\b", lowered)
        )
        if not explicit_positive:
            return 0
        return "contradictory_polarity"
    if negative:
        return 0
    if positive:
        return 1
    return None


def _materialize_fixture_response(raw_response: Any) -> Any:
    """Expand the corpus' compact repeat form into a real raw response."""

    if not isinstance(raw_response, Mapping) or "_fixture_repeat" not in raw_response:
        return raw_response
    repeat = raw_response.get("_fixture_repeat")
    if not isinstance(repeat, Mapping):
        raise ValueError("_fixture_repeat must be an object")
    value = repeat.get("value")
    count = repeat.get("count")
    if not isinstance(value, str) or isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("_fixture_repeat requires string value and integer count")
    if count < 0 or count > 100_000:
        raise ValueError("_fixture_repeat count is outside the safe fixture bound")
    return value * count


@dataclass(frozen=True)
class ScriptBenchmarkCase:
    case_id: str
    question: str
    ground_truth: str
    raw_response: Any
    case_class: str
    expected_good: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScriptBenchmarkCase":
        case_id = str(data.get("case_id", ""))
        case_class = str(data.get("case_class", ""))
        if not case_id.strip():
            raise ValueError("benchmark case_id is required")
        if case_class not in {"good", "bad", "adversarial", "invalid"}:
            raise ValueError(f"unsupported benchmark case_class: {case_class}")
        return cls(
            case_id=case_id,
            question=str(data.get("question", "")),
            ground_truth=str(data.get("ground_truth", "")),
            raw_response=_materialize_fixture_response(data.get("raw_response")),
            case_class=case_class,
            expected_good=bool(data.get("expected_good", case_class == "good")),
        )


@dataclass(frozen=True)
class RobustEvaluation:
    score: float
    response_text: str
    valid: bool
    issues: tuple[str, ...]
    algorithm: str = ROBUST_EVALUATOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "response_text": self.response_text,
            "valid": self.valid,
            "issues": list(self.issues),
            "algorithm": self.algorithm,
        }


def evaluate_robust_reference(
    question: str,
    ground_truth: str,
    raw_response: Any,
    *,
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS,
) -> RobustEvaluation:
    """Score one response with explicit anti-gaming diagnostics.

    The candidate is intentionally conservative: malformed, contradictory,
    wrong-window, stuffed, empty, and overlong answers receive zero.  A valid
    response receives a bounded blend of semantic F1, outcome consistency, and
    concision.  It is a development diagnostic, not the official scorer.
    """

    response_value = _materialize_fixture_response(raw_response)
    response_text = "" if response_value is None else normalize_response(response_value)
    issues: list[str] = []
    if not response_text:
        issues.append("empty_response")
    if len(response_text) > max_response_chars:
        issues.append("response_too_long")
    if isinstance(response_value, str):
        stripped = response_value.strip()
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                issues.append("malformed_json_like_response")
    if not ground_truth.strip():
        issues.append("empty_ground_truth")

    response_tokens = _semantic_tokens(response_text)
    truth_tokens = _semantic_tokens(ground_truth)
    if not truth_tokens:
        issues.append("empty_ground_truth_tokens")

    response_times = set(TIME_PATTERN.findall(response_text))
    question_times = set(TIME_PATTERN.findall(question))
    if response_times and question_times and not response_times.issubset(question_times):
        issues.append("wrong_time_window")

    counts = Counter(_tokens(response_text))
    token_count = sum(counts.values())
    unique_ratio = (len(counts) / token_count) if token_count else 1.0
    if token_count >= 36 and unique_ratio < 0.45 and max(counts.values(), default=0) >= 6:
        issues.append("keyword_stuffing")

    truth_polarity = _explicit_polarity(ground_truth)
    response_polarity = _explicit_polarity(response_text)
    probability = _probability(response_value, response_text)
    if response_polarity == "contradictory_polarity":
        issues.append("contradictory_polarity")
        response_polarity = None
    if probability is not None and response_polarity in (0, 1):
        implied_polarity = int(probability >= 0.5)
        if implied_polarity != response_polarity:
            issues.append("contradictory_probability")
    if response_polarity is None and probability is not None:
        response_polarity = int(probability >= 0.5)
    if truth_polarity == "contradictory_polarity":
        issues.append("ambiguous_ground_truth")
        truth_polarity = None
    if (
        truth_polarity in (0, 1)
        and response_polarity in (0, 1)
        and truth_polarity != response_polarity
    ):
        issues.append("polarity_mismatch")

    fatal = {
        "empty_response",
        "response_too_long",
        "malformed_json_like_response",
        "empty_ground_truth",
        "empty_ground_truth_tokens",
        "wrong_time_window",
        "keyword_stuffing",
        "contradictory_polarity",
        "contradictory_probability",
        "ambiguous_ground_truth",
        "polarity_mismatch",
    }
    if any(issue in fatal for issue in issues):
        return RobustEvaluation(0.0, response_text, False, tuple(dict.fromkeys(issues)))

    if not response_tokens or not truth_tokens:
        overlap_f1 = 0.0
    else:
        overlap = len(response_tokens & truth_tokens)
        precision = overlap / len(response_tokens)
        recall = overlap / len(truth_tokens)
        overlap_f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0

    if truth_polarity in (0, 1) and response_polarity in (0, 1):
        polarity_quality = 1.0 if truth_polarity == response_polarity else 0.0
    else:
        polarity_quality = 0.5
    concision_quality = max(
        0.0,
        min(1.0, 1.0 - max(0, len(response_text) - 240) / max(1, max_response_chars - 240)),
    )
    score = round(
        max(0.0, min(1.0, (0.55 * overlap_f1) + (0.30 * polarity_quality) + (0.15 * concision_quality))),
        6,
    )
    return RobustEvaluation(score, response_text, True, tuple(dict.fromkeys(issues)))


@dataclass(frozen=True)
class ScriptBenchmarkSummary:
    total_cases: int
    good_cases: int
    baseline_behavior_accuracy: float
    candidate_behavior_accuracy: float
    behavior_accuracy_improvement: float
    baseline_mean_score: float
    candidate_mean_score: float
    candidate_mean_score_delta: float
    candidate_good_case_pass_rate: float
    candidate_adversarial_rejection_rate: float
    baseline_adversarial_accepts: int
    candidate_adversarial_accepts: int
    score_bounds_ok: bool
    issues_by_type: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "good_cases": self.good_cases,
            "baseline_behavior_accuracy": self.baseline_behavior_accuracy,
            "candidate_behavior_accuracy": self.candidate_behavior_accuracy,
            "behavior_accuracy_improvement": self.behavior_accuracy_improvement,
            "baseline_mean_score": self.baseline_mean_score,
            "candidate_mean_score": self.candidate_mean_score,
            "candidate_mean_score_delta": self.candidate_mean_score_delta,
            "candidate_good_case_pass_rate": self.candidate_good_case_pass_rate,
            "candidate_adversarial_rejection_rate": self.candidate_adversarial_rejection_rate,
            "baseline_adversarial_accepts": self.baseline_adversarial_accepts,
            "candidate_adversarial_accepts": self.candidate_adversarial_accepts,
            "score_bounds_ok": self.score_bounds_ok,
            "issues_by_type": dict(sorted(self.issues_by_type.items())),
        }


def _expected_pass(case: ScriptBenchmarkCase, evaluation: ReferenceEvaluation | RobustEvaluation, threshold: float) -> bool:
    if case.expected_good:
        return evaluation.valid and evaluation.score >= threshold
    return (not evaluation.valid) or evaluation.score < threshold


def run_script_benchmark(
    cases: Sequence[ScriptBenchmarkCase],
    *,
    good_score_threshold: float = DEFAULT_GOOD_SCORE_THRESHOLD,
) -> dict[str, Any]:
    if not 0 < good_score_threshold < 1:
        raise ValueError("good_score_threshold must be between 0 and 1")
    if not cases:
        raise ValueError("benchmark requires at least one case")

    results: list[dict[str, Any]] = []
    baseline_passes = 0
    candidate_passes = 0
    baseline_adversarial_accepts = 0
    candidate_adversarial_accepts = 0
    candidate_good_passes = 0
    candidate_adversarial_rejections = 0
    adversarial_total = 0
    baseline_scores: list[float] = []
    candidate_scores: list[float] = []
    issues_by_type: Counter[str] = Counter()

    for case in cases:
        baseline = evaluate_reference(case.question, case.ground_truth, case.raw_response)
        candidate = evaluate_robust_reference(case.question, case.ground_truth, case.raw_response)
        baseline_pass = _expected_pass(case, baseline, good_score_threshold)
        candidate_pass = _expected_pass(case, candidate, good_score_threshold)
        baseline_passes += int(baseline_pass)
        candidate_passes += int(candidate_pass)
        baseline_scores.append(baseline.score)
        candidate_scores.append(candidate.score)
        if case.case_class in {"adversarial", "invalid"}:
            adversarial_total += 1
            baseline_adversarial_accepts += int(baseline.valid and baseline.score >= good_score_threshold)
            candidate_adversarial_accepts += int(candidate.valid and candidate.score >= good_score_threshold)
            candidate_adversarial_rejections += int(not candidate.valid)
        if case.expected_good:
            candidate_good_passes += int(candidate_pass)
        for issue in candidate.issues:
            issues_by_type[issue] += 1
        results.append(
            {
                "case_id": case.case_id,
                "case_class": case.case_class,
                "expected_good": case.expected_good,
                "baseline": baseline.to_dict(),
                "candidate": candidate.to_dict(),
                "baseline_expected_behavior": baseline_pass,
                "candidate_expected_behavior": candidate_pass,
            }
        )

    total = len(cases)
    baseline_accuracy = baseline_passes / total
    candidate_accuracy = candidate_passes / total
    candidate_mean = sum(candidate_scores) / total
    baseline_mean = sum(baseline_scores) / total
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "official_status": "development_only_not_telegraph_canonical_script",
        "scoring_lanes": {
            "baseline": "existing_development_reference_proxy",
            "candidate": ROBUST_EVALUATOR_VERSION,
            "brier": "separate_domain_benchmark_not_included_here",
        },
        "thresholds": {"good_score_threshold": good_score_threshold},
        "summary": ScriptBenchmarkSummary(
            total_cases=total,
            good_cases=sum(int(case.expected_good) for case in cases),
            baseline_behavior_accuracy=round(baseline_accuracy, 6),
            candidate_behavior_accuracy=round(candidate_accuracy, 6),
            behavior_accuracy_improvement=round(candidate_accuracy - baseline_accuracy, 6),
            baseline_mean_score=round(baseline_mean, 6),
            candidate_mean_score=round(candidate_mean, 6),
            candidate_mean_score_delta=round(candidate_mean - baseline_mean, 6),
            candidate_good_case_pass_rate=round(
                candidate_good_passes / max(1, sum(int(case.expected_good) for case in cases)), 6
            ),
            candidate_adversarial_rejection_rate=round(
                candidate_adversarial_rejections / max(1, adversarial_total), 6
            ),
            baseline_adversarial_accepts=baseline_adversarial_accepts,
            candidate_adversarial_accepts=candidate_adversarial_accepts,
            score_bounds_ok=all(0 <= score <= 1 for score in [*baseline_scores, *candidate_scores]),
            issues_by_type=dict(issues_by_type),
        ).to_dict(),
        "cases": results,
    }


def load_script_benchmark_cases(path: str) -> tuple[list[ScriptBenchmarkCase], str]:
    raw = Path(path).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark fixture must be a JSON list")
    return [ScriptBenchmarkCase.from_dict(item) for item in data], hashlib.sha256(raw).hexdigest()
