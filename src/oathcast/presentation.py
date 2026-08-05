"""Human-readable presentation for the local Application evidence demo.

The renderer is intentionally fixture-aware. It makes the cross-Miner
decision, external influence, later resolution, durable hashes, and owned
Miner ablation easy to inspect without implying that development data is
Telegraph traffic or official demand evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


PRESENTATION_VERSION = "application_evidence_markdown_v1"


def _cell(value: Any) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _probability(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "invalid"


def _number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "invalid"


def _json_block(value: Any) -> str:
    encoded = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    return encoded.replace("```", "\\u0060\\u0060\\u0060")


def _last_record(case_evidence: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    records = case_evidence.get(key)
    if not isinstance(records, list) or not records:
        return None
    record = records[-1]
    return record if isinstance(record, Mapping) else None


def render_application_demo_markdown(payload: Mapping[str, Any]) -> str:
    """Render one demo payload as an evidence-first Markdown brief."""

    if not isinstance(payload, Mapping):
        raise TypeError("demo payload must be a mapping")

    decision = payload.get("decision")
    resolution = payload.get("resolution")
    case_evidence = payload.get("case_evidence")
    if not isinstance(decision, Mapping):
        raise ValueError("demo payload is missing decision evidence")
    if not isinstance(resolution, Mapping):
        raise ValueError("demo payload is missing resolution evidence")
    if not isinstance(case_evidence, Mapping):
        raise ValueError("demo payload is missing case evidence")

    question = case_evidence.get("question")
    question = question if isinstance(question, Mapping) else {}
    replies = decision.get("replies")
    replies = replies if isinstance(replies, list) else []
    fallback = payload.get("owned_miner_fallback")
    fallback = fallback if isinstance(fallback, Mapping) else None
    decision_record = _last_record(case_evidence, "decisions") or {}
    resolution_record = _last_record(case_evidence, "resolutions") or {}

    lines = [
        "# OathCast Application evidence demo",
        "",
        f"> **DEVELOPMENT FIXTURE ONLY** — presentation `{PRESENTATION_VERSION}`. "
        "This run is not Telegraph traffic, payment evidence, official demand, "
        "or a live ground-truth claim.",
        "",
        "## Forecast case",
        "",
        f"- **Event:** `{_cell(question.get('event_id') or decision.get('event_id'))}`",
        f"- **Location:** {_cell(question.get('location_name'))} "
        f"({_number(question.get('latitude'), 4)}, {_number(question.get('longitude'), 4)})",
        f"- **Window:** `{_cell(question.get('horizon_start'))}` → "
        f"`{_cell(question.get('horizon_end'))}` ({_cell(question.get('timezone') or 'UTC')})",
        f"- **Cutoff:** `{_cell(question.get('forecast_cutoff'))}`",
        f"- **Predicate:** `{_cell(question.get('metric'))} "
        f"{_cell(question.get('operator'))} {_cell(question.get('threshold_mm'))} mm`",
        "",
        "## Miner comparison",
        "",
        "| Miner | Ownership | Probability | Valid | Latency (ms) | Transport |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for reply in replies:
        if not isinstance(reply, Mapping):
            continue
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(reply.get("slug")),
                    "owned" if reply.get("owned") else "external",
                    _probability(reply.get("probability")),
                    "yes" if reply.get("valid") else "no",
                    _number(reply.get("latency_ms")),
                    _cell(reply.get("transport")),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Live decision",
            "",
            f"- **Aggregate probability:** {_probability(decision.get('aggregate_probability'))}",
            f"- **Event likely:** {'yes' if decision.get('event_likely') else 'no'}",
            f"- **Recommended action:** `{_cell(decision.get('recommended_action'))}`",
            f"- **External Miner used:** {'yes' if decision.get('used_external_miner') else 'no'}",
            f"- **External influence detected:** "
            f"{'yes' if decision.get('external_influence') else 'no'}",
            f"- **Application request ID:** `{_cell(decision.get('application_request_id'))}`",
            "",
            "## Later resolution",
            "",
            f"- **Status:** `{_cell(resolution.get('status'))}`",
            f"- **Outcome:** {_cell(resolution.get('outcome'))} "
            "(`1` means the event occurred; `0` means it did not)",
            f"- **Observed precipitation:** {_cell(resolution.get('precipitation_mm'))} mm",
            f"- **Observation source:** `{_cell(resolution.get('source'))}`",
            f"- **Observation ID:** `{_cell(resolution.get('observation_id'))}`",
            "",
            "## Durable evidence",
            "",
            f"- **Question SHA-256:** `{_cell(case_evidence.get('question_sha256'))}`",
            f"- **Decision SHA-256:** `{_cell(decision_record.get('decision_sha256'))}`",
            f"- **Resolution SHA-256:** `{_cell(resolution_record.get('resolution_sha256'))}`",
            f"- **Protocol/payment receipts:** "
            f"{'present' if any(isinstance(reply, Mapping) and reply.get('protocol_result') for reply in replies) else 'not present in this fixture run'}",
            "",
            "## Owned-Miner-disabled ablation",
            "",
        ]
    )
    if fallback is None:
        lines.append("Not run. Pass `--compare-owned-fallback` to execute the ablation.")
    else:
        fallback_decision = fallback.get("decision")
        fallback_decision = fallback_decision if isinstance(fallback_decision, Mapping) else {}
        fallback_replies = fallback_decision.get("replies")
        fallback_replies = fallback_replies if isinstance(fallback_replies, list) else []
        all_external = bool(fallback_replies) and all(
            isinstance(reply, Mapping) and not reply.get("owned") for reply in fallback_replies
        )
        lines.extend(
            [
                f"- **Ablation passed:** {'yes' if fallback.get('ok') else 'no'}",
                f"- **Owned Miner disabled:** {'yes' if fallback.get('owned_miner_disabled') else 'no'}",
                f"- **External replies remained usable:** {'yes' if all_external else 'no'}",
                f"- **Fallback aggregate probability:** "
                f"{_probability(fallback_decision.get('aggregate_probability'))}",
            ]
        )

    lines.extend(["", "## Miner response details", ""])
    for reply in replies:
        if not isinstance(reply, Mapping):
            continue
        lines.extend(
            [
                f"### `{_cell(reply.get('slug'))}`",
                "",
                f"- **Normalized content:** {_cell(reply.get('content'))}",
                "- **Raw response:**",
                "",
                "```json",
                _json_block(reply.get("raw_response")),
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "This demo proves that the Application can compare owned and external "
            "responses, retain case evidence, resolve an exact observation window, "
            "and continue with the owned Miner disabled. It does not prove Miner "
            "registration, WASM scoring, paid Telegraph traffic, Explorer activity, "
            "or official Track 3 qualification.",
            "",
        ]
    )
    return "\n".join(lines)
