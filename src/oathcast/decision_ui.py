"""Small public Track-3 decision interface.

This module deliberately owns the web boundary only.  It does not discover
Miners, call a weather provider, create x402 headers, sign a transaction, or
pretend that a local fixture is Telegraph traffic.  A real application injects
one decision runner; until that runner declares both routing and payment ready,
the HTTP API returns ``503 Service Unavailable``.

The implementation is standard-library-only so it can be run as a small
development service while the official Telegraph integration is provisioned.
The public response contains a small, allow-listed Miner evidence shape rather
than arbitrary upstream payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
import json
import math
import re
import uuid
from urllib.parse import urlsplit


SERVICE_NAME = "oathcast-decision-ui"
API_PATH = "/api/decision"
HEALTH_PATH = "/health"
STATUS_PATH = "/status"

# The cap is intentionally small: the request contains a few human-entered
# scalar values, not a forecast payload or an upstream response.
MAX_JSON_BODY_BYTES = 16 * 1024
MAX_BODY_BYTES = MAX_JSON_BODY_BYTES
MAX_ACTIVITY_LENGTH = 120
MAX_LOCATION_LENGTH = 200
MAX_DATETIME_LENGTH = 64
MAX_RESULT_TEXT_LENGTH = 1200
MAX_MINER_EVIDENCE = 32

DECISION_ACTIONS = frozenset({"go", "delay", "relocate", "contingency"})
EVIDENCE_STATUSES = frozenset({"valid", "unavailable", "invalid", "unknown"})
EVIDENCE_IDS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ValidationError(ValueError):
    """A client-side request validation error with safe field messages."""

    def __init__(self, message: str = "Request validation failed.", *, fields: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.fields = dict(fields or {})

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": "invalid_request",
            "message": str(self),
        }
        if self.fields:
            payload["fields"] = dict(self.fields)
        return payload


class DecisionUnavailable(RuntimeError):
    """Raised when a decision cannot be obtained from the configured runner."""


class TelegraphNotConfigured(DecisionUnavailable):
    """Raised when real Telegraph routing and payment are not both ready."""


class DecisionContractError(ValueError):
    """Raised when an injected runner returns an unsafe or invalid result."""


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(fields={field: "must be a string"})
    text = value.strip()
    if not text:
        raise ValidationError(fields={field: "is required"})
    if len(text) > maximum:
        raise ValidationError(fields={field: f"must be at most {maximum} characters"})
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValidationError(fields={field: "contains a control character"})
    return text


def _clean_result_text(value: Any, *, field: str, maximum: int) -> str:
    """Keep runner prose public, while redacting credential-like fragments."""

    try:
        text = _clean_text(value, field=field, maximum=maximum)
    except ValidationError as exc:
        raise DecisionContractError(str(exc)) from exc

    # The runner contract is already allow-listed, but result prose is still
    # treated as untrusted.  These patterns cover common accidental credential
    # disclosures without logging or returning the original fragment.
    sensitive_patterns = (
        r"(?i)\b(?:private[ _-]?key|secret|mnemonic|seed[ _-]?phrase|xpriv|access[ _-]?token|authorization|bearer)\b[^\n]*",
        r"(?i)\bwallet(?:[ _-]?(?:key|secret|address|credential|material))?\b[^\n]*",
        r"\b0x[0-9a-fA-F]{40,}\b",
    )
    for pattern in sensitive_patterns:
        text = re.sub(pattern, "[redacted]", text)
    return text


def _number(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(fields={field: "must be a JSON number"})
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ValidationError(fields={field: "must be finite"}) from exc
    if not math.isfinite(numeric):
        raise ValidationError(fields={field: "must be finite"})
    if not minimum <= numeric <= maximum:
        raise ValidationError(fields={field: f"must be between {minimum:g} and {maximum:g}"})
    return numeric


def _optional_number(value: Any, *, field: str, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    try:
        return _number(value, field=field, minimum=minimum, maximum=maximum)
    except ValidationError as exc:
        raise DecisionContractError(str(exc)) from exc


def _datetime_with_offset(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(fields={"local_datetime": "must be an ISO 8601 string"})
    text = value.strip()
    if not text:
        raise ValidationError(fields={"local_datetime": "is required"})
    if len(text) > MAX_DATETIME_LENGTH:
        raise ValidationError(fields={"local_datetime": f"must be at most {MAX_DATETIME_LENGTH} characters"})
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            fields={"local_datetime": "must be a valid ISO 8601 date-time with an offset"}
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(fields={"local_datetime": "must include a UTC offset"})
    return parsed


def _select_alias(data: Mapping[str, Any], names: tuple[str, ...], *, field: str) -> Any:
    present = [name for name in names if name in data]
    if not present:
        raise ValidationError(fields={field: "is required"})
    if len(present) > 1:
        raise ValidationError(fields={field: "use one supported field name, not aliases together"})
    return data[present[0]]


@dataclass(frozen=True)
class DecisionInput:
    """Validated human input passed to an injected decision runner."""

    activity: str
    location: str
    latitude: float
    longitude: float
    local_datetime: datetime
    risk_threshold_percent: float
    consent: bool = True

    @property
    def local_date_time(self) -> datetime:
        """Compatibility spelling for callers that use the UI label."""

        return self.local_datetime

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DecisionInput":
        return parse_decision_input(data)


def parse_decision_input(data: Mapping[str, Any]) -> DecisionInput:
    """Validate and normalize one public decision request.

    The canonical JSON names are ``activity``, ``location``, ``latitude``,
    ``longitude``, ``local_datetime``, ``risk_threshold_percent``, and
    ``consent``.  The short ``lat``/``lon`` names and two human-friendly date
    and threshold aliases are accepted deliberately and are still part of the
    strict allow-list.
    """

    if not isinstance(data, Mapping):
        raise ValidationError("Request body must be a JSON object.")

    allowed = {
        "activity",
        "location",
        "latitude",
        "lat",
        "longitude",
        "lon",
        "local_datetime",
        "local_date_time",
        "risk_threshold_percent",
        "risk_threshold",
        "consent",
    }
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ValidationError(fields={"body": "contains unsupported fields"})

    activity = _clean_text(data.get("activity"), field="activity", maximum=MAX_ACTIVITY_LENGTH)
    location = _clean_text(data.get("location"), field="location", maximum=MAX_LOCATION_LENGTH)
    latitude = _number(
        _select_alias(data, ("latitude", "lat"), field="latitude"),
        field="latitude",
        minimum=-90,
        maximum=90,
    )
    longitude = _number(
        _select_alias(data, ("longitude", "lon"), field="longitude"),
        field="longitude",
        minimum=-180,
        maximum=180,
    )
    local_datetime = _datetime_with_offset(
        _select_alias(data, ("local_datetime", "local_date_time"), field="local_datetime")
    )
    risk_threshold_percent = _number(
        _select_alias(
            data,
            ("risk_threshold_percent", "risk_threshold"),
            field="risk_threshold_percent",
        ),
        field="risk_threshold_percent",
        minimum=0,
        maximum=100,
    )
    consent = data.get("consent")
    if consent is not True:
        raise ValidationError(
            fields={"consent": "explicit consent is required to run this decision"}
        )

    return DecisionInput(
        activity=activity,
        location=location,
        latitude=latitude,
        longitude=longitude,
        local_datetime=local_datetime,
        risk_threshold_percent=risk_threshold_percent,
        consent=True,
    )


@dataclass(frozen=True)
class MinerEvidence:
    """Allow-listed public evidence for one Miner response.

    Raw response bodies, payment challenges, authorization headers, wallet
    addresses, and signing material are intentionally not representable here.
    """

    miner_id: str
    status: str
    probability_percent: float | None = None
    evidence_id: str | None = None
    routed_via_telegraph: bool = False
    payment_verified: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "MinerEvidence":
        if isinstance(value, cls):
            evidence = value
        elif isinstance(value, Mapping):
            allowed = {
                "miner_id",
                "miner",
                "status",
                "probability_percent",
                "evidence_id",
                "routed_via_telegraph",
                "payment_verified",
            }
            # Unknown fields are ignored rather than serialized.  This is a
            # deliberate secret boundary for adapter-specific payloads.
            if not isinstance(value.get("miner_id", value.get("miner")), str):
                raise DecisionContractError("Miner evidence needs a public miner_id")
            probability = _optional_number(
                value.get("probability_percent"),
                field="probability_percent",
                minimum=0,
                maximum=100,
            )
            evidence_id = value.get("evidence_id")
            if evidence_id is not None and (
                not isinstance(evidence_id, str) or not EVIDENCE_IDS.fullmatch(evidence_id)
            ):
                raise DecisionContractError("Miner evidence has an invalid evidence_id")
            routed = value.get("routed_via_telegraph", False)
            payment = value.get("payment_verified", False)
            if not isinstance(routed, bool) or not isinstance(payment, bool):
                raise DecisionContractError("Miner evidence flags must be boolean")
            evidence = cls(
                miner_id=value.get("miner_id", value.get("miner")),
                status=value.get("status", "unknown"),
                probability_percent=probability,
                evidence_id=evidence_id,
                routed_via_telegraph=routed,
                payment_verified=payment,
            )
        else:
            raise DecisionContractError("Miner evidence must be an object")

        try:
            miner_id = _clean_text(evidence.miner_id, field="miner_id", maximum=128)
            status = _clean_text(evidence.status, field="status", maximum=32).lower()
        except ValidationError as exc:
            raise DecisionContractError(str(exc)) from exc
        if status not in EVIDENCE_STATUSES:
            raise DecisionContractError("Miner evidence has an unsupported status")
        probability = _optional_number(
            evidence.probability_percent,
            field="probability_percent",
            minimum=0,
            maximum=100,
        )
        if evidence.evidence_id is not None and not EVIDENCE_IDS.fullmatch(evidence.evidence_id):
            raise DecisionContractError("Miner evidence has an invalid evidence_id")
        if not isinstance(evidence.routed_via_telegraph, bool) or not isinstance(
            evidence.payment_verified, bool
        ):
            raise DecisionContractError("Miner evidence flags must be boolean")
        if evidence.payment_verified and not evidence.routed_via_telegraph:
            raise DecisionContractError("verified payment evidence must be Telegraph-routed")
        return cls(
            miner_id=miner_id,
            status=status,
            probability_percent=probability,
            evidence_id=evidence.evidence_id,
            routed_via_telegraph=evidence.routed_via_telegraph,
            payment_verified=evidence.payment_verified,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "miner_id": self.miner_id,
            "status": self.status,
            "probability_percent": self.probability_percent,
            "evidence_id": self.evidence_id,
            "routed_via_telegraph": self.routed_via_telegraph,
            "payment_verified": self.payment_verified,
        }


@dataclass(frozen=True)
class DecisionResult:
    """Decision and the public evidence needed to explain it."""

    action: str
    summary: str
    rationale: str
    miner_evidence: tuple[MinerEvidence, ...] = ()
    risk_percent: float | None = None
    request_id: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "DecisionResult":
        if isinstance(value, cls):
            raw = value
        elif isinstance(value, Mapping):
            evidence_values = value.get("miner_evidence", value.get("miners", []))
            if not isinstance(evidence_values, (list, tuple)):
                raise DecisionContractError("miner_evidence must be an array")
            if len(evidence_values) > MAX_MINER_EVIDENCE:
                raise DecisionContractError("too many Miner evidence records")
            raw = cls(
                action=value.get("action", value.get("decision")),
                summary=value.get("summary"),
                rationale=value.get("rationale", ""),
                miner_evidence=tuple(MinerEvidence.from_value(item) for item in evidence_values),
                risk_percent=value.get("risk_percent"),
                request_id=value.get("request_id"),
            )
        else:
            raise DecisionContractError("decision runner must return an object")

        try:
            action = _clean_text(raw.action, field="action", maximum=24).lower()
            summary = _clean_result_text(
                raw.summary,
                field="summary",
                maximum=MAX_RESULT_TEXT_LENGTH,
            )
            rationale = _clean_result_text(
                raw.rationale,
                field="rationale",
                maximum=MAX_RESULT_TEXT_LENGTH,
            )
        except ValidationError as exc:
            raise DecisionContractError(str(exc)) from exc
        if action not in DECISION_ACTIONS:
            raise DecisionContractError(
                "action must be one of go, delay, relocate, or contingency"
            )
        if not isinstance(raw.miner_evidence, (list, tuple)):
            raise DecisionContractError("miner_evidence must be an array")
        if len(raw.miner_evidence) > MAX_MINER_EVIDENCE:
            raise DecisionContractError("too many Miner evidence records")
        evidence = tuple(MinerEvidence.from_value(item) for item in raw.miner_evidence)
        risk_percent = _optional_number(
            raw.risk_percent,
            field="risk_percent",
            minimum=0,
            maximum=100,
        )
        request_id = raw.request_id
        if request_id is not None and (
            not isinstance(request_id, str) or not EVIDENCE_IDS.fullmatch(request_id)
        ):
            raise DecisionContractError("request_id must be a safe public identifier")
        return cls(
            action=action,
            summary=summary,
            rationale=rationale,
            miner_evidence=evidence,
            risk_percent=risk_percent,
            request_id=request_id,
        )

    def with_request_id(self, request_id: str) -> "DecisionResult":
        return replace(self, request_id=request_id)


class DecisionRunner(Protocol):
    """Callable seam for a real Telegraph-backed decision implementation."""

    def __call__(self, request: DecisionInput) -> DecisionResult | Mapping[str, Any]:
        ...


class TelegraphDecisionRunner:
    """Fail-closed adapter seam for the eventual real Telegraph integration.

    ``decision_callable`` must be supplied by the application integration, and
    both readiness flags must be true.  This class never builds payment
    headers, reads wallet material, or invents a response on its own.
    """

    def __init__(
        self,
        decision_callable: Callable[[DecisionInput], DecisionResult | Mapping[str, Any]] | None = None,
        *,
        routing_configured: bool = False,
        payment_configured: bool = False,
    ) -> None:
        self.decision_callable = decision_callable
        self.routing_configured = routing_configured
        self.payment_configured = payment_configured

    @property
    def configured(self) -> bool:
        return bool(
            callable(self.decision_callable)
            and self.routing_configured
            and self.payment_configured
        )

    @property
    def telegraph_configured(self) -> bool:
        return self.configured

    def __call__(self, request: DecisionInput) -> DecisionResult | Mapping[str, Any]:
        if not self.configured:
            raise TelegraphNotConfigured(
                "Live Telegraph routing and payment are not configured."
            )
        # The integration owns actual routing, payment authorization, and
        # settlement verification.  This boundary passes only validated user
        # input and accepts only the allow-listed result above.
        assert self.decision_callable is not None
        return self.decision_callable(request)


class DecisionApplication:
    """HTTP-independent application service used by the request handler."""

    def __init__(
        self,
        decision_runner: DecisionRunner | Callable[[DecisionInput], Any] | None = None,
        *,
        max_body_bytes: int = MAX_JSON_BODY_BYTES,
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.decision_runner: Any = (
            decision_runner if decision_runner is not None else TelegraphDecisionRunner()
        )
        self.max_body_bytes = max_body_bytes

    @property
    def runner_configured(self) -> bool:
        configured = getattr(self.decision_runner, "configured", None)
        if configured is not None:
            return bool(configured)
        return callable(self.decision_runner) or callable(
            getattr(self.decision_runner, "run", None)
        )

    @property
    def telegraph_configured(self) -> bool:
        return bool(getattr(self.decision_runner, "telegraph_configured", False))

    def status_payload(self) -> dict[str, Any]:
        ready = self.runner_configured
        return {
            "service": SERVICE_NAME,
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "runner_configured": ready,
            "telegraph_routing_and_payment_configured": self.telegraph_configured,
            "decision_path": API_PATH,
            "max_json_body_bytes": self.max_body_bytes,
            "wallet_secrets_exposed": False,
        }

    def decide(self, request: DecisionInput) -> DecisionResult:
        if not self.runner_configured:
            raise TelegraphNotConfigured(
                "Live Telegraph routing and payment are not configured."
            )
        try:
            if callable(self.decision_runner):
                value = self.decision_runner(request)
            else:
                value = self.decision_runner.run(request)
            result = DecisionResult.from_value(value)
        except DecisionUnavailable:
            raise
        except DecisionContractError:
            raise
        except Exception as exc:
            # The public boundary deliberately does not return exception text:
            # an adapter may have included upstream or payment details.
            raise DecisionUnavailable("decision runner unavailable") from exc
        return result.with_request_id(result.request_id or f"decision-{uuid.uuid4().hex}")

    def public_result(self, request: DecisionInput, result: DecisionResult) -> dict[str, Any]:
        return {
            "ok": True,
            "decision": result.action,
            "action": result.action,
            "summary": result.summary,
            "rationale": result.rationale,
            "risk_percent": result.risk_percent,
            "risk_threshold_percent": request.risk_threshold_percent,
            "miner_evidence": [item.to_public_dict() for item in result.miner_evidence],
            "request_id": result.request_id,
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


def decode_json_body(body: bytes) -> Any:
    """Decode one bounded UTF-8 JSON body with duplicate-key rejection."""

    try:
        text = body.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        raise ValidationError("Request body must be valid UTF-8 JSON.") from exc


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _offset_options() -> str:
    options: list[str] = []
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        sign = "+" if minutes >= 0 else "-"
        absolute = abs(minutes)
        label = f"UTC{sign}{absolute // 60:02d}:{absolute % 60:02d}"
        value = f"{sign}{absolute // 60:02d}:{absolute % 60:02d}"
        selected = " selected" if value == "+00:00" else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    return "".join(options)


def _format_percent(value: float | None) -> str:
    return "—" if value is None else f"{value:g}%"


def render_decision_result(result: DecisionResult) -> str:
    """Render a result as escaped HTML for tests or a server-side shell."""

    safe = DecisionResult.from_value(result)
    evidence_rows: list[str] = []
    for evidence in safe.miner_evidence:
        item = evidence.to_public_dict()
        evidence_rows.append(
            "<tr>"
            f"<td>{escape(str(item['miner_id']))}</td>"
            f"<td>{escape(str(item['status']))}</td>"
            f"<td>{escape(_format_percent(item['probability_percent']))}</td>"
            f"<td>{'yes' if item['routed_via_telegraph'] else 'no'}</td>"
            f"<td>{'verified' if item['payment_verified'] else 'not verified'}</td>"
            "</tr>"
        )
    rows = "".join(evidence_rows) or (
        '<tr><td colspan="5">No public Miner evidence was returned.</td></tr>'
    )
    return (
        '<section class="result" aria-labelledby="result-heading">'
        '<p class="eyebrow">Decision returned</p>'
        f'<h2 id="result-heading">{escape(safe.action.upper())}</h2>'
        f'<p class="summary">{escape(safe.summary)}</p>'
        f'<p>{escape(safe.rationale)}</p>'
        '<dl class="result-facts">'
        f'<div><dt>Risk estimate</dt><dd>{escape(_format_percent(safe.risk_percent))}</dd></div>'
        f'<div><dt>Request ID</dt><dd>{escape(safe.request_id or "—")}</dd></div>'
        '</dl>'
        '<h3>Miner evidence</h3>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th scope="col">Miner</th><th scope="col">Status</th>'
        '<th scope="col">Risk</th><th scope="col">Telegraph route</th>'
        '<th scope="col">Payment</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>'
        '</section>'
    )


def render_page(*, result: DecisionResult | None = None, error: str | None = None) -> str:
    """Return the accessible, dependency-free public page."""

    feedback = ""
    if error:
        feedback = f'<p class="feedback error" role="alert">{escape(error)}</p>'
    result_markup = render_decision_result(result) if result is not None else ""
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OathCast outdoor decision check</title>
  <meta name="description" content="A privacy-minimal outdoor activity decision check.">
  <style>
    :root {{ color-scheme: light; --ink: #17221d; --muted: #5b6b62; --paper: #f7f5ef; --panel: #fffdf8; --line: #d8dfd6; --accent: #166534; --accent-dark: #0f4322; --danger: #9f1239; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(100% - 2rem, 920px); margin: 0 auto; padding: clamp(2rem, 7vw, 5rem) 0; }}
    .intro {{ max-width: 680px; margin-bottom: 2rem; }}
    h1, h2, h3 {{ line-height: 1.15; letter-spacing: -0.02em; }}
    h1 {{ max-width: 13ch; margin: .2rem 0 1rem; font-size: clamp(2.3rem, 8vw, 4.6rem); }}
    h2 {{ margin-top: 0; font-size: clamp(1.65rem, 4vw, 2.4rem); }}
    h3 {{ margin-top: 2rem; font-size: 1.05rem; }}
    .eyebrow {{ margin: 0; color: var(--accent); font-size: .78rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }}
    .lede, .fine-print {{ color: var(--muted); }}
    .grid {{ display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 1.1rem; padding: clamp(1rem, 3vw, 1.6rem); box-shadow: 0 12px 35px rgba(23, 34, 29, .06); }}
    form {{ display: grid; gap: 1.25rem; }}
    fieldset {{ min-width: 0; margin: 0; padding: 0; border: 0; }}
    legend {{ margin-bottom: .9rem; font-size: 1.1rem; font-weight: 750; }}
    label {{ display: block; margin-bottom: .35rem; font-weight: 650; }}
    .help {{ margin: .25rem 0 0; color: var(--muted); font-size: .88rem; }}
    input, select, button {{ width: 100%; min-height: 2.9rem; border: 1px solid #aebbb0; border-radius: .65rem; background: #fff; color: var(--ink); font: inherit; padding: .65rem .75rem; }}
    input:focus, select:focus, button:focus-visible {{ outline: 3px solid rgba(22, 101, 52, .27); outline-offset: 2px; border-color: var(--accent); }}
    .consent {{ display: flex; align-items: flex-start; gap: .7rem; }}
    .consent input {{ width: 1.25rem; min-width: 1.25rem; height: 1.25rem; min-height: 1.25rem; margin-top: .2rem; padding: 0; accent-color: var(--accent); }}
    .consent label {{ margin: 0; font-weight: 500; }}
    button {{ width: auto; cursor: pointer; border-color: var(--accent); background: var(--accent); color: white; font-weight: 750; padding-inline: 1.2rem; }}
    button:hover {{ background: var(--accent-dark); }}
    .privacy {{ border-left: .3rem solid var(--accent); background: #edf6ee; }}
    .privacy strong {{ color: var(--accent-dark); }}
    .feedback {{ margin: 1rem 0 0; border-radius: .6rem; padding: .7rem .85rem; }}
    .feedback.error {{ background: #fff1f2; color: var(--danger); }}
    .result {{ margin-top: 1.5rem; border: 2px solid var(--accent); border-radius: 1.1rem; background: var(--panel); padding: clamp(1rem, 3vw, 1.6rem); }}
    .result h2 {{ color: var(--accent-dark); }}
    .summary {{ font-size: 1.2rem; font-weight: 700; }}
    .result-facts {{ display: grid; gap: .7rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 1.25rem 0; }}
    .result-facts div {{ padding: .7rem; border-radius: .6rem; background: #f0f4ee; }}
    dt {{ color: var(--muted); font-size: .83rem; }}
    dd {{ margin: .1rem 0 0; font-weight: 750; overflow-wrap: anywhere; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: .65rem .5rem; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }}
    footer {{ margin-top: 2rem; color: var(--muted); font-size: .88rem; }}
    @media (max-width: 640px) {{ .grid, .result-facts {{ grid-template-columns: 1fr; }} main {{ width: min(100% - 1rem, 920px); }} .panel {{ border-radius: .8rem; }} }}
  </style>
</head>
<body>
  <main>
    <header class="intro">
      <p class="eyebrow">OathCast · Track 3</p>
      <h1>Make the outdoor call with evidence.</h1>
      <p class="lede">Describe one activity, place, and time. A configured decision runner returns one clear action: go, delay, relocate, or contingency.</p>
    </header>

    <section class="panel privacy" aria-labelledby="privacy-heading">
      <h2 id="privacy-heading">Consent and privacy</h2>
      <p><strong>Use only details you are comfortable sending.</strong> The service uses this information for one decision request. Do not enter names, contact details, medical information, wallet addresses, private keys, payment credentials, or other secrets. We do not need an account.</p>
      <p class="fine-print">The public service does not create or sign payments. If real Telegraph routing and payment are not configured, a decision is unavailable rather than guessed.</p>
    </section>

    <section class="panel" aria-labelledby="form-heading" style="margin-top: 1rem;">
      <h2 id="form-heading">Your decision</h2>
      <form id="decision-form" novalidate>
        <fieldset>
          <legend>What are you planning?</legend>
          <div class="grid">
            <div>
              <label for="activity">Outdoor activity</label>
              <input id="activity" name="activity" type="text" maxlength="120" autocomplete="off" placeholder="Trail run, picnic, football" required>
            </div>
            <div>
              <label for="location">Location</label>
              <input id="location" name="location" type="text" maxlength="200" autocomplete="off" placeholder="Park or neighborhood" required>
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend>Where and when?</legend>
          <div class="grid">
            <div>
              <label for="latitude">Latitude</label>
              <input id="latitude" name="latitude" type="number" min="-90" max="90" step="any" inputmode="decimal" placeholder="6.5244" required>
              <p class="help" id="latitude-help">Decimal degrees, from -90 to 90.</p>
            </div>
            <div>
              <label for="longitude">Longitude</label>
              <input id="longitude" name="longitude" type="number" min="-180" max="180" step="any" inputmode="decimal" placeholder="3.3792" required>
              <p class="help" id="longitude-help">Decimal degrees, from -180 to 180.</p>
            </div>
            <div>
              <label for="local_datetime">Local date and time</label>
              <input id="local_datetime" name="local_datetime" type="datetime-local" required>
              <p class="help" id="local-datetime-help">Choose the local time at the location.</p>
            </div>
            <div>
              <label for="timezone_offset">UTC offset</label>
              <select id="timezone_offset" name="timezone_offset" aria-describedby="local-datetime-help">{_offset_options()}</select>
              <p class="help">Use the location's offset at that date.</p>
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend>How much risk can you accept?</legend>
          <label for="risk_threshold_percent">Maximum acceptable risk (%)</label>
          <input id="risk_threshold_percent" name="risk_threshold_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" placeholder="30" required>
          <p class="help">This is a decision threshold, not a promise that conditions are safe.</p>
        </fieldset>

        <div class="consent">
          <input id="consent" name="consent" type="checkbox" required>
          <label for="consent">I consent to sending these planning details for this decision, and I have not included secrets or sensitive personal information.</label>
        </div>
        <button type="submit">Check the decision</button>
        <p class="feedback" id="feedback" role="status" aria-live="polite"></p>
      </form>
    </section>

    <div id="result" aria-live="polite">{result_markup}</div>
    {feedback}
    <noscript><p class="feedback error">JavaScript is required to send this JSON request. No external runtime dependency is used.</p></noscript>
    <footer>Decision responses show public Miner evidence only. Payment and wallet material never belongs in this interface.</footer>
  </main>
  <script>
    (() => {{
      const form = document.getElementById("decision-form");
      const feedback = document.getElementById("feedback");
      const result = document.getElementById("result");
      const setFeedback = (message, isError = false) => {{
        feedback.textContent = message;
        feedback.className = isError ? "feedback error" : "feedback";
      }};
      const addText = (parent, tag, value, className) => {{
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = value;
        parent.appendChild(node);
        return node;
      }};
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        if (!form.checkValidity()) {{
          form.reportValidity();
          return;
        }}
        const local = document.getElementById("local_datetime").value;
        const offset = document.getElementById("timezone_offset").value;
        const payload = {{
          activity: document.getElementById("activity").value,
          location: document.getElementById("location").value,
          latitude: Number(document.getElementById("latitude").value),
          longitude: Number(document.getElementById("longitude").value),
          local_datetime: local + offset,
          risk_threshold_percent: Number(document.getElementById("risk_threshold_percent").value),
          consent: document.getElementById("consent").checked
        }};
        setFeedback("Checking…");
        try {{
          const response = await fetch("{API_PATH}", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json", "Accept": "application/json" }},
            body: JSON.stringify(payload)
          }});
          const data = await response.json();
          if (!response.ok) {{
            setFeedback(data.message || "The decision service is unavailable.", true);
            return;
          }}
          result.replaceChildren();
          const section = document.createElement("section");
          section.className = "result";
          section.setAttribute("aria-labelledby", "live-result-heading");
          addText(section, "p", "Decision returned", "eyebrow");
          const heading = addText(section, "h2", String(data.action || data.decision || "unknown").toUpperCase());
          heading.id = "live-result-heading";
          addText(section, "p", data.summary || "No summary returned.", "summary");
          addText(section, "p", data.rationale || "No rationale returned.");
          const facts = document.createElement("dl");
          facts.className = "result-facts";
          const risk = document.createElement("div");
          addText(risk, "dt", "Risk estimate");
          addText(risk, "dd", data.risk_percent == null ? "—" : String(data.risk_percent) + "%");
          facts.appendChild(risk);
          const request = document.createElement("div");
          addText(request, "dt", "Request ID");
          addText(request, "dd", data.request_id || "—");
          facts.appendChild(request);
          section.appendChild(facts);
          addText(section, "h3", "Miner evidence");
          const evidence = Array.isArray(data.miner_evidence) ? data.miner_evidence : [];
          if (!evidence.length) {{
            addText(section, "p", "No public Miner evidence was returned.");
          }} else {{
            evidence.forEach((item) => {{
              const line = document.createElement("p");
              line.textContent = String(item.miner_id || "Miner") + " · " + String(item.status || "unknown") + " · " + (item.probability_percent == null ? "risk unavailable" : String(item.probability_percent) + "% risk");
              section.appendChild(line);
            }});
          }}
          result.appendChild(section);
          setFeedback("Decision received.");
        }} catch (error) {{
          setFeedback("The decision service could not be reached.", true);
        }}
      }});
    }})();
  </script>
</body>
</html>'''


class DecisionRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the page, health/status endpoints, and JSON API."""

    server_version = "OathCastDecisionUI/1"
    sys_version = ""

    @property
    def application(self) -> DecisionApplication:
        return self.server.application  # type: ignore[attr-defined]

    def _headers(self, *, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.close_connection = True
        self.send_response(status)
        self._headers(content_type="application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self._headers(content_type="text/html; charset=utf-8")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, message: str, *, error: str) -> None:
        self._send_json(status, {"error": error, "message": message})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._send_html(200, render_page())
            return
        if path in {HEALTH_PATH, STATUS_PATH}:
            self._send_json(200, self.application.status_payload())
            return
        self._error(404, "Not found.", error="not_found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path not in {API_PATH, "/decision", "/v1/decision"}:
            self._error(404, "Not found.", error="not_found")
            return

        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or (
            content_types[0].lower().split(";", 1)[0].strip() != "application/json"
        ):
            self._error(415, "Content-Type must be application/json.", error="unsupported_media_type")
            return
        transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
        if len(transfer_encodings) > 1 or (
            transfer_encodings
            and transfer_encodings[0].lower().strip() not in {"identity"}
        ):
            self.close_connection = True
            self._error(400, "Chunked request bodies are not supported.", error="invalid_request")
            return

        content_lengths = self.headers.get_all("Content-Length", [])
        if len(content_lengths) == 0:
            self._error(411, "Content-Length is required.", error="length_required")
            return
        if len(content_lengths) != 1:
            self.close_connection = True
            self._error(400, "Content-Length must be provided once.", error="invalid_request")
            return
        raw_length = content_lengths[0]
        try:
            length = int(raw_length, 10)
        except (TypeError, ValueError):
            self.close_connection = True
            self._error(400, "Content-Length must be a non-negative integer.", error="invalid_request")
            return
        if length < 0:
            self.close_connection = True
            self._error(400, "Content-Length must be a non-negative integer.", error="invalid_request")
            return
        if length > self.application.max_body_bytes:
            self.close_connection = True
            self._error(413, "JSON request body is too large.", error="body_too_large")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self._error(400, "Request body was truncated.", error="invalid_request")
            return

        try:
            payload = decode_json_body(body)
            request = parse_decision_input(payload)
        except ValidationError as exc:
            self._send_json(422, exc.to_public_dict())
            return

        if not self.application.runner_configured:
            self._error(
                503,
                "Live Telegraph routing and payment are not configured.",
                error="decision_unavailable",
            )
            return
        try:
            result = self.application.decide(request)
        except TelegraphNotConfigured:
            self._error(
                503,
                "Live Telegraph routing and payment are not configured.",
                error="decision_unavailable",
            )
            return
        except DecisionUnavailable:
            self._error(503, "The decision service is unavailable.", error="decision_unavailable")
            return
        except DecisionContractError:
            self._error(503, "The decision service returned no usable decision.", error="decision_unavailable")
            return
        except Exception:
            self._error(503, "The decision service is unavailable.", error="decision_unavailable")
            return
        self._send_json(200, self.application.public_result(request, result))

    def log_message(self, format: str, *args: Any) -> None:
        # Request bodies and query strings never go to stdout/stderr.  A
        # deployment can attach its own access logger at the server boundary.
        return


class DecisionHTTPServer(ThreadingHTTPServer):
    """Threaded stdlib server carrying an injected application instance."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        decision_runner: DecisionRunner | Callable[[DecisionInput], Any] | None = None,
        max_body_bytes: int = MAX_JSON_BODY_BYTES,
    ) -> None:
        self.application = DecisionApplication(
            decision_runner,
            max_body_bytes=max_body_bytes,
        )
        super().__init__(server_address, DecisionRequestHandler)


def make_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    decision_runner: DecisionRunner | Callable[[DecisionInput], Any] | None = None,
    max_body_bytes: int = MAX_JSON_BODY_BYTES,
) -> DecisionHTTPServer:
    """Build a server; callers may inject a real, already-authorized runner."""

    return DecisionHTTPServer(
        (host, port),
        decision_runner=decision_runner,
        max_body_bytes=max_body_bytes,
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    decision_runner: DecisionRunner | Callable[[DecisionInput], Any] | None = None,
    max_body_bytes: int = MAX_JSON_BODY_BYTES,
) -> None:
    """Run until interrupted.  No runner means a deliberately unavailable API."""

    server = make_server(
        host,
        port,
        decision_runner=decision_runner,
        max_body_bytes=max_body_bytes,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = [
    "API_PATH",
    "DECISION_ACTIONS",
    "DecisionApplication",
    "DecisionContractError",
    "DecisionHTTPServer",
    "DecisionInput",
    "DecisionRequestHandler",
    "DecisionResult",
    "DecisionRunner",
    "DecisionUnavailable",
    "EVIDENCE_STATUSES",
    "MAX_BODY_BYTES",
    "MAX_JSON_BODY_BYTES",
    "MinerEvidence",
    "TelegraphDecisionRunner",
    "TelegraphNotConfigured",
    "ValidationError",
    "decode_json_body",
    "make_server",
    "parse_decision_input",
    "render_decision_result",
    "render_page",
    "run_server",
]
