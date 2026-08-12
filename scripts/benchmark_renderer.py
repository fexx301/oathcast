#!/usr/bin/env python3
"""Compare public-text renderer variants against the local scorer proxies.

Telegraph's Canonical Script is not released. This harness therefore scores
candidate renderings with two *local development proxies* only:

1. `evaluate_reference` — the bounded overlap/length proxy that stands in for
   the documented cosine + BM25 + length-quality composite.
2. `evaluate_robust_reference` — the stricter anti-gaming candidate, used here
   purely as a guard: a rendering that trips any of its fatal issues is
   rejected regardless of how well it scores on the proxy.

Neither number is a Telegraph score, a WASM result, or qualifying traffic.
Treat improvements as directional evidence that a rendering answers the asked
question more completely, not as a predicted protocol score.

A forecast is scored after resolution, so each variant is evaluated against
both ground-truth branches and reported as an expected score under its own
stated probability. This prevents a variant from looking good only when the
high-probability branch happens to occur.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from oathcast.forecast import CanonicalForecast, ForecastQuestion
from oathcast.reference_evaluator import evaluate_reference
from oathcast.script_benchmark import evaluate_robust_reference
from oathcast.render import (
    calibrated_phrase,
    render_forecast_content,
    render_forecast_content_v1,
)


HARNESS_VERSION = "renderer_proxy_benchmark_v1"
GOOD_SCORE_THRESHOLD = 0.55

# Fatal issues from the anti-gaming candidate. Any of these disqualifies a
# variant outright; we are not trading integrity for proxy score.
DISQUALIFYING_ISSUES = frozenset(
    {
        "keyword_stuffing",
        "wrong_time_window",
        "contradictory_polarity",
        "contradictory_probability",
        "response_too_long",
        "malformed_json_like_response",
        "empty_response",
    }
)


def _natural_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%H:%M")


def _natural_date(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    return f"{utc.day} {utc.strftime('%B %Y')}"


def _percentage(probability: float) -> str:
    return f"{probability * 100:.2f}".rstrip("0").rstrip(".")


# `calibrated_phrase` now lives in `oathcast.render` and is imported above.
# Keeping a second copy here would let the benchmark drift away from the code
# it is supposed to be measuring.


# --- Candidate renderings -------------------------------------------------


def variant_v1_current(question: ForecastQuestion, forecast: CanonicalForecast) -> str:
    """The original renderer, imported rather than copied."""

    return render_forecast_content_v1(question, forecast)


def variant_natural_time(question: ForecastQuestion, forecast: CanonicalForecast) -> str:
    """Same sentence, but the window is stated in readable UTC clock time."""

    return (
        f"At {question.location_name}, the probability of {question.event_label} "
        f"from {_natural_time(question.horizon_start)} to "
        f"{_natural_time(question.horizon_end)} UTC on "
        f"{_natural_date(question.horizon_start)} is "
        f"{_percentage(forecast.probability)}%."
    )


def variant_answers_question(question: ForecastQuestion, forecast: CanonicalForecast) -> str:
    """Answer the question that was asked, then give the probability."""

    return (
        f"{question.event_label.capitalize()} is "
        f"{calibrated_phrase(forecast.probability)} in {question.location_name} "
        f"from {_natural_time(question.horizon_start)} to "
        f"{_natural_time(question.horizon_end)} UTC on "
        f"{_natural_date(question.horizon_start)}. Probability: "
        f"{_percentage(forecast.probability)}%."
    )


def variant_question_vocabulary(
    question: ForecastQuestion, forecast: CanonicalForecast
) -> str:
    """Answer in the question's own vocabulary, including the requested hour."""

    return (
        f"{question.event_label.capitalize()} is "
        f"{calibrated_phrase(forecast.probability)} in {question.location_name} "
        f"during the requested UTC hour, from {_natural_time(question.horizon_start)} "
        f"to {_natural_time(question.horizon_end)} UTC on "
        f"{_natural_date(question.horizon_start)}. Probability: "
        f"{_percentage(forecast.probability)}%."
    )


def variant_no_borrowed_phrase(
    question: ForecastQuestion, forecast: CanonicalForecast
) -> str:
    """The shipped v2 renderer, imported rather than copied.

    This was the ablation of `question_vocabulary` with the borrowed corpus
    phrase `during the requested UTC hour` removed. It scored *identically* to
    `question_vocabulary` on the F1 integrity lane, which showed the phrase
    added no meaning — it only inflated the recall-only proxy by echoing
    vocabulary this harness itself authored. Being shorter and free of
    self-referential wording, it was promoted to `render_forecast_content`.
    """

    return render_forecast_content(question, forecast)


def variant_resolution_concepts(
    question: ForecastQuestion, forecast: CanonicalForecast
) -> str:
    """Cover the concepts any resolution would state, not one author's sentence.

    A resolution names what was measured, where, over which window, and
    against which threshold. Stating those concepts is defensible on its own
    terms — it is what a forecast consumer needs — and it happens to overlap
    whatever vocabulary the real resolver uses, without copying any single
    phrasing.
    """

    return (
        f"{question.event_label.capitalize()} is "
        f"{calibrated_phrase(forecast.probability)} at the {question.location_name} "
        f"point in the hour from {_natural_time(question.horizon_start)} to "
        f"{_natural_time(question.horizon_end)} UTC on "
        f"{_natural_date(question.horizon_start)}. Resolution measures recorded "
        f"rainfall against the {question.threshold_mm:g} mm threshold. "
        f"Probability: {_percentage(forecast.probability)}%."
    )


VARIANTS: dict[str, Callable[[ForecastQuestion, CanonicalForecast], str]] = {
    "v1_current_shipped": variant_v1_current,
    "natural_time": variant_natural_time,
    "answers_question": variant_answers_question,
    "question_vocabulary": variant_question_vocabulary,
    "no_borrowed_phrase": variant_no_borrowed_phrase,
    "resolution_concepts": variant_resolution_concepts,
}


# --- Proxy question / ground-truth construction ---------------------------


def proxy_question_text(question: ForecastQuestion) -> str:
    """Approximate the asked question.

    Telegraph has not published the Weather Intent question format. This
    mirrors the phrasing already used by the fixed adversarial corpus so the
    two local benchmarks stay consistent with each other.
    """

    return (
        f"Will {question.event_label} occur in {question.location_name} "
        f"from {_natural_time(question.horizon_start)} to "
        f"{_natural_time(question.horizon_end)} UTC?"
    )


def proxy_ground_truth(question: ForecastQuestion, *, occurred: bool) -> dict[str, str]:
    """Approximate resolved ground truth for one outcome branch.

    Returns several paraphrases rather than one string. Scoring against a
    single phrasing is circular: a candidate rendering that happens to reuse
    that phrasing scores well for reasons that would not survive contact with
    the real format. Averaging across deliberately varied vocabulary — corpus
    wording, terse, instrument-style observation, verdict-style, and plain
    speech — measures whether a rendering covers the *concepts* a resolution
    would mention rather than one author's sentence.
    """

    location = question.location_name
    start = _natural_time(question.horizon_start)
    end = _natural_time(question.horizon_end)
    date = _natural_date(question.horizon_start)
    threshold = f"{question.threshold_mm:g}"

    if occurred:
        return {
            "corpus": (
                f"Yes. Measurable precipitation occurred in {location} "
                f"during the requested UTC hour."
            ),
            "terse": f"Yes. Rain occurred in {location}.",
            "observation": (
                f"Observed: 0.4 mm of rainfall recorded at {location} between "
                f"{start} and {end} UTC on {date}."
            ),
            "verdict": (
                f"Outcome: precipitation above the {threshold} mm threshold was "
                f"measured at the {location} point."
            ),
            "plain": f"It rained in {location} in that hour.",
        }
    return {
        "corpus": (
            f"No. Measurable precipitation did not occur in {location} "
            f"during the requested UTC hour."
        ),
        "terse": f"No. No rain occurred in {location}.",
        "observation": (
            f"Observed: 0.0 mm of rainfall recorded at {location} between "
            f"{start} and {end} UTC on {date}."
        ),
        "verdict": (
            f"Outcome: precipitation did not reach the {threshold} mm threshold "
            f"at the {location} point."
        ),
        "plain": f"It did not rain in {location} in that hour.",
    }


@dataclass(frozen=True)
class VariantOutcome:
    variant: str
    probability: float
    content: str
    characters: int
    score_if_occurred: float
    score_if_not_occurred: float
    expected_score: float
    worst_paraphrase_expected_score: float
    robust_score_if_occurred: float
    robust_score_if_not_occurred: float
    disqualifying_issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "probability": self.probability,
            "content": self.content,
            "characters": self.characters,
            "score_if_occurred": self.score_if_occurred,
            "score_if_not_occurred": self.score_if_not_occurred,
            "expected_score": self.expected_score,
            "worst_paraphrase_expected_score": self.worst_paraphrase_expected_score,
            "robust_score_if_occurred": self.robust_score_if_occurred,
            "robust_score_if_not_occurred": self.robust_score_if_not_occurred,
            "disqualifying_issues": list(self.disqualifying_issues),
        }


def evaluate_variant(
    name: str,
    renderer: Callable[[ForecastQuestion, CanonicalForecast], str],
    question: ForecastQuestion,
    probability: float,
) -> VariantOutcome:
    forecast = CanonicalForecast(
        event_id=question.event_id,
        provider="harness",
        probability=probability,
        horizon_start=question.horizon_start,
        horizon_end=question.horizon_end,
        threshold_mm=question.threshold_mm,
        issued_at=question.forecast_cutoff,
        native_event_definition="harness_proxy",
        event_equivalence="documented_event_match",
        adapter_version="harness",
    )
    content = renderer(question, forecast)
    raw_response = {"content": content, "probability": round(probability, 4)}
    question_text = proxy_question_text(question)

    positive_truths = proxy_ground_truth(question, occurred=True)
    negative_truths = proxy_ground_truth(question, occurred=False)

    positive_scores = {
        key: evaluate_reference(question_text, truth, raw_response).score
        for key, truth in positive_truths.items()
    }
    negative_scores = {
        key: evaluate_reference(question_text, truth, raw_response).score
        for key, truth in negative_truths.items()
    }

    # The integrity guard is phrasing-independent, so the corpus wording is
    # sufficient and keeps this consistent with the adversarial benchmark.
    robust_positive = evaluate_robust_reference(
        question_text, positive_truths["corpus"], raw_response
    )
    robust_negative = evaluate_robust_reference(
        question_text, negative_truths["corpus"], raw_response
    )

    issues = {
        issue
        for evaluation in (robust_positive, robust_negative)
        for issue in evaluation.issues
        if issue in DISQUALIFYING_ISSUES
    }

    mean_positive = sum(positive_scores.values()) / len(positive_scores)
    mean_negative = sum(negative_scores.values()) / len(negative_scores)
    expected = (probability * mean_positive) + ((1 - probability) * mean_negative)

    # Report the weakest paraphrase too: a variant should not depend on one
    # lucky vocabulary match to clear the threshold.
    per_paraphrase_expected = [
        (probability * positive_scores[key]) + ((1 - probability) * negative_scores[key])
        for key in positive_scores
    ]

    return VariantOutcome(
        variant=name,
        probability=probability,
        content=content,
        characters=len(content),
        score_if_occurred=round(mean_positive, 6),
        score_if_not_occurred=round(mean_negative, 6),
        expected_score=round(expected, 6),
        worst_paraphrase_expected_score=round(min(per_paraphrase_expected), 6),
        robust_score_if_occurred=robust_positive.score,
        robust_score_if_not_occurred=robust_negative.score,
        disqualifying_issues=tuple(sorted(issues)),
    )


def default_question() -> ForecastQuestion:
    return ForecastQuestion(
        event_id="renderer-benchmark-lagos-1",
        location_name="Lagos",
        latitude=6.5244,
        longitude=3.3792,
        horizon_start=datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc),
        horizon_end=datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc),
        forecast_cutoff=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
    )


def run_benchmark(
    probabilities: Sequence[float] | None = None,
    question: ForecastQuestion | None = None,
) -> dict[str, Any]:
    question = question or default_question()
    probabilities = list(probabilities or (0.05, 0.2, 0.5, 0.7, 0.95))

    outcomes: list[VariantOutcome] = []
    for name, renderer in VARIANTS.items():
        for probability in probabilities:
            outcomes.append(evaluate_variant(name, renderer, question, probability))

    summary: dict[str, Any] = {}
    for name in VARIANTS:
        rows = [row for row in outcomes if row.variant == name]
        expected = [row.expected_score for row in rows]
        worst = [row.worst_paraphrase_expected_score for row in rows]
        # The guard lane uses F1, so padding costs precision. The proxy lane is
        # recall-only, so padding is free there. Reporting both is the point:
        # where they disagree, the disagreement is the finding.
        guard = [
            (row.probability * row.robust_score_if_occurred)
            + ((1 - row.probability) * row.robust_score_if_not_occurred)
            for row in rows
        ]
        disqualified = sorted({issue for row in rows for issue in row.disqualifying_issues})
        summary[name] = {
            "mean_expected_score": round(sum(expected) / len(expected), 6),
            "min_expected_score": round(min(expected), 6),
            "max_expected_score": round(max(expected), 6),
            "min_worst_paraphrase_score": round(min(worst), 6),
            "mean_guard_expected_score": round(sum(guard) / len(guard), 6),
            "passes_good_threshold_at_all_probabilities": all(
                value >= GOOD_SCORE_THRESHOLD for value in expected
            ),
            "mean_characters": round(sum(row.characters for row in rows) / len(rows), 2),
            "disqualifying_issues": disqualified,
            "integrity_ok": not disqualified,
        }

    # Rank on the F1 guard lane, not the recall-only proxy. The proxy can only
    # reward adding words, so it prefers whichever variant says the most; the
    # guard penalises padding through precision. Ranking on the proxy alone
    # would have recommended the longest candidate on merit it did not have.
    ranked = sorted(
        (name for name, data in summary.items() if data["integrity_ok"]),
        key=lambda name: (
            summary[name]["mean_guard_expected_score"],
            -summary[name]["mean_characters"],
        ),
        reverse=True,
    )
    return {
        "harness_version": HARNESS_VERSION,
        "official_status": "development_only_not_telegraph_canonical_script",
        "scoring_lanes": {
            "proxy": "bounded_overlap_length_reference_proxy",
            "integrity_guard": "development_robust_semantic_proxy_v1",
            "brier": "separate_domain_benchmark_not_included_here",
        },
        "thresholds": {"good_score_threshold": GOOD_SCORE_THRESHOLD},
        "probabilities": probabilities,
        "proxy_question": proxy_question_text(question),
        "proxy_ground_truth_paraphrases": {
            "occurred": proxy_ground_truth(question, occurred=True),
            "did_not_occur": proxy_ground_truth(question, occurred=False),
        },
        "summary": summary,
        "ranking_by_mean_expected_score": ranked,
        "cases": [row.to_dict() for row in outcomes],
        "limitations": [
            "Telegraph's Canonical Script is not released; these are local proxies.",
            "The question and ground-truth wording are approximations of an unpublished format.",
            "Each branch is scored against five paraphrases and averaged, so a variant "
            "cannot win by echoing one hand-authored ground-truth sentence.",
            "A proxy gain is directional evidence only and is not a predicted protocol score.",
            "No variant that trips an anti-gaming issue is eligible regardless of proxy score.",
            "The proxy lane scores recall only, so it can be raised by adding words. "
            "Telegraph's published composite uses cosine similarity and BM25, both of "
            "which normalise for length, so the F1 guard lane is the closer analogue "
            "and is what the ranking uses.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write the JSON report to this path")
    parser.add_argument(
        "--probability",
        action="append",
        type=float,
        dest="probabilities",
        help="Probability to evaluate (repeatable)",
    )
    args = parser.parse_args()

    report = run_benchmark(args.probabilities)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")

    print(f"harness: {report['harness_version']} ({report['official_status']})")
    print(f"proxy question: {report['proxy_question']}")
    print()
    header = (
        f"{'variant':<24} {'proxy':>8} {'guard':>8} {'min':>8} "
        f"{'worst_p':>8} {'chars':>7} {'integrity':>10}"
    )
    print(header)
    print("-" * len(header))
    for name in sorted(
        report["summary"],
        key=lambda key: report["summary"][key]["mean_guard_expected_score"],
        reverse=True,
    ):
        data = report["summary"][name]
        integrity = "ok" if data["integrity_ok"] else ",".join(data["disqualifying_issues"])
        print(
            f"{name:<24} {data['mean_expected_score']:>8.4f} "
            f"{data['mean_guard_expected_score']:>8.4f} "
            f"{data['min_expected_score']:>8.4f} "
            f"{data['min_worst_paraphrase_score']:>8.4f} "
            f"{data['mean_characters']:>7.1f} {integrity:>10}"
        )
    print()
    print("proxy = recall-only overlap (length-biased); guard = F1 (penalises padding).")
    print("Ranked on guard. Where the two lanes disagree, prefer guard.")
    print()
    print(
        "These are LOCAL PROXY scores. They are not comparable to the Telegraph\n"
        "leaderboard numbers from scripts/read_leaderboard.py, even though both\n"
        "land in the same 0.4-0.7 range — that coincidence is why the comparison\n"
        "is tempting. This proxy is 0.8*overlap + 0.2*length_quality; Telegraph\n"
        "scores cosine + BM25 + length quality, which is not implemented here.\n"
        "A proxy score above the live target does not mean the bar is cleared."
    )
    print()
    if report["ranking_by_mean_expected_score"]:
        best = report["ranking_by_mean_expected_score"][0]
        print(f"best eligible variant: {best}")
    else:
        print("no eligible variant: every candidate tripped an integrity issue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
