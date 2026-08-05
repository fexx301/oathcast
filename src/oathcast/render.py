"""Deterministic, minimal text presentation for Telegraph's current scorer."""

from __future__ import annotations

import json
from typing import Any

from oathcast.forecast import CanonicalForecast, ForecastQuestion, format_timestamp


RENDERER_VERSION = "semantic_text_v1"


def _public_probability(probability: float) -> float:
    """Keep the visible percentage and numeric field exactly aligned."""

    return round(probability, 4)


def _percentage(probability: float) -> str:
    percentage = f"{probability * 100:.2f}".rstrip("0").rstrip(".")
    return percentage


def render_forecast_content(
    question: ForecastQuestion,
    forecast: CanonicalForecast,
    *,
    probability: float | None = None,
) -> str:
    """Render one short sentence with the same event vocabulary for every provider."""

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
