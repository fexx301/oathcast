"""Shared probability extraction for Miner responses and local benchmarks."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any

from oathcast.protocol import ProtocolResultEnvelope


PROBABILITY_PATTERN = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
PROBABILITY_KEYS = (
    "probability",
    "precipitation_probability",
    "probability_of_precipitation",
    "rain_probability",
    "probability_of_rain",
    "pop",
)
PERCENT_PROBABILITY_KEYS = (
    "chance_of_rain",
    "rain_chance",
    "precipitation_chance",
    "precipitation_probability_percent",
    "rain_probability_percent",
)


def normalize_probability_value(value: Any, key: str) -> float | None:
    """Normalize one recognized probability field without guessing its units."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    probability = float(value)
    if not math.isfinite(probability):
        return None
    if key in PERCENT_PROBABILITY_KEYS:
        return probability / 100 if 0 <= probability <= 100 else None
    if key not in PROBABILITY_KEYS:
        return None
    if 0 <= probability <= 1:
        return probability
    if key != "probability" and 1 < probability <= 100:
        return probability / 100
    return None


def _body(raw_response: Any) -> Any:
    if isinstance(raw_response, ProtocolResultEnvelope):
        return raw_response.body
    return raw_response


def _content(raw_response: Any) -> str:
    raw_response = _body(raw_response)
    if isinstance(raw_response, str):
        return raw_response.strip()
    if isinstance(raw_response, Mapping):
        content = raw_response.get("content")
        if isinstance(content, str):
            return content.strip()
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message")
            if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                return message["content"].strip()
        return json.dumps(dict(raw_response), sort_keys=True, separators=(",", ":"))
    return str(raw_response).strip()


def extract_probability(raw_response: Any, *, text: str | None = None) -> float | None:
    """Extract a unit-interval probability from supported response shapes."""

    raw_response = _body(raw_response)
    if isinstance(raw_response, Mapping):
        for key in (*PROBABILITY_KEYS, *PERCENT_PROBABILITY_KEYS):
            probability = normalize_probability_value(raw_response.get(key), key)
            if probability is not None:
                return probability
        for key in ("data", "result", "forecast", "prediction", "output"):
            probability = extract_probability(raw_response.get(key))
            if probability is not None:
                return probability
        choices = raw_response.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                probability = extract_probability(choice)
                if probability is not None:
                    return probability

    match = PROBABILITY_PATTERN.search(_content(raw_response) if text is None else text)
    if match is None:
        return None
    percentage = float(match.group(1))
    return percentage / 100 if 0 <= percentage <= 100 else None
