"""Development reference for the future Script Author input contract.

This is not the official Telegraph scorer and is not intended to reproduce its
cosine/BM25 implementation. It verifies that question, ground truth, and raw
responses can be normalized deterministically and that malformed/empty/overly
long responses receive a bounded result while the public harness is pending.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?", re.IGNORECASE)
DEFAULT_MAX_RESPONSE_CHARS = 4096


def normalize_response(raw_response: Any) -> str:
    """Convert supported JSON/chat/plain-text shapes into scorer-visible text."""

    if isinstance(raw_response, str):
        text = raw_response
    elif isinstance(raw_response, dict):
        content = raw_response.get("content")
        if isinstance(content, str):
            text = content
        else:
            choices = raw_response.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                text = message.get("content", "") if isinstance(message, dict) else ""
                if not isinstance(text, str) or not text:
                    text = json.dumps(raw_response, sort_keys=True, separators=(",", ":"))
            else:
                text = json.dumps(raw_response, sort_keys=True, separators=(",", ":"))
    else:
        text = str(raw_response)
    return " ".join(text.split())


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


@dataclass(frozen=True)
class ReferenceEvaluation:
    score: float
    response_text: str
    valid: bool
    issues: tuple[str, ...]
    algorithm: str = "development_proxy_not_telegraph_scorer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "response_text": self.response_text,
            "valid": self.valid,
            "issues": list(self.issues),
            "algorithm": self.algorithm,
        }


def evaluate_reference(
    question: str,
    ground_truth: str,
    raw_response: Any,
    *,
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS,
) -> ReferenceEvaluation:
    """Run a bounded local proxy while preserving the official 3-input shape."""

    del question  # The future harness supplies it; this contract test keeps it opaque.
    response_text = normalize_response(raw_response)
    issues: list[str] = []
    if not response_text:
        issues.append("empty_response")
    if len(response_text) > max_response_chars:
        issues.append("response_too_long")
    truth_tokens = set(_tokens(ground_truth))
    response_tokens = set(_tokens(response_text))
    if not truth_tokens:
        issues.append("empty_ground_truth")

    if issues:
        return ReferenceEvaluation(0.0, response_text, False, tuple(issues))

    overlap = len(truth_tokens & response_tokens) / len(truth_tokens)
    length_quality = min(1.0, len(response_tokens) / 12)
    score = round(max(0.0, min(1.0, (0.8 * overlap) + (0.2 * length_quality))), 6)
    return ReferenceEvaluation(score, response_text, True, ())
