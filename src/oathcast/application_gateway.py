"""Loopback-only Track 3 Application gateway.

The gateway accepts an authenticated, consented forecast request, turns it
into OathCast's strict one-hour question, and routes it through an external
Telegraph Miner using the private payment boundary. It is deliberately a
different server from the public decision UI; it is never intended to be
published through Caddy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from oathcast.application import (
    ApplicationProjectionError,
    ApplicationTelegraphMinerClient,
    ApplicationDecision,
    CrossMinerRouter,
    RoutingError,
)
from oathcast.application_payment import (
    ApplicationPaymentBoundary,
    PaymentAuthorizationError,
    PaymentBoundaryUnavailable,
    PaymentConsentRequired,
    PaymentOutcomeUnknown,
    PaymentPolicyConflict,
    PaymentPreflightRejected,
)
from oathcast.cases import CaseConflict, CaseStateError, SqliteCaseStore
from oathcast.decision_ui import (
    DecisionInput,
    DecisionResult,
    MinerEvidence,
    ValidationError,
    decode_json_body,
    parse_decision_input,
)
from oathcast.demand import DemandLedger
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion, UTC, format_timestamp
from oathcast.ground_truth import ObservationSource
from oathcast.workflow import ApplicationWorkflow


APPLICATION_PATH = "/v1/application/forecast"
HEALTH_PATH = "/healthz"
MAX_GATEWAY_BODY_BYTES = 16 * 1024
MAX_PRINCIPAL_LENGTH = 128
MAX_IDEMPOTENCY_LENGTH = 128
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class ApplicationGatewayError(RuntimeError):
    """A sanitized gateway error suitable for an internal HTTP response."""


class ApplicationNotReady(ApplicationGatewayError):
    pass


class NullObservationSource:
    """Resolution is intentionally a separate operation from the live pilot."""

    def observe(self, question: ForecastQuestion) -> None:
        del question
        return None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identity(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ApplicationGatewayError(f"{name} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ApplicationGatewayError(f"{name} is invalid")
    if SAFE_ID.fullmatch(value) is None:
        raise ApplicationGatewayError(f"{name} is invalid")
    return value


def application_request_id(principal_id: str, idempotency_key: str) -> str:
    """Derive a stable, non-secret correlation id from the authenticated pair."""

    return "app-" + _sha256(
        _canonical_json(
            {"version": 1, "principal_id": principal_id, "idempotency_key": idempotency_key}
        )
    )[:60]


def application_request_fingerprint(
    request: DecisionInput,
    question: ForecastQuestion,
) -> str:
    """Bind the authenticated idempotency key to the normalized full input."""

    return _sha256(
        _canonical_json(
            {
                "version": 1,
                "activity": request.activity,
                "location": request.location,
                "latitude": request.latitude,
                "longitude": request.longitude,
                "local_datetime": format_timestamp(question.horizon_start),
                "risk_threshold_percent": request.risk_threshold_percent,
                "consent": request.consent,
            }
        )
    )


def build_question_from_decision_input(
    request: DecisionInput,
    *,
    principal_id: str = "",
    idempotency_key: str = "",
) -> ForecastQuestion:
    """Build the exact UTC one-hour contract used by the live Miner adapters."""

    start = request.local_datetime.astimezone(UTC)
    if start.minute or start.second or start.microsecond:
        raise ValidationError(
            fields={
                "local_datetime": (
                    "must be aligned to a whole UTC hour for the live forecast contract"
                )
            }
        )
    end = start + timedelta(hours=1)
    cutoff = start - timedelta(hours=1)
    identity = {
        "activity": request.activity,
        "location": request.location,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "local_datetime": format_timestamp(start),
        "risk_threshold_percent": request.risk_threshold_percent,
        "principal_id": principal_id,
        "idempotency_key": idempotency_key,
    }
    event_id = "application-" + _sha256(_canonical_json(identity))[:48]
    return ForecastQuestion(
        event_id=event_id,
        location_name=request.location,
        latitude=request.latitude,
        longitude=request.longitude,
        horizon_start=start,
        horizon_end=end,
        forecast_cutoff=cutoff,
    )


def _evidence_for_reply(reply: Any) -> MinerEvidence:
    protocol_result = getattr(reply, "protocol_result", None)
    receipt = getattr(protocol_result, "receipt", None)
    verified = bool(receipt is not None and receipt.settlement_verified)
    routed = bool(protocol_result is not None and receipt is not None)
    evidence_id = None
    if receipt is not None:
        evidence_id = f"receipt-{receipt.receipt_sha256}"
    status = "valid" if reply.valid and (not routed or verified) else "unknown"
    if reply.error and protocol_result is None:
        status = "unavailable"
    return MinerEvidence(
        miner_id=reply.miner_id,
        status=status,
        probability_percent=(
            None if reply.probability is None else round(reply.probability * 100, 4)
        ),
        evidence_id=evidence_id,
        routed_via_telegraph=routed,
        payment_verified=verified,
    )


def decision_result_for(
    decision: ApplicationDecision,
    request: DecisionInput,
) -> DecisionResult:
    risk_percent = round(decision.aggregate_probability * 100, 4)
    threshold_percent = decision.decision_threshold * 100
    contingency = risk_percent >= threshold_percent
    action = "contingency" if contingency else "go"
    summary = (
        f"The Telegraph-routed forecast estimates {risk_percent:g}% risk of measurable "
        f"precipitation during the selected hour."
    )
    rationale = (
        f"The estimate is {'at or above' if contingency else 'below'} your "
        f"{threshold_percent:g}% threshold."
    )
    return DecisionResult(
        action=action,
        summary=summary,
        rationale=rationale,
        miner_evidence=tuple(_evidence_for_reply(reply) for reply in decision.replies),
        risk_percent=risk_percent,
        request_id=decision.application_request_id,
    )


def decision_from_stored(
    payload: Mapping[str, Any],
    request: DecisionInput,
) -> DecisionResult:
    """Rebuild only the public result from a sealed case, without another call."""

    raw_replies = payload.get("replies", [])
    evidence: list[MinerEvidence] = []
    for raw in raw_replies if isinstance(raw_replies, list) else []:
        if not isinstance(raw, Mapping):
            continue
        protocol = raw.get("protocol_result")
        receipt = protocol.get("receipt") if isinstance(protocol, Mapping) else None
        verified = bool(
            isinstance(receipt, Mapping)
            and receipt.get("settlement_verification") == "verified"
        )
        routed = isinstance(protocol, Mapping)
        receipt_hash = receipt.get("receipt_sha256") if isinstance(receipt, Mapping) else None
        probability = raw.get("probability")
        evidence.append(
            MinerEvidence(
                miner_id=str(raw.get("miner_id", "unknown")),
                status="valid" if raw.get("valid") and (not routed or verified) else "unknown",
                probability_percent=(
                    None
                    if probability is None
                    else round(float(probability) * 100, 4)
                ),
                evidence_id=(None if not isinstance(receipt_hash, str) else f"receipt-{receipt_hash}"),
                routed_via_telegraph=routed,
                payment_verified=verified,
            )
        )
    aggregate = float(payload.get("aggregate_probability", 0))
    risk_percent = round(aggregate * 100, 4)
    threshold_percent = float(
        payload.get("decision_threshold", request.risk_threshold_percent / 100)
    ) * 100
    if not 0 <= threshold_percent <= 100:
        threshold_percent = request.risk_threshold_percent
    contingency = risk_percent >= threshold_percent
    return DecisionResult(
        action="contingency" if contingency else "go",
        summary=(
            f"The Telegraph-routed forecast estimates {risk_percent:g}% risk of measurable "
            "precipitation during the selected hour."
        ),
        rationale=(
            f"The estimate is {'at or above' if contingency else 'below'} your "
            f"{threshold_percent:g}% threshold."
        ),
        miner_evidence=tuple(evidence),
        risk_percent=risk_percent,
        request_id=str(payload.get("application_request_id", "")) or None,
    )


@dataclass
class LiveApplicationService:
    capabilities: tuple[MinerCapability, ...]
    payment_boundary: ApplicationPaymentBoundary
    case_store: SqliteCaseStore
    demand_ledger: DemandLedger
    own_slugs: frozenset[str] = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.capabilities and self.payment_boundary.configured)

    def decide(
        self,
        request: DecisionInput,
        *,
        principal_id: str,
        idempotency_key: str,
    ) -> DecisionResult:
        if not self.configured:
            raise ApplicationNotReady("the private Application boundary is not configured")
        question = build_question_from_decision_input(
            request,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
        )
        self.case_store.bind_application_request(
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_sha256=application_request_fingerprint(request, question),
            event_id=question.event_id,
        )
        existing = self.case_store.get(question.event_id)
        if existing is not None and isinstance(existing.get("decision"), Mapping):
            return decision_from_stored(existing["decision"], request)
        if question.forecast_cutoff <= datetime.now(tz=UTC):
            raise ValidationError(
                fields={
                    "local_datetime": (
                        "must leave time for the forecast cutoff before the selected hour"
                    )
                }
            )

        app_id = application_request_id(principal_id, idempotency_key)
        clients = {
            capability.slug: ApplicationTelegraphMinerClient(
                capability,
                self.payment_boundary,
                principal_id=principal_id,
                endpoint=capability.endpoint_name,
                demand_ledger=self.demand_ledger,
                consent=request.consent,
            )
            for capability in self.capabilities
            if capability.slug not in self.own_slugs
        }
        router = CrossMinerRouter(
            self.capabilities,
            clients,
            own_slugs=set(self.own_slugs),
            require_external=True,
            reply_projector=lambda reply: self.case_store.record_reply(
                question.event_id,
                reply.to_dict(),
            ),
        )
        workflow = ApplicationWorkflow(
            router,
            self.case_store,
            NullObservationSource(),
        )
        try:
            decision = workflow.decide(
                question,
                disable_owned=True,
                application_request_id=app_id,
                decision_threshold=request.risk_threshold_percent / 100,
            )
        except CaseStateError:
            stored = self.case_store.get(question.event_id)
            if stored is not None and isinstance(stored.get("decision"), Mapping):
                return decision_from_stored(stored["decision"], request)
            raise
        return decision_result_for(decision, request)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_gateway_json(body: bytes) -> Any:
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("Request body must be valid UTF-8 JSON.") from exc


class ApplicationGatewayHandler(BaseHTTPRequestHandler):
    server_version = "OathCastApplicationGateway/1"
    sys_version = ""

    @property
    def service(self) -> LiveApplicationService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def app_token(self) -> str:
        return self.server.app_token  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"ok": False, "error": code, "message": message})

    def _authorized(self) -> tuple[str, str] | None:
        authorization = self.headers.get_all("Authorization", [])
        principal = self.headers.get_all("X-OathCast-Principal", [])
        idempotency = self.headers.get_all("Idempotency-Key", [])
        expected = f"Bearer {self.app_token}"
        if len(authorization) != 1 or authorization[0] != expected:
            self._error(401, "unauthorized", "application authorization failed")
            return None
        if len(principal) != 1 or len(idempotency) != 1:
            self._error(400, "invalid_request", "principal and idempotency headers are required")
            return None
        try:
            principal_id = _safe_identity(principal[0], "principal", MAX_PRINCIPAL_LENGTH)
            idempotency_key = _safe_identity(
                idempotency[0], "idempotency key", MAX_IDEMPOTENCY_LENGTH
            )
        except ApplicationGatewayError:
            self._error(400, "invalid_request", "principal or idempotency header is invalid")
            return None
        return principal_id, idempotency_key

    def do_GET(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == HEALTH_PATH:
            self._send_json(
                200,
                {
                    "service": "oathcast-application-gateway",
                    "ready": self.service.configured,
                    "public_ui_enabled": False,
                    "payment_boundary": "private_unix_socket",
                },
            )
            return
        self._error(404, "not_found", "not found")

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != APPLICATION_PATH:
            self._error(404, "not_found", "not found")
            return
        identity = self._authorized()
        if identity is None:
            return
        if not self.service.configured:
            self._error(503, "not_ready", "the Application payment boundary is not ready")
            return
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].lower().split(";", 1)[0].strip() != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json")
            return
        transfer = self.headers.get_all("Transfer-Encoding", [])
        if transfer and (len(transfer) != 1 or transfer[0].lower().strip() != "identity"):
            self.close_connection = True
            self._error(400, "invalid_request", "chunked request bodies are not supported")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            self._error(411, "length_required", "Content-Length is required")
            return
        try:
            length = int(lengths[0], 10)
        except (TypeError, ValueError):
            self.close_connection = True
            self._error(400, "invalid_request", "Content-Length is invalid")
            return
        if length < 0:
            self.close_connection = True
            self._error(400, "invalid_request", "Content-Length is invalid")
            return
        if length > MAX_GATEWAY_BODY_BYTES:
            self.close_connection = True
            self._error(413, "body_too_large", "request body is too large")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self._error(400, "invalid_request", "request body was truncated")
            return
        try:
            request = parse_decision_input(_decode_gateway_json(body))
        except ValidationError as exc:
            self._send_json(422, exc.to_public_dict())
            return
        principal_id, idempotency_key = identity
        try:
            result = self.service.decide(
                request,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
            )
        except ValidationError as exc:
            self._send_json(422, exc.to_public_dict())
            return
        except CaseConflict:
            self._error(409, "idempotency_conflict", "the idempotency key is bound to a different request")
            return
        except PaymentConsentRequired:
            self._error(422, "consent_required", "explicit consent is required")
            return
        except PaymentAuthorizationError:
            self._error(503, "payment_unavailable", "the private payment boundary is unavailable")
            return
        except PaymentOutcomeUnknown:
            self._error(409, "payment_outcome_unknown", "the payment outcome requires reconciliation")
            return
        except PaymentPolicyConflict:
            self._error(409, "payment_policy_conflict", "the request conflicts with stored payment evidence")
            return
        except (PaymentBoundaryUnavailable, PaymentPreflightRejected, RoutingError):
            self._error(503, "miner_unavailable", "no usable external Miner response was available")
            return
        except ApplicationProjectionError:
            self._error(503, "application_unavailable", "the received Miner evidence could not be stored")
            return
        except ApplicationNotReady:
            self._error(503, "not_ready", "the Application payment boundary is not ready")
            return
        except Exception:
            self._error(503, "application_unavailable", "the Application service is unavailable")
            return
        self._send_json(
            200,
            {
                "ok": True,
                "decision": result.action,
                "action": result.action,
                "summary": result.summary,
                "rationale": result.rationale,
                "risk_percent": result.risk_percent,
                "risk_threshold_percent": request.risk_threshold_percent,
                "miner_evidence": [item.to_public_dict() for item in result.miner_evidence],
                "request_id": result.request_id,
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
        return


class ApplicationGatewayServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        service: LiveApplicationService,
        app_token: str,
    ) -> None:
        if server_address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("the Application gateway must bind to loopback")
        self.service = service
        self.app_token = app_token
        super().__init__(server_address, ApplicationGatewayHandler)


def make_application_gateway(
    service: LiveApplicationService,
    *,
    app_token: str,
    host: str = "127.0.0.1",
    port: int = 8790,
) -> ApplicationGatewayServer:
    if (
        not isinstance(app_token, str)
        or not 32 <= len(app_token.encode("utf-8")) <= 512
        or any(ord(character) < 32 or ord(character) == 127 for character in app_token)
    ):
        raise ValueError("app_token must be 32-512 bytes without control characters")
    return ApplicationGatewayServer((host, port), service=service, app_token=app_token)


__all__ = [
    "APPLICATION_PATH",
    "ApplicationGatewayServer",
    "ApplicationNotReady",
    "HEALTH_PATH",
    "LiveApplicationService",
    "build_question_from_decision_input",
    "decision_result_for",
    "make_application_gateway",
]
