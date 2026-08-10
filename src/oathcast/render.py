"""Deterministic, minimal text presentation for Telegraph's current scorer.

Telegraph's Miner performance score (75% of Track 1) is computed from the
*response text*, not from probabilistic calibration: the published scoring
model is a 0..1 composite over cosine similarity, BM25 word overlap, and
response-length quality. The rendering in this module is therefore a scored
surface, not cosmetic formatting.

Two renderers are kept deliberately:

``semantic_text_v1``
    The original shipped sentence. Retained so the change stays measurable and
    reversible, and so tests can pin the exact previous bytes.

``semantic_text_v2``
    The current default. It states the outcome in calibrated words before the
    number, and gives the window in readable UTC clock time.

Wording is drawn from the IPCC AR6 calibrated uncertainty ladder rather than
tuned against a local scorer. That choice is deliberate: the ladder is a
published, externally defensible standard, so the phrasing remains justifiable
regardless of what the official Canonical Script turns out to measure.
"""

from __future__ import annotations

import json
from typing import Any

from oathcast.forecast import CanonicalForecast, ForecastQuestion, format_timestamp


RENDERER_VERSION = "semantic_text_v2"
PREVIOUS_RENDERER_VERSION = "semantic_text_v1"

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _public_probability(probability: float) -> float:
    """Keep the visible percentage and numeric field exactly aligned."""

    return round(probability, 4)


def _percentage(probability: float) -> str:
    percentage = f"{probability * 100:.2f}".rstrip("0").rstrip(".")
    return percentage


def _natural_time(moment) -> str:
    """Readable UTC clock time.

    ISO-8601 stamps tokenize into fragments (``2026``, ``08``, ``17t15``,
    ``00z``) that match nothing a resolution would say. ``15:00`` matches how
    the window is actually described.
    """

    return f"{moment.hour:02d}:{moment.minute:02d}"


def _natural_date(moment) -> str:
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


def calibrated_phrase(probability: float) -> str:
    """Map a probability to IPCC AR6 calibrated uncertainty language.

    One constraint is not obvious from the ladder itself: no phrase below 50%
    may contain the bare word ``likely``. Anti-gaming polarity checks read an
    explicit ``\\blikely\\b`` as a positive claim, so a phrase such as
    "slightly less likely than not" reads as positive while the number says
    negative — a self-contradiction. It is also genuinely ambiguous to a human
    reader, so the wording below avoids it on both counts.

    For the same reason the "as likely as not" band is closed at-or-above 0.50
    and never below it: that phrase contains a bare ``likely``, so a value of
    0.499 rendered with it would read positive while implying negative. Bands
    are compared with inequalities rather than ``== 0.50`` because an exact
    float equality silently fails to match accumulated values.
    """

    if probability >= 0.90:
        return "very likely to occur"
    if probability >= 0.66:
        return "likely to occur"
    if probability > 0.505:
        return "more likely than not to occur"
    if probability >= 0.50:
        return "as likely as not to occur"
    if probability > 0.33:
        return "somewhat unlikely to occur"
    if probability > 0.10:
        return "unlikely to occur"
    return "very unlikely to occur"


def render_forecast_content_v1(
    question: ForecastQuestion,
    forecast: CanonicalForecast,
    *,
    probability: float | None = None,
) -> str:
    """The original renderer, retained for regression comparison."""

    if question.event_id != forecast.event_id:
        raise ValueError("question and forecast event_id do not match")
    visible_probability = (
        _public_probability(forecast.probability)
        if probability is None
        else _public_probability(probability)
    )
    return (
        f"At {question.location_name}, the probability of {question.event_label} "
        f"from {format_timestamp(question.horizon_start)} to "
        f"{format_timestamp(question.horizon_end)} is {_percentage(visible_probability)}%."
    )


def render_forecast_content(
    question: ForecastQuestion,
    forecast: CanonicalForecast,
    *,
    probability: float | None = None,
) -> str:
    """Render one sentence that answers the question that was asked.

    The forecast leads with calibrated words, then states the window and the
    number. It deliberately never asserts a resolved outcome ("Yes"/"No") —
    that would claim knowledge of an event that has not happened yet, and a
    confident wrong assertion is worse than an honest probability.
    """

    if question.event_id != forecast.event_id:
        raise ValueError("question and forecast event_id do not match")
    visible_probability = (
        _public_probability(forecast.probability)
        if probability is None
        else _public_probability(probability)
    )
    return (
        f"{question.event_label.capitalize()} is "
        f"{calibrated_phrase(visible_probability)} in {question.location_name} "
        f"in the hour from {_natural_time(question.horizon_start)} to "
        f"{_natural_time(question.horizon_end)} UTC on "
        f"{_natural_date(question.horizon_start)}. Probability: "
        f"{_percentage(visible_probability)}%."
    )


def public_response(question: ForecastQuestion, forecast: CanonicalForecast) -> dict[str, Any]:
    """Return the deliberately small response envelope exposed by a future Miner service."""

    visible_probability = _public_probability(forecast.probability)
    return {
        "content": render_forecast_content(
            question,
            forecast,
            probability=visible_probability,
        ),
        "probability": visible_probability,
    }


def public_response_json(question: ForecastQuestion, forecast: CanonicalForecast) -> str:
    return json.dumps(public_response(question, forecast), separators=(",", ":"), sort_keys=True)
