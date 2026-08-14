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
from pathlib import Path
from typing import Any, Protocol
import json
import math
import re
import uuid
from urllib.parse import urlsplit

from oathcast.release import current_release


SERVICE_NAME = "oathcast-decision-ui"
API_PATH = "/api/decision"
HEALTH_PATH = "/health"
STATUS_PATH = "/status"
LOGO_PATH = "/assets/oathcast-mark.webp"
LOGO_FILE = Path(__file__).with_name("assets") / "oathcast-mark.webp"
LOGO_VERSION = "16fae356"

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
    """Capability-bearing seam for a real Telegraph-backed implementation."""

    @property
    def configured(self) -> bool:
        ...

    @property
    def telegraph_configured(self) -> bool:
        ...

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
        decision_runner: DecisionRunner | None = None,
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
        # A bare callable is not proof that real Telegraph routing and payment
        # are ready. Public execution requires an explicit capability-bearing
        # runner so an accidentally injected fixture cannot enable the API.
        return bool(
            getattr(self.decision_runner, "configured", False)
            and getattr(self.decision_runner, "telegraph_configured", False)
            and (
                callable(self.decision_runner)
                or callable(getattr(self.decision_runner, "run", None))
            )
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
            "public_mode": "read_only_fixture",
            "api_mode": "live_decisions" if ready else "fail_closed",
            "fixture_available": True,
            "live_decision_available": ready,
            "decision_api_available": ready,
            "telegraph_routing_and_payment_configured": self.telegraph_configured,
            "release": current_release().to_dict(),
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


def _format_percent(value: float | None) -> str:
    return "Not available" if value is None else f"{value:g}%"


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
        f'<div><dt>Request ID</dt><dd>{escape(safe.request_id or "Not available")}</dd></div>'
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
    """Return the accessible, dependency-free public status and fixture page."""

    feedback = ""
    if error:
        feedback = f'<p class="feedback error" role="alert">{escape(error)}</p>'
    result_markup = render_decision_result(result) if result is not None else ""
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OathCast public status and development fixture</title>
  <meta name="description" content="OathCast public release status and a clearly labeled, client-only development fixture.">
  <style>
    :root {{ color-scheme: dark; --ink: #f4f1ed; --muted: #aaa6a2; --paper: #000000; --panel: #080808; --panel-strong: #0e0e0e; --line: #282828; --line-strong: #3a3a3a; --accent: #d82335; --accent-bright: #f04452; --accent-soft: #26070b; --focus: #ff5a66; --danger: #ff8a94; --danger-soft: #26070b; --shadow: rgba(216, 35, 53, .12); }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ min-height: 100dvh; margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.58 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body::before {{ content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none; background-image: linear-gradient(rgba(255,255,255,.026) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.026) 1px, transparent 1px); background-size: 48px 48px; mask-image: linear-gradient(to bottom, black 0, transparent 54rem); }}
    a {{ color: var(--ink); text-decoration-color: var(--accent); text-underline-offset: .24em; }}
    button, input, select {{ font: inherit; }}
    button, a {{ -webkit-tap-highlight-color: transparent; }}
    .shell {{ width: min(100% - 2rem, 1160px); margin: 0 auto; padding: 1.25rem 0 3rem; }}
    .site-header {{ min-height: 4.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; border-bottom: 1px solid var(--line); }}
    .brand {{ display: inline-flex; align-items: center; gap: .5rem; margin: 0; font-size: 1.05rem; font-weight: 860; letter-spacing: 0; }}
    .brand-mark {{ width: 2.4rem; height: 2.4rem; object-fit: contain; flex: 0 0 auto; filter: drop-shadow(0 0 .5rem rgba(216, 35, 53, .14)); }}
    .status-link {{ font-size: .86rem; font-weight: 720; }}
    .skip-link {{ position: fixed; top: .75rem; left: .75rem; z-index: 2; transform: translateY(-5rem); border: 1px solid var(--accent); border-radius: .3rem; background: var(--paper); padding: .65rem .85rem; font-weight: 800; transition: transform .18s ease; }}
    .skip-link:focus {{ transform: translateY(0); }}
    main {{ display: grid; gap: clamp(1.5rem, 3vw, 2.5rem); padding-top: clamp(3rem, 8vw, 7rem); }}
    h1, h2, h3 {{ line-height: 1.12; letter-spacing: 0; }}
    h1 {{ max-width: 13ch; margin: 1rem 0 1.15rem; font-size: 6.25rem; line-height: .94; text-wrap: balance; }}
    h2 {{ margin: 0 0 .75rem; font-size: 2.65rem; text-wrap: balance; }}
    h3 {{ margin: 0; font-size: 1.08rem; }}
    p {{ max-width: 68ch; }}
    .lede, .supporting, .help, footer {{ color: var(--muted); }}
    .lede {{ margin: 0; font-size: 1.22rem; }}
    .semantic-status {{ display: inline-flex; align-items: center; min-height: 2rem; border: 1px solid #6e111a; border-radius: .3rem; background: var(--accent-soft); color: #ff8c96; padding: .32rem .62rem; font-size: .78rem; font-weight: 820; }}
    .hero {{ position: relative; display: grid; gap: 2rem; grid-template-columns: minmax(0, 1.55fr) minmax(17rem, .45fr); align-items: end; padding-bottom: clamp(2rem, 5vw, 4rem); border-bottom: 1px solid var(--line); }}
    .hero::after {{ content: "42"; position: absolute; right: 0; top: -4.5rem; z-index: -1; color: rgba(216, 35, 53, .11); font-size: 20rem; font-weight: 900; line-height: 1; letter-spacing: 0; font-variant-numeric: tabular-nums; }}
    .hero-status {{ border-left: .22rem solid var(--accent); padding: .45rem 0 .45rem 1rem; }}
    .hero-status strong {{ display: block; margin-bottom: .25rem; }}
    .panel {{ border: 1px solid var(--line); border-radius: .45rem; background: var(--panel); padding: clamp(1.25rem, 3vw, 2.25rem); box-shadow: 0 1.5rem 4rem var(--shadow); }}
    .status-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--line-strong); }}
    .status-item {{ min-height: 8.5rem; border-bottom: 1px solid var(--line); padding: 1.25rem 1rem 1.25rem 0; }}
    .status-item:nth-child(odd) {{ border-right: 1px solid var(--line); }}
    .status-item:nth-child(even) {{ padding-left: 1.25rem; }}
    .status-item strong {{ display: block; margin-bottom: .35rem; }}
    .status-item p {{ margin: 0; color: var(--muted); }}
    .available {{ color: var(--ink); }}
    .unavailable {{ color: #ff7883; }}
    .fixture-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }}
    .fixture-note {{ border-left: .2rem solid var(--accent); background: #100304; color: #d8d1ce; padding: .82rem 1rem; font-size: .9rem; }}
    .fixture-layout {{ display: grid; gap: 1.25rem; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); margin-top: 1.25rem; }}
    .fixture-controls {{ display: grid; gap: 1rem; }}
    label {{ display: block; margin-bottom: .35rem; font-weight: 720; }}
    input, select, button {{ width: 100%; min-height: 3rem; border: 1px solid var(--line-strong); border-radius: .35rem; background: #030303; color: var(--ink); padding: .68rem .78rem; }}
    input {{ font-variant-numeric: tabular-nums; }}
    input:focus, select:focus, button:focus-visible, a:focus-visible {{ outline: 3px solid rgba(255, 90, 102, .42); outline-offset: 2px; border-color: var(--focus); }}
    @supports (outline-color: color-mix(in srgb, black 50%, white)) {{ input:focus, select:focus, button:focus-visible, a:focus-visible {{ outline-color: color-mix(in srgb, var(--focus) 38%, transparent); }} }}
    button {{ width: auto; cursor: pointer; border-color: var(--accent); background: var(--accent); color: #ffffff; font-weight: 800; padding-inline: 1.1rem; transition: background-color .18s ease, border-color .18s ease, transform .18s ease; }}
    button:hover {{ border-color: var(--accent-bright); background: var(--accent-bright); }}
    button:active {{ transform: translateY(1px); }}
    button[disabled] {{ cursor: not-allowed; border-color: var(--line-strong); background: #090909; color: #706d6b; transform: none; }}
    .button-row {{ display: flex; align-items: center; flex-wrap: wrap; gap: .75rem; }}
    .result {{ border: 1px solid var(--line-strong); border-radius: .4rem; background: var(--panel-strong); padding: clamp(1.15rem, 3vw, 1.7rem); }}
    .result .semantic-status {{ border-color: var(--line-strong); background: #050505; color: var(--muted); }}
    .result h3 {{ margin-top: 1rem; font-size: 2rem; }}
    .summary {{ font-size: 1.12rem; font-weight: 750; }}
    .result-facts {{ display: grid; gap: .7rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 1.15rem 0; }}
    .result-facts div {{ border-top: 1px solid var(--line-strong); background: transparent; padding: .75rem 0; }}
    dt {{ color: var(--muted); font-size: .82rem; }}
    dd {{ margin: .1rem 0 0; font-weight: 760; overflow-wrap: anywhere; }}
    .limits {{ display: grid; gap: .65rem; margin: 1rem 0 0; padding: 0; list-style: none; }}
    .limits li {{ padding-left: 1rem; border-left: .18rem solid #4a1218; }}
    .feedback {{ min-height: 1.5rem; margin: 0; font-size: .9rem; }}
    .feedback.error {{ border-radius: .65rem; background: var(--danger-soft); color: var(--danger); padding: .7rem .85rem; }}
    .live-disabled {{ position: relative; overflow: hidden; border-color: #621018; background: #0d0203; }}
    .live-disabled::after {{ content: "LOCKED"; position: absolute; right: 1rem; bottom: -1.7rem; color: rgba(216,35,53,.14); font-size: 8rem; font-weight: 900; line-height: 1; letter-spacing: 0; }}
    .live-disabled > * {{ position: relative; z-index: 1; }}
    .live-disabled .supporting {{ color: #d39aa0; }}
    .live-disabled p {{ margin-bottom: 0; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: .65rem .5rem; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: .78rem; letter-spacing: 0; }}
    footer {{ margin-top: 1rem; padding-top: 1.25rem; border-top: 1px solid var(--line); font-size: .88rem; }}
    input, select {{ color-scheme: dark; }}
    @media (max-width: 960px) {{
      h1 {{ font-size: 4.75rem; }}
      h2 {{ font-size: 2.15rem; }}
      .hero::after {{ font-size: 16rem; }}
    }}
    @media (max-width: 760px) {{
      .shell {{ width: min(100% - 1.25rem, 1160px); }}
      .site-header {{ align-items: flex-start; padding: .9rem 0; }}
      .hero, .fixture-layout, .status-grid, .result-facts {{ grid-template-columns: 1fr; }}
      .fixture-head {{ display: block; }}
      .fixture-head .semantic-status {{ margin-bottom: .75rem; }}
      h1 {{ max-width: 11ch; font-size: 3rem; }}
      h2 {{ font-size: 1.65rem; }}
      .lede {{ font-size: 1.04rem; }}
      .result h3 {{ font-size: 1.45rem; }}
      .hero::after {{ top: -1rem; font-size: 12rem; }}
      .live-disabled::after {{ font-size: 4rem; }}
      .status-item, .status-item:nth-child(even) {{ border-right: 0; padding: 1.1rem 0; }}
      .panel {{ border-radius: .35rem; }}
      .button-row button {{ width: 100%; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} * {{ transition: none !important; }} }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to content</a>
  <div class="shell">
    <header class="site-header">
      <p class="brand"><img class="brand-mark" src="{LOGO_PATH}?v={LOGO_VERSION}" width="192" height="192" alt="" aria-hidden="true">OathCast</p>
      <a class="status-link" href="{STATUS_PATH}">Machine-readable status</a>
    </header>
    <main id="main-content">
      <section class="hero" aria-labelledby="page-heading">
        <div>
          <span class="semantic-status">Live decisions unavailable</span>
          <h1 id="page-heading">OathCast is online. Live decisions are not.</h1>
          <p class="lede">The Miner is registered and active. This interface stays read-only while paid Application flows remain disabled.</p>
        </div>
        <div class="hero-status" role="status">
          <strong>Current public mode</strong>
          <span>Miner live. Fixture local. Decision API closed.</span>
        </div>
      </section>

      <section aria-labelledby="availability-heading">
        <h2 id="availability-heading">What is available now</h2>
        <div class="status-grid">
          <div class="status-item">
            <strong class="available">Public Miner</strong>
            <p>The authenticated forecast service is deployed separately and reports its release identity.</p>
          </div>
          <div class="status-item">
            <strong class="available">Development fixture</strong>
            <p>A static example demonstrates the intended decision language without a network request.</p>
          </div>
          <div class="status-item">
            <strong class="available">Telegraph registration</strong>
            <p>Active as on-chain registration ID 78 and dispatcher routing ID 64173 under WEATHER_FORECAST.</p>
          </div>
          <div class="status-item">
            <strong class="unavailable">Paid Application requests</strong>
            <p>No wallet signing, payment composition, live Application intake, or qualifying demand is enabled here.</p>
          </div>
        </div>
      </section>

      <section class="panel" aria-labelledby="fixture-heading">
        <div class="fixture-head">
          <span class="semantic-status">Development fixture</span>
          <div>
            <h2 id="fixture-heading">Try the decision presentation</h2>
            <p class="supporting">Adjust the sample risk and threshold. The result is calculated only in this browser from those two values.</p>
          </div>
        </div>
        <p class="fixture-note"><strong>This example is not Telegraph-routed.</strong> It makes no payment, creates no qualifying demand, and is not a safety guarantee.</p>
        <div class="fixture-layout">
          <div class="fixture-controls" id="fixture-controls">
            <div>
              <label for="fixture-risk">Example rain risk (%)</label>
              <input id="fixture-risk" type="number" min="0" max="100" step="1" value="42" inputmode="numeric" required aria-describedby="fixture-risk-help">
              <p class="help" id="fixture-risk-help">Development input only. No provider is called.</p>
            </div>
            <div>
              <label for="fixture-threshold">Example decision threshold (%)</label>
              <input id="fixture-threshold" type="number" min="0" max="100" step="1" value="30" inputmode="numeric" required aria-describedby="fixture-threshold-help">
              <p class="help" id="fixture-threshold-help">At or above the threshold, the example recommends a contingency.</p>
            </div>
            <div class="button-row">
              <button id="fixture-update" type="button">Update example</button>
            </div>
            <p class="feedback" id="fixture-feedback" role="status" aria-live="polite">Example ready.</p>
          </div>
          <section class="result" id="fixture-result" aria-labelledby="fixture-result-heading">
            <span class="semantic-status">Static example</span>
            <h3 id="fixture-result-heading">Example outcome: CONTINGENCY</h3>
            <p class="summary" id="fixture-summary">Prepare a covered alternative for the sample outdoor activity.</p>
            <p id="fixture-rationale">The development risk of 42% is at or above the example threshold of 30%.</p>
            <dl class="result-facts">
              <div><dt>Example risk</dt><dd id="fixture-risk-output">42%</dd></div>
              <div><dt>Example threshold</dt><dd id="fixture-threshold-output">30%</dd></div>
            </dl>
            <ul class="limits">
              <li>Development fixture</li>
              <li>Not Telegraph-routed</li>
              <li>No payment</li>
              <li>Not qualifying demand</li>
              <li>Not a safety guarantee</li>
            </ul>
          </section>
        </div>
      </section>

      <section class="panel live-disabled" aria-labelledby="live-heading">
        <h2 id="live-heading">Live Planning Desk intake is disabled</h2>
        <p>No personal planning details are accepted from this public page. The live action stays unavailable until reviewed Telegraph routing, payment authorization, and evidence handling are deliberately enabled.</p>
        <div class="button-row">
          <button type="button" disabled aria-describedby="live-disabled-reason">Run live decision</button>
          <span id="live-disabled-reason" class="supporting">Unavailable in this release</span>
        </div>
      </section>

      <div id="result" aria-live="polite">{result_markup}</div>
      {feedback}
      <noscript><p class="feedback error">JavaScript is only needed to update the local development fixture. No live request is available.</p></noscript>
      <footer>OathCast does not expose wallet material here. Fixture activity is local to the browser and is never counted as Telegraph traffic.</footer>
    </main>
  </div>
  <script>
    (() => {{
      const updateButton = document.getElementById("fixture-update");
      const riskInput = document.getElementById("fixture-risk");
      const thresholdInput = document.getElementById("fixture-threshold");
      const heading = document.getElementById("fixture-result-heading");
      const summary = document.getElementById("fixture-summary");
      const rationale = document.getElementById("fixture-rationale");
      const riskOutput = document.getElementById("fixture-risk-output");
      const thresholdOutput = document.getElementById("fixture-threshold-output");
      const feedback = document.getElementById("fixture-feedback");
      updateButton.addEventListener("click", () => {{
        const risk = Number(riskInput.value);
        const threshold = Number(thresholdInput.value);
        if (riskInput.value.trim() === "" || thresholdInput.value.trim() === "" || !Number.isFinite(risk) || !Number.isFinite(threshold) || risk < 0 || risk > 100 || threshold < 0 || threshold > 100) {{
          riskInput.setAttribute("aria-invalid", String(riskInput.value.trim() === "" || !Number.isFinite(risk) || risk < 0 || risk > 100));
          thresholdInput.setAttribute("aria-invalid", String(thresholdInput.value.trim() === "" || !Number.isFinite(threshold) || threshold < 0 || threshold > 100));
          feedback.textContent = "Use values from 0 to 100.";
          feedback.className = "feedback error";
          return;
        }}
        riskInput.setAttribute("aria-invalid", "false");
        thresholdInput.setAttribute("aria-invalid", "false");
        const contingency = risk >= threshold;
        heading.textContent = "Example outcome: " + (contingency ? "CONTINGENCY" : "GO");
        summary.textContent = contingency
          ? "Prepare a covered alternative for the sample outdoor activity."
          : "The sample activity stays within the selected development threshold.";
        rationale.textContent = "The development risk of " + risk + "% is " + (contingency ? "at or above" : "below") + " the example threshold of " + threshold + "%.";
        riskOutput.textContent = risk + "%";
        thresholdOutput.textContent = threshold + "%";
        feedback.textContent = "Local example updated. No request was sent.";
        feedback.className = "feedback";
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

    def _headers(self, *, content_type: str, cache_control: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
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
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_logo(self) -> None:
        try:
            body = LOGO_FILE.read_bytes()
        except OSError:
            self._error(404, "Not found.", error="not_found")
            return
        self.close_connection = True
        self.send_response(200)
        self._headers(
            content_type="image/webp",
            cache_control="public, max-age=31536000, immutable",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, *, error: str) -> None:
        self._send_json(status, {"error": error, "message": message})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._send_html(200, render_page())
            return
        if path == LOGO_PATH:
            self._send_logo()
            return
        if path in {HEALTH_PATH, STATUS_PATH}:
            self._send_json(200, self.application.status_payload())
            return
        self._error(404, "Not found.", error="not_found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path != API_PATH:
            self._error(404, "Not found.", error="not_found")
            return

        # The public release has no live intake. Reject before inspecting
        # metadata or reading a body so direct HTTP clients cannot submit
        # planning details to a disabled integration.
        if not self.application.runner_configured:
            self._error(
                503,
                "Live Telegraph routing and payment are not configured.",
                error="decision_unavailable",
            )
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
        decision_runner: DecisionRunner | None = None,
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
    decision_runner: DecisionRunner | None = None,
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
    decision_runner: DecisionRunner | None = None,
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
    "LOGO_PATH",
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
