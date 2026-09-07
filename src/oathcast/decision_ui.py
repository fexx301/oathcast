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
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
import base64
import hashlib
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
try:
    _LOGO_BYTES = LOGO_FILE.read_bytes()
except OSError:
    _LOGO_BYTES = None

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
        r"(?i)\b(?:private[ _-]?key|mnemonic|seed[ _-]?phrase|xpriv|access[ _-]?token|authorization|bearer)\b[^\n]*",
        r"(?i)\bsecret\b(?:\s*[:=]\s*[^\s,;]+)?",
        r"(?i)\bwallet(?:[ _-]?(?:key|secret|address|credential|material))?\b(?:\s*[:=]\s*[^\s,;]+)?",
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


class DemoDecisionRunner:
    """Deterministic, payment-free runner used for a judge-friendly preview.

    The demo is intentionally a separate capability from the Telegraph path.
    It gives a reviewer a complete interaction to try without suggesting that
    a local calculation is live weather data, protocol traffic, or demand.
    """

    public_mode = "demo"

    @property
    def configured(self) -> bool:
        return True

    @property
    def telegraph_configured(self) -> bool:
        return False

    def __call__(self, request: DecisionInput) -> DecisionResult:
        seed = "|".join(
            (
                request.activity,
                request.location,
                f"{request.latitude:.6f}",
                f"{request.longitude:.6f}",
                request.local_datetime.isoformat(),
            )
        ).encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        # Keep the preview varied while preserving a stable result for the
        # same brief. This is a scenario generator, not a weather model.
        risk_percent = round(18 + (int.from_bytes(digest[:4], "big") % 6501) / 100, 2)
        contingency = risk_percent >= request.risk_threshold_percent
        action = "contingency" if contingency else "go"
        return DecisionResult(
            action=action,
            summary=(
                f"Demo estimate: {risk_percent:g}% precipitation risk for the selected hour."
            ),
            rationale=(
                f"The local scenario is {'at or above' if contingency else 'below'} "
                f"your {request.risk_threshold_percent:g}% threshold."
            ),
            risk_percent=risk_percent,
            miner_evidence=(
                MinerEvidence(
                    miner_id="local-demo",
                    status="unknown",
                    probability_percent=risk_percent,
                ),
            ),
        )


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
        configured = bool(getattr(self.decision_runner, "configured", False))
        callable_runner = callable(self.decision_runner) or callable(
            getattr(self.decision_runner, "run", None)
        )
        # The only non-Telegraph mode that can open the public API is the
        # explicit, payment-free demo runner. Everything else stays closed.
        demo = getattr(self.decision_runner, "public_mode", None) == "demo"
        telegraph = bool(getattr(self.decision_runner, "telegraph_configured", False))
        return bool(configured and callable_runner and (demo or telegraph))

    @property
    def telegraph_configured(self) -> bool:
        return bool(getattr(self.decision_runner, "telegraph_configured", False))

    @property
    def public_mode(self) -> str:
        if not self.runner_configured:
            return "read_only_fixture"
        if getattr(self.decision_runner, "public_mode", None) == "demo":
            return "demo"
        return "live"

    def status_payload(self) -> dict[str, Any]:
        ready = self.runner_configured
        mode = self.public_mode
        return {
            "service": SERVICE_NAME,
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "runner_configured": ready,
            "public_mode": mode,
            "api_mode": (
                "demo"
                if mode == "demo"
                else "live_decisions"
                if mode == "live"
                else "fail_closed"
            ),
            "fixture_available": True,
            "interactive_demo_available": mode == "demo",
            "live_decision_available": mode == "live",
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


def _render_page(*, result: DecisionResult | None = None, error: str | None = None) -> str:
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
            <p>Active as on-chain registration ID 245 and dispatcher routing ID 64173 under WEATHER_FORECAST.</p>
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


def _render_interactive_page(*, mode: str) -> str:
    """Render the judge-facing Planning Desk for demo or live mode."""

    live = mode == "live"
    mode_label = "Live Telegraph route" if live else "Interactive demo"
    mode_class = "live-mode" if live else "demo-mode"
    mode_title = (
        "Make the call before the weather changes."
        if live
        else "Make a plan before the weather changes."
    )
    mode_copy = (
        "Describe one outdoor decision, choose the hour that matters, and receive a "
        "transparent risk decision backed by the configured Telegraph Application path."
        if live
        else "Describe one outdoor decision, choose the hour that matters, and see how "
        "OathCast turns a forecast signal into a clear next step."
    )
    notice = (
        "This brief is routed through the private Application boundary. It is planning "
        "support, not a safety guarantee."
        if live
        else "This is a payment-free local scenario. It never calls Telegraph, spends funds, "
        "or counts as protocol demand."
    )
    consent_label = (
        "I agree to send this planning brief through OathCast's live Application route."
        if live
        else "I understand this is a local demonstration and not live weather data."
    )
    submit_label = "Run live decision" if live else "Run interactive preview"
    footer = (
        "Live route enabled by the operator. Wallet material stays outside this browser. "
        "Use the result as non-binding planning support."
        if live
        else "Demo mode is intentionally separate from Telegraph traffic. No wallet material "
        "is exposed, and no request leaves this application."
    )
    planned = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    hour_options = "".join(
        f'<option value="{hour:02d}"{' selected' if hour == planned.hour else ''}>{hour:02d}:00</option>'
        for hour in range(24)
    )
    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OathCast Planning Desk</title>
  <meta name="description" content="Use OathCast to turn a weather forecast into a clear outdoor planning decision.">
  <style>
    :root { color-scheme: dark; --ink:#f4f7f8; --muted:#a6b0b5; --faint:#6e7a80; --paper:#071014; --panel:#0c171c; --panel-2:#101e24; --line:#203139; --line-strong:#36505a; --sky:#61d5f5; --sky-deep:#153c4b; --amber:#f3bf6a; --green:#77e2a6; --danger:#ff9d9d; --danger-bg:#351c20; --shadow:rgba(0,0,0,.28); }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { min-height:100dvh; margin:0; background:var(--paper); color:var(--ink); font:16px/1.58 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none; opacity:.32; background-image:linear-gradient(rgba(97,213,245,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(97,213,245,.05) 1px,transparent 1px); background-size:48px 48px; mask-image:linear-gradient(to bottom,black,transparent 52rem); }
    a { color:var(--ink); text-underline-offset:.22em; text-decoration-color:var(--sky); }
    button,input,select { font:inherit; }
    button,a { -webkit-tap-highlight-color:transparent; }
    button { touch-action:manipulation; }
    .shell { width:min(100% - 2rem,1180px); margin:0 auto; padding:1rem 0 4rem; }
    .topbar { min-height:4.5rem; display:flex; align-items:center; justify-content:space-between; gap:1rem; border-bottom:1px solid var(--line); }
    .brand { display:inline-flex; align-items:center; gap:.65rem; margin:0; font-weight:850; letter-spacing:.02em; }
    .brand-mark { width:2.5rem; height:2.5rem; object-fit:contain; filter:drop-shadow(0 0 .65rem rgba(97,213,245,.12)); }
    .status-link { color:var(--muted); font-size:.86rem; font-weight:700; }
    .skip-link { position:fixed; top:.75rem; left:.75rem; z-index:20; transform:translateY(-5rem); border:1px solid var(--sky); background:var(--paper); padding:.7rem .9rem; font-weight:800; }
    .skip-link:focus { transform:translateY(0); }
    main { display:grid; gap:clamp(1.5rem,4vw,3.5rem); padding-top:clamp(2.5rem,7vw,6rem); }
    .eyebrow,.meta { color:var(--faint); font:700 .75rem/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.14em; text-transform:uppercase; }
    .hero { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(18rem,.65fr); gap:clamp(2rem,6vw,6rem); align-items:end; padding-bottom:clamp(2rem,5vw,4rem); border-bottom:1px solid var(--line); }
    .hero-kicker { display:flex; align-items:center; gap:.65rem; color:var(--sky); }
    .hero-kicker svg { width:1.1rem; height:1.1rem; flex:0 0 auto; }
    .mode-badge { display:inline-flex; align-items:center; gap:.45rem; width:max-content; margin-top:1.5rem; border:1px solid var(--line-strong); border-radius:999px; padding:.38rem .7rem; color:var(--ink); font-size:.8rem; font-weight:800; }
    .mode-badge::before { content:""; width:.5rem; height:.5rem; border-radius:50%; background:var(--amber); box-shadow:0 0 .6rem currentColor; }
    .live-mode { border-color:rgba(119,226,166,.55); color:var(--green); }
    .live-mode::before { background:var(--green); }
    h1,h2,h3 { line-height:1.08; letter-spacing:-.035em; }
    h1 { max-width:13ch; margin:1rem 0 1.25rem; font-size:clamp(3rem,6.2vw,5.8rem); text-wrap:balance; }
    h2 { margin:0 0 .75rem; font-size:clamp(1.8rem,4vw,3.2rem); text-wrap:balance; }
    h3 { margin:0; font-size:1.05rem; }
    p { max-width:68ch; }
    .lede { max-width:62ch; margin:0; color:var(--muted); font-size:clamp(1.05rem,1.8vw,1.28rem); }
    .hero-aside { display:grid; gap:1rem; border-left:2px solid var(--sky); padding-left:1.15rem; }
    .hero-aside strong { display:block; margin-bottom:.25rem; }
    .hero-aside p { margin:0; color:var(--muted); }
    .steps { display:grid; gap:.9rem; margin-top:1.2rem; }
    .step { display:grid; grid-template-columns:2rem 1fr; gap:.75rem; align-items:start; }
    .step-number { display:grid; place-items:center; width:2rem; height:2rem; border:1px solid var(--line-strong); border-radius:50%; color:var(--sky); font:700 .8rem ui-monospace,SFMono-Regular,Menlo,monospace; }
    .step p { margin:.18rem 0 0; color:var(--muted); font-size:.9rem; }
    .section-heading { display:flex; justify-content:space-between; align-items:end; gap:1rem; margin-bottom:1.1rem; }
    .section-heading p { margin:0; color:var(--muted); }
    .workbench { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(18rem,.9fr); gap:1.25rem; }
    .panel { border:1px solid var(--line); border-radius:.55rem; background:linear-gradient(145deg,rgba(16,30,36,.96),rgba(8,18,22,.96)); box-shadow:0 1.5rem 4rem var(--shadow); padding:clamp(1.15rem,3vw,2rem); }
    form { display:grid; gap:1.25rem; }
    fieldset { min-width:0; margin:0; border:0; padding:0; }
    legend { margin-bottom:.9rem; color:var(--ink); font-size:1.1rem; font-weight:800; }
    .legend-note { display:block; margin-top:.15rem; color:var(--muted); font-size:.88rem; font-weight:400; }
    .field-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1rem; }
    .field { min-width:0; }
    label { display:block; margin-bottom:.35rem; font-weight:750; }
    .required { color:var(--amber); }
    .help { margin:.35rem 0 0; color:var(--muted); font-size:.82rem; }
    input,select { width:100%; min-height:3rem; border:1px solid var(--line-strong); border-radius:.35rem; background:#071014; color:var(--ink); padding:.7rem .78rem; color-scheme:dark; }
    input:focus,select:focus,button:focus-visible,a:focus-visible { outline:3px solid rgba(97,213,245,.38); outline-offset:2px; border-color:var(--sky); }
    input[aria-invalid="true"],select[aria-invalid="true"] { border-color:var(--danger); }
    .field-error { margin:.35rem 0 0; color:var(--danger); font-size:.84rem; }
    .field-error[hidden],.error-summary[hidden] { display:none; }
    details { border-top:1px solid var(--line); padding-top:.8rem; }
    summary { width:max-content; cursor:pointer; color:var(--sky); font-weight:750; }
    summary:focus-visible { outline:3px solid rgba(97,213,245,.38); outline-offset:3px; }
    .consent { display:flex; gap:.7rem; align-items:flex-start; border-top:1px solid var(--line); padding-top:1rem; }
    .consent input { width:1.25rem; min-width:1.25rem; height:1.25rem; min-height:1.25rem; margin-top:.15rem; accent-color:var(--sky); }
    .consent label { margin:0; font-weight:600; }
    .button-row { display:flex; align-items:center; flex-wrap:wrap; gap:.8rem; }
    button { min-height:3rem; border:1px solid var(--sky); border-radius:.35rem; background:var(--sky); color:#061116; cursor:pointer; font-weight:850; padding:.7rem 1.1rem; transition:background-color .18s ease,border-color .18s ease,transform .18s ease,opacity .18s ease; }
    button:hover { border-color:#a7ecff; background:#a7ecff; }
    button:active { transform:translateY(1px); }
    button[disabled] { cursor:wait; opacity:.62; }
    .status-line { min-height:1.5rem; margin:0; color:var(--muted); font-size:.88rem; }
    .status-line.success { color:var(--green); }
    .error-summary { border:1px solid #b85d68; border-radius:.35rem; background:var(--danger-bg); color:var(--danger); padding:.8rem .9rem; }
    .error-summary p { margin:0; font-weight:800; }
    .error-summary ul { margin:.4rem 0 0 1.1rem; padding:0; }
    .error-summary a { color:var(--danger); }
    .side-panel { display:grid; align-content:start; gap:1.25rem; }
    .side-panel > p { margin:0; color:var(--muted); }
    .signal-card { border:1px solid var(--line-strong); border-radius:.4rem; background:rgba(7,16,20,.72); padding:1rem; }
    .signal-card strong { display:block; margin:.35rem 0; }
    .signal-card p { margin:0; color:var(--muted); font-size:.9rem; }
    .result-shell { min-height:18rem; display:grid; align-content:start; gap:1rem; }
    .result-shell.empty { place-items:center; text-align:center; border-style:dashed; }
    .result-shell.empty p { margin:0; color:var(--muted); }
    .result-shell.empty svg { width:2.4rem; height:2.4rem; color:var(--sky); }
    .result-header { display:flex; align-items:start; justify-content:space-between; gap:1rem; }
    .result-label { color:var(--green); font:700 .75rem ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.14em; text-transform:uppercase; }
    .decision-word { margin:.25rem 0 0; color:var(--ink); font-size:clamp(2.2rem,5vw,4.5rem); }
    .decision-summary { margin:0; font-size:1.12rem; font-weight:750; }
    .decision-rationale { margin:0; color:var(--muted); }
    .result-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.75rem; }
    .result-facts div { border-top:1px solid var(--line-strong); padding-top:.6rem; }
    .result-facts dt { color:var(--muted); font-size:.8rem; }
    .result-facts dd { margin:.15rem 0 0; font-weight:800; overflow-wrap:anywhere; }
    .evidence { border-top:1px solid var(--line); padding-top:.9rem; }
    .evidence p { margin:.25rem 0 0; color:var(--muted); font-size:.86rem; }
    .evidence-chip { display:inline-flex; margin-top:.65rem; border:1px solid var(--line-strong); border-radius:999px; padding:.3rem .55rem; color:var(--muted); font-size:.8rem; }
    .boundary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
    .boundary-item { border-top:1px solid var(--line-strong); padding-top:.8rem; }
    .boundary-item strong { display:block; margin-bottom:.25rem; }
    .boundary-item p { margin:0; color:var(--muted); font-size:.9rem; }
    footer { border-top:1px solid var(--line); padding-top:1.1rem; color:var(--muted); font-size:.88rem; }
    @media (max-width:900px) { .hero,.workbench { grid-template-columns:1fr; } .hero-aside { max-width:36rem; } }
    @media (max-width:680px) { .shell { width:min(100% - 1.25rem,1180px); } .topbar { align-items:flex-start; padding:.75rem 0; } .field-grid,.result-facts,.boundary { grid-template-columns:1fr; } h1 { max-width:13ch; font-size:clamp(2.8rem,14vw,4.5rem); } .section-heading { display:block; } .section-heading p { margin-top:.35rem; } .panel { padding:1rem; } .button-row button { width:100%; } }
    @media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } *,*::before,*::after { transition:none !important; animation:none !important; } }
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to content</a>
  <div class="shell">
    <header class="topbar">
      <p class="brand"><img class="brand-mark" src="__LOGO_PATH__?v=__LOGO_VERSION__" width="192" height="192" alt="" aria-hidden="true">OathCast <span class="meta">/ Planning Desk</span></p>
      <a class="status-link" href="/status">View system status</a>
    </header>
    <main id="main-content">
      <section class="hero" aria-labelledby="page-heading">
        <div>
          <div class="hero-kicker eyebrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 16.5 8.5 12 12 15.5 19.5 8"/><path d="M15 8h4.5v4.5"/></svg> Weather intelligence for a real decision</div>
          <span class="mode-badge __MODE_CLASS__">__MODE_LABEL__</span>
          <h1 id="page-heading">__MODE_TITLE__</h1>
          <p class="lede">__MODE_COPY__</p>
        </div>
        <aside class="hero-aside" aria-label="How OathCast works">
          <div><span class="eyebrow">A simple three-step brief</span></div>
          <div class="steps">
            <div class="step"><span class="step-number">01</span><div><strong>Describe the plan</strong><p>Tell OathCast what could change if rain is likely.</p></div></div>
            <div class="step"><span class="step-number">02</span><div><strong>Choose one hour</strong><p>Use the UTC hour when the decision matters.</p></div></div>
            <div class="step"><span class="step-number">03</span><div><strong>Read the action</strong><p>Get a threshold-based recommendation with evidence.</p></div></div>
          </div>
        </aside>
      </section>

      <section aria-labelledby="brief-heading">
        <div class="section-heading"><div><span class="eyebrow">Start here</span><h2 id="brief-heading">Build your planning brief</h2></div><p>About 30 seconds</p></div>
        <div class="workbench">
          <div class="panel">
            <p class="signal-card"><strong>__MODE_NOTICE__</strong><span class="help">Only enter information needed for this one planning decision. Do not include passwords, payment details, or sensitive personal information.</span></p>
            <form id="decision-form" novalidate>
              <fieldset>
                <legend>1. What are you deciding?<span class="legend-note">Name the activity in plain language.</span></legend>
                <div class="field">
                  <label for="activity">Activity <span class="required" aria-hidden="true">*</span></label>
                  <input id="activity" name="activity" type="text" maxlength="120" autocomplete="off" value="Saturday market setup" required aria-describedby="activity-help activity-error">
                  <p class="help" id="activity-help">Example: decide whether to move a market setup indoors.</p>
                  <p class="field-error" id="activity-error" hidden></p>
                </div>
              </fieldset>

              <fieldset>
                <legend>2. Where and when?<span class="legend-note">The location helps identify the forecast point. The hour is always UTC.</span></legend>
                <div class="field">
                  <label for="location">Location name <span class="required" aria-hidden="true">*</span></label>
                  <input id="location" name="location" type="text" maxlength="200" autocomplete="address-level2" value="Lagos outdoor market" required aria-describedby="location-help location-error">
                  <p class="help" id="location-help">A recognizable name is enough; no address is required.</p>
                  <p class="field-error" id="location-error" hidden></p>
                </div>
                <div class="field-grid" style="margin-top:1rem">
                  <div class="field"><label for="forecast-date">Date (UTC) <span class="required" aria-hidden="true">*</span></label><input id="forecast-date" type="date" value="__DEFAULT_DATE__" required aria-describedby="forecast-time-help date-error"><p class="field-error" id="date-error" hidden></p></div>
                  <div class="field"><label for="forecast-hour">Hour (UTC) <span class="required" aria-hidden="true">*</span></label><select id="forecast-hour" required aria-describedby="forecast-time-help hour-error">__HOUR_OPTIONS__</select><p class="field-error" id="hour-error" hidden></p></div>
                </div>
                <p class="help" id="forecast-time-help">OathCast checks one exact hour, for example 16:00–17:00 UTC.</p>
                <details>
                  <summary>Change the map point</summary>
                  <div class="field-grid" style="margin-top:1rem">
                    <div class="field"><label for="latitude">Latitude</label><input id="latitude" type="number" min="-90" max="90" step="any" value="6.5244" aria-describedby="latitude-help latitude-error"><p class="help" id="latitude-help">Decimal degrees, north/south.</p><p class="field-error" id="latitude-error" hidden></p></div>
                    <div class="field"><label for="longitude">Longitude</label><input id="longitude" type="number" min="-180" max="180" step="any" value="3.3792" aria-describedby="longitude-help longitude-error"><p class="help" id="longitude-help">Decimal degrees, east/west.</p><p class="field-error" id="longitude-error" hidden></p></div>
                  </div>
                </details>
              </fieldset>

              <fieldset>
                <legend>3. Set your action threshold<span class="legend-note">At or above this risk, OathCast recommends a contingency.</span></legend>
                <div class="field"><label for="risk-threshold">Rain-risk threshold (%) <span class="required" aria-hidden="true">*</span></label><input id="risk-threshold" type="number" min="0" max="100" step="1" value="30" inputmode="numeric" required aria-describedby="threshold-help threshold-error"><p class="help" id="threshold-help">30% means “prepare an alternative if risk reaches 30%.”</p><p class="field-error" id="threshold-error" hidden></p></div>
              </fieldset>

              <div class="consent"><input id="consent" type="checkbox" checked aria-describedby="consent-help consent-error"><div><label for="consent">__CONSENT_LABEL__ <span class="required" aria-hidden="true">*</span></label><p class="help" id="consent-help">This tool provides non-binding planning support. You remain responsible for the decision.</p><p class="field-error" id="consent-error" hidden></p></div></div>
              <div id="form-errors" class="error-summary" role="alert" tabindex="-1" hidden><p>Check the highlighted fields.</p><ul id="form-errors-list"></ul></div>
              <div class="button-row"><button id="run-decision" type="submit">__SUBMIT_LABEL__</button><p id="request-status" class="status-line" role="status" aria-live="polite">Ready for your brief.</p></div>
            </form>
          </div>

          <aside class="side-panel">
            <div class="panel result-shell empty" id="decision-result" aria-live="polite"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 18h16M6 15.5l3-3 2.5 2.5L17.5 9"/><path d="M17.5 9H20v2.5"/></svg><div><h3>Your decision appears here</h3><p>Complete the brief to see the recommended action, risk estimate, and public evidence summary.</p></div></div>
            <div class="panel"><span class="eyebrow">What the result means</span><div class="steps"><div class="step"><span class="step-number">A</span><div><strong>Go</strong><p>Risk is below your threshold for the selected hour.</p></div></div><div class="step"><span class="step-number">B</span><div><strong>Contingency</strong><p>Risk meets or exceeds your threshold; prepare the alternative.</p></div></div></div></div>
          </aside>
        </div>
      </section>

      <section class="panel" aria-labelledby="boundary-heading"><div class="section-heading"><div><span class="eyebrow">Clear boundaries</span><h2 id="boundary-heading">Useful, inspectable, non-binding</h2></div></div><div class="boundary"><div class="boundary-item"><strong>One decision at a time</strong><p>The brief is narrowed to one place, one hour, and one threshold so the answer stays understandable.</p></div><div class="boundary-item"><strong>Evidence stays visible</strong><p>The result identifies how the signal was obtained without exposing raw credentials or wallet material.</p></div><div class="boundary-item"><strong>Weather is uncertain</strong><p>Use the result to plan responsibly; it is not an emergency alert or safety guarantee.</p></div></div></section>
      <footer>__FOOTER__ <a href="/status">Open machine-readable status</a>.</footer>
    </main>
  </div>
  <script>
    (() => {
      const form = document.getElementById("decision-form");
      const submit = document.getElementById("run-decision");
      const status = document.getElementById("request-status");
      const result = document.getElementById("decision-result");
      const errorSummary = document.getElementById("form-errors");
      const errorList = document.getElementById("form-errors-list");
      const dateInput = document.getElementById("forecast-date");
      const hourInput = document.getElementById("forecast-hour");
      const fields = {
        activity: document.getElementById("activity"),
        location: document.getElementById("location"),
        latitude: document.getElementById("latitude"),
        longitude: document.getElementById("longitude"),
        risk_threshold_percent: document.getElementById("risk-threshold"),
        consent: document.getElementById("consent")
      };
      const errors = {
        activity: document.getElementById("activity-error"),
        location: document.getElementById("location-error"),
        latitude: document.getElementById("latitude-error"),
        longitude: document.getElementById("longitude-error"),
        risk_threshold_percent: document.getElementById("threshold-error"),
        local_datetime: document.getElementById("date-error"),
        consent: document.getElementById("consent-error")
      };
      function safe(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[character]));
      }
      function clearErrors() {
        errorSummary.hidden = true;
        errorList.replaceChildren();
        Object.entries(errors).forEach(([name, element]) => { element.hidden = true; element.textContent = ""; });
        Object.values(fields).forEach((element) => element.setAttribute("aria-invalid", "false"));
        dateInput.setAttribute("aria-invalid", "false");
        hourInput.setAttribute("aria-invalid", "false");
      }
      function showErrors(fieldErrors) {
        const visible = [];
        Object.entries(fieldErrors || {}).forEach(([name, message]) => {
          const error = errors[name] || errors.local_datetime;
          const input = name === "local_datetime" ? dateInput : (fields[name] || fields.risk_threshold_percent);
          if (!error || !input) return;
          error.hidden = false;
          error.textContent = String(message);
          input.setAttribute("aria-invalid", "true");
          visible.push({name, message: String(message), input});
        });
        if (visible.length) {
          visible.forEach(({name, message, input}) => {
            const item = document.createElement("li");
            const link = document.createElement("a");
            link.href = `#${input.id}`;
            link.textContent = `${name === "local_datetime" ? "Date and hour" : name}: ${message}`;
            item.append(link);
            errorList.append(item);
          });
          errorSummary.hidden = false;
          errorSummary.focus();
        }
      }
      function payload() {
        return {
          activity: fields.activity.value.trim(),
          location: fields.location.value.trim(),
          latitude: Number(fields.latitude.value),
          longitude: Number(fields.longitude.value),
          local_datetime: `${dateInput.value}T${hourInput.value}:00:00+00:00`,
          risk_threshold_percent: Number(fields.risk_threshold_percent.value),
          consent: fields.consent.checked
        };
      }
      function renderResult(body) {
        const evidence = Array.isArray(body.miner_evidence) ? body.miner_evidence : [];
        const evidenceCopy = evidence.length
          ? evidence.map((item) => `${safe(item.miner_id)} · ${safe(item.status)} · ${item.payment_verified ? "payment verified" : "no payment"}`).join("<br>")
          : "No public evidence record was returned.";
        result.className = "panel result-shell";
        result.innerHTML = `<div class="result-header"><div><span class="result-label">Decision returned</span><h3 class="decision-word">${safe(String(body.action || body.decision || "unknown").toUpperCase())}</h3></div><span class="mode-badge __MODE_CLASS__">__MODE_LABEL__</span></div><p class="decision-summary">${safe(body.summary || "No summary was returned.")}</p><p class="decision-rationale">${safe(body.rationale || "")}</p><dl class="result-facts"><div><dt>Risk estimate</dt><dd>${safe(body.risk_percent == null ? "Not available" : `${body.risk_percent}%`)}</dd></div><div><dt>Your threshold</dt><dd>${safe(`${body.risk_threshold_percent ?? fields.risk_threshold_percent.value}%`)}</dd></div><div><dt>Request ID</dt><dd>${safe(body.request_id || "Not available")}</dd></div></dl><div class="evidence"><strong>Evidence summary</strong><p>${evidenceCopy}</p><span class="evidence-chip">Raw responses and credentials stay private</span></div>`;
        const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        result.scrollIntoView({behavior: prefersReducedMotion ? "auto" : "smooth", block: "start"});
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        clearErrors();
        status.className = "status-line";
        status.textContent = "Checking the brief…";
        submit.disabled = true;
        form.setAttribute("aria-busy", "true");
        try {
          const response = await fetch("/api/decision", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload())});
          let body;
          try { body = await response.json(); } catch { body = {message:"The service returned an unreadable response."}; }
          if (!response.ok) {
            showErrors(body.fields || {});
            throw new Error(body.message || "The decision could not be completed.");
          }
          renderResult(body);
          status.className = "status-line success";
          status.textContent = "Decision ready. Review the evidence summary on the right.";
        } catch (error) {
          if (!errorSummary.hidden) status.textContent = "Update the highlighted fields and try again.";
          else status.textContent = String(error.message || "The service is unavailable.");
        } finally {
          submit.disabled = false;
          form.setAttribute("aria-busy", "false");
        }
      });
    })();
  </script>
</body>
</html>"""
    return (
        page
        .replace("__MODE_LABEL__", escape(mode_label))
        .replace("__MODE_CLASS__", mode_class)
        .replace("__MODE_TITLE__", escape(mode_title))
        .replace("__MODE_COPY__", escape(mode_copy))
        .replace("__MODE_NOTICE__", escape(notice))
        .replace("__CONSENT_LABEL__", escape(consent_label))
        .replace("__SUBMIT_LABEL__", escape(submit_label))
        .replace("__FOOTER__", escape(footer))
        .replace("__DEFAULT_DATE__", planned.date().isoformat())
        .replace("__HOUR_OPTIONS__", hour_options)
        .replace("__LOGO_PATH__", LOGO_PATH)
        .replace("__LOGO_VERSION__", LOGO_VERSION)
    )


def _inline_source_hash(page: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", page, flags=re.DOTALL)
    if match is None:
        raise RuntimeError(f"static page is missing its inline {tag}")
    digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


_STATIC_PAGE = _render_page()
_STYLE_SOURCE_HASH = _inline_source_hash(_STATIC_PAGE, "style")
_SCRIPT_SOURCE_HASH = _inline_source_hash(_STATIC_PAGE, "script")
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src 'self'; "
    f"style-src {_STYLE_SOURCE_HASH}; script-src {_SCRIPT_SOURCE_HASH}; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'"
)


def _content_security_policy(page: str) -> str:
    """Build the exact CSP for either the static or interactive page."""

    connect_source = "'self'" if 'id="decision-form"' in page else "'none'"
    return (
        "default-src 'none'; img-src 'self'; "
        f"style-src {_inline_source_hash(page, 'style')}; "
        f"script-src {_inline_source_hash(page, 'script')}; "
        f"connect-src {connect_source}; base-uri 'none'; form-action 'none'"
    )


def render_page(
    *,
    application: Any | None = None,
    result: DecisionResult | None = None,
    error: str | None = None,
) -> str:
    """Return the configured interactive page or the static fallback."""

    if result is None and error is None:
        if application is not None and getattr(application, "runner_configured", False):
            return _render_interactive_page(mode=getattr(application, "public_mode", "demo"))
        return _STATIC_PAGE
    return _render_page(result=result, error=error)


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
            _content_security_policy(body),
        )
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_logo(self) -> None:
        body = _LOGO_BYTES
        if body is None:
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
            self._send_html(200, render_page(application=self.application))
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
    "DemoDecisionRunner",
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
