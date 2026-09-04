"""Application-side cross-Miner routing and decision policy.

This layer is intentionally separate from the Miner service. The Miner serves
forecasts; the Application discovers competitors, calls them, retains raw
responses, and makes a live decision without access to future ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from oathcast.application_payment import (
    ApplicationPaymentError,
    ApplicationPaymentBoundary,
    canonical_request_fingerprint,
)
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion, format_timestamp
from oathcast.miner_adapters import AdaptedMinerResponse, adapter_for_miner
from oathcast.probability import (
    PERCENT_PROBABILITY_KEYS,
    PROBABILITY_KEYS,
    PROBABILITY_PATTERN,
    extract_probability,
)
from oathcast.protocol import ProtocolResultEnvelope, outbound_headers


UTC = timezone.utc
DEFAULT_MAX_RESPONSE_BODY_BYTES = 2 * 1024 * 1024
MINER_REQUEST_FAILED_REASON = "miner request failed"
LOGGER = logging.getLogger("oathcast.application")


class MinerClient(Protocol):
    def __call__(self, question: ForecastQuestion) -> Any:
        ...


class RoutingError(RuntimeError):
    """Raised when the Application cannot satisfy its cross-Miner policy."""


class ApplicationProjectionError(RuntimeError):
    """Raised when durable evidence cannot be projected after a reply."""


def _read_bounded_response(
    response: Any,
    *,
    max_body_bytes: int,
    source: str,
) -> bytes:
    """Read one HTTP body without allowing an upstream to exhaust memory."""

    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    headers = getattr(response, "headers", None)
    declared = headers.get("Content-Length") if headers is not None else None
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError):
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > max_body_bytes:
            raise ValueError(f"{source} response exceeds {max_body_bytes} byte cap")
    body = response.read(max_body_bytes + 1)
    if len(body) > max_body_bytes:
        raise ValueError(f"{source} response exceeds {max_body_bytes} byte cap")
    return body


def _log_router_failure(
    capability: MinerCapability,
    *,
    request_id: str,
    error: BaseException,
) -> None:
    """Keep correlation and exception-type diagnostics out of evidence text."""

    cause = error.__cause__ or error
    LOGGER.error(
        json.dumps(
            {
                "event": "miner_request_failed",
                "request_id": request_id,
                "miner_id": capability.miner_id,
                "slug": capability.slug,
                "error_type": type(error).__name__,
                "cause_type": type(cause).__name__,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def extract_content(raw_response: Any) -> str:
    if isinstance(raw_response, ProtocolResultEnvelope):
        raw_response = raw_response.body
    if isinstance(raw_response, str):
        return raw_response.strip()
    if isinstance(raw_response, dict):
        content = raw_response.get("content")
        if isinstance(content, str):
            return content.strip()
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
        return json.dumps(raw_response, sort_keys=True, separators=(",", ":"))
    return str(raw_response).strip()


@dataclass(frozen=True)
class MinerReply:
    miner_id: str
    slug: str
    owned: bool
    raw_response: Any | None
    probability: float | None
    content: str
    latency_ms: float | None
    transport: str
    probability_comparable: bool = True
    error: str | None = None
    received_at: datetime | None = None
    parser_version: str = "probability_extractor_v1"
    validity_reason: str | None = None
    request_id: str | None = None
    protocol_result: ProtocolResultEnvelope | None = None

    @property
    def valid(self) -> bool:
        return (
            self.error is None
            and self.probability is not None
            and self.probability_comparable
        )

    @property
    def has_comparable_probability(self) -> bool:
        """Whether this reply is eligible to influence probability consensus."""

        return self.valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "miner_id": self.miner_id,
            "slug": self.slug,
            "owned": self.owned,
            "raw_response": self.raw_response,
            "probability": self.probability,
            "probability_comparable": self.probability_comparable,
            "content": self.content,
            "latency_ms": self.latency_ms,
            "transport": self.transport,
            "error": self.error,
            "valid": self.valid,
            "received_at": (
                None if self.received_at is None else format_timestamp(self.received_at)
            ),
            "parser_version": self.parser_version,
            "validity_reason": self.validity_reason,
            "request_id": self.request_id,
            "protocol_result": (
                None if self.protocol_result is None else self.protocol_result.to_dict()
            ),
        }


@dataclass(frozen=True)
class ApplicationDecision:
    question: ForecastQuestion
    aggregate_probability: float
    event_likely: bool
    recommended_action: str
    used_external_miner: bool
    external_influence: bool
    replies: tuple[MinerReply, ...]
    decided_at: datetime
    application_request_id: str = ""
    decision_threshold: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.question.event_id,
            "aggregate_probability": self.aggregate_probability,
            "event_likely": self.event_likely,
            "recommended_action": self.recommended_action,
            "used_external_miner": self.used_external_miner,
            "external_influence": self.external_influence,
            "replies": [reply.to_dict() for reply in self.replies],
            "decided_at": format_timestamp(self.decided_at),
            "application_request_id": self.application_request_id,
            "decision_threshold": self.decision_threshold,
        }


class HttpMinerClient:
    """Small development client for a Miner HTTP endpoint.

    Payment headers are deliberately not fabricated here. A caller can inject
    headers through the constructor for local integration tests; live paid
    Telegraph traffic belongs behind the reviewed Solana x402 boundary.
    """

    def __init__(
        self,
        capability: MinerCapability,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 12.0,
        max_response_body_bytes: int = DEFAULT_MAX_RESPONSE_BODY_BYTES,
    ) -> None:
        self.capability = capability
        self.headers = headers or {}
        self.timeout_seconds = timeout_seconds
        if max_response_body_bytes <= 0:
            raise ValueError("max_response_body_bytes must be positive")
        self.max_response_body_bytes = max_response_body_bytes

    def __call__(self, question: ForecastQuestion) -> Any:
        return self.request_with_id(question, request_id=None)

    def request_with_id(self, question: ForecastQuestion, request_id: str | None) -> Any:
        params = {
            "event_id": question.event_id,
            "location_name": question.location_name,
            "lat": f"{question.latitude:.6f}",
            "lon": f"{question.longitude:.6f}",
            "horizon_start": format_timestamp(question.horizon_start),
            "horizon_end": format_timestamp(question.horizon_end),
            "forecast_cutoff": format_timestamp(question.forecast_cutoff),
            "threshold_mm": f"{question.threshold_mm:g}",
        }
        url = f"{self.capability.base_url.rstrip('/')}{self.capability.endpoint_path}?{urlencode(params)}"
        headers = outbound_headers(self.headers)
        if request_id:
            headers["X-Request-ID"] = request_id
            headers["X-OathCast-Application-Request-ID"] = request_id
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = _read_bounded_response(
                response,
                max_body_bytes=self.max_response_body_bytes,
                source="Miner",
            )
            payload = json.loads(body.decode("utf-8"))
        return payload


class TelegraphMinerClient:
    """Paid dispatcher client used by the Application after discovery."""

    def __init__(
        self,
        capability: MinerCapability,
        payment_client: Any,
        *,
        endpoint: str | None = None,
        demand_ledger: Any | None = None,
    ) -> None:
        self.capability = capability
        self.payment_client = payment_client
        self.adapter = adapter_for_miner(capability.miner_id)
        self.endpoint = endpoint or self.adapter.endpoint_name or capability.endpoint_name
        self.demand_ledger = demand_ledger

    def __call__(self, question: ForecastQuestion) -> Any:
        return self.request_with_id(question, request_id=None)

    def request_with_id(self, question: ForecastQuestion, request_id: str | None) -> Any:
        params = self.adapter.build_params(question)
        request_headers = None
        if request_id:
            request_headers = {
                "X-Request-ID": request_id,
                "X-OathCast-Application-Request-ID": request_id,
            }
        request_kwargs = (
            {} if request_headers is None else {"request_headers": request_headers}
        )
        response = self.payment_client.request_miner(
            self.capability.miner_id,
            self.endpoint,
            params,
            **request_kwargs,
        )
        protocol_result = ProtocolResultEnvelope.from_payment_response(
            response,
            route_mode="telegraph",
            registry_snapshot_sha256=self.capability.registry_snapshot_sha256,
        )
        if self.demand_ledger is not None:
            settlement_verified = protocol_result.receipt.settlement_verified
            self.demand_ledger.record(
                question_event_id=question.event_id,
                application_request_id=request_id,
                miner_id=self.capability.miner_id,
                endpoint=self.endpoint,
                transport="telegraph",
                routed_through_telegraph=True,
                payment_method="x402",
                payment_status="settled" if settlement_verified else "paid_unverified",
                payment_evidence=(
                    "x402_settlement" if settlement_verified else "x402_header_unverified"
                ),
                http_status=getattr(response, "status", None),
                is_fixture=False,
                source="application",
                payment_attempt_id=protocol_result.receipt.payment_attempt_id,
                settlement_artifact_sha256=protocol_result.receipt.settlement_artifact_sha256,
                settlement_verification=protocol_result.receipt.settlement_verification,
                protocol_receipt_sha256=protocol_result.receipt.receipt_sha256,
            )
        return protocol_result


class ApplicationTelegraphMinerClient:
    """Live Application client whose spend authority is the private sidecar."""

    def __init__(
        self,
        capability: MinerCapability,
        payment_boundary: ApplicationPaymentBoundary,
        *,
        principal_id: str,
        endpoint: str | None = None,
        demand_ledger: Any | None = None,
        consent: bool = True,
    ) -> None:
        self.capability = capability
        self.payment_boundary = payment_boundary
        self.adapter = adapter_for_miner(capability.miner_id)
        self.endpoint = endpoint or self.adapter.endpoint_name or capability.endpoint_name
        self.principal_id = principal_id
        self.demand_ledger = demand_ledger
        self.consent = consent

    def __call__(self, question: ForecastQuestion) -> Any:
        return self.request_with_id(question, request_id=None)

    def request_with_id(self, question: ForecastQuestion, request_id: str | None) -> Any:
        if not request_id:
            raise RoutingError("an Application request id is required for a paid Miner call")
        validator = getattr(self.adapter, "validate_question", None)
        if callable(validator):
            try:
                validator(question)
            except ValueError as error:
                raise RoutingError("the external Miner cannot serve this forecast horizon") from error
        params = self.adapter.build_params(question)
        payment_idempotency_key = self._payment_idempotency_key(request_id)
        request_fingerprint = canonical_request_fingerprint(
            principal_id=self.principal_id,
            idempotency_key=payment_idempotency_key,
            miner_id=self.capability.miner_id,
            endpoint=self.endpoint,
            params=params,
        )
        response = self.payment_boundary.request_miner(
            principal_id=self.principal_id,
            idempotency_key=payment_idempotency_key,
            request_fingerprint=request_fingerprint,
            miner_id=self.capability.miner_id,
            endpoint=self.endpoint,
            params=params,
            consent=self.consent,
        )
        protocol_result = ProtocolResultEnvelope.from_payment_response(
            response,
            route_mode="telegraph",
            registry_snapshot_sha256=self.capability.registry_snapshot_sha256,
        )
        if self.demand_ledger is not None:
            try:
                self.demand_ledger.record(
                    question_event_id=question.event_id,
                    application_request_id=request_id,
                    miner_id=self.capability.miner_id,
                    endpoint=self.endpoint,
                    transport="telegraph",
                    routed_through_telegraph=True,
                    payment_method="x402",
                    payment_status="settled",
                    payment_evidence="x402_settlement",
                    http_status=response.status,
                    is_fixture=False,
                    source="application",
                    payment_attempt_id=protocol_result.receipt.payment_attempt_id,
                    settlement_artifact_sha256=protocol_result.receipt.settlement_artifact_sha256,
                    settlement_verification=protocol_result.receipt.settlement_verification,
                    protocol_receipt_sha256=protocol_result.receipt.receipt_sha256,
                )
            except Exception as error:
                LOGGER.error(
                    json.dumps(
                        {
                            "event": "demand_projection_failed",
                            "request_id": request_id,
                            "miner_id": self.capability.miner_id,
                            "error_type": type(error).__name__,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                raise ApplicationProjectionError(
                    "the received Miner reply could not be durably projected"
                ) from error
        return protocol_result

    def _payment_idempotency_key(self, application_request_id: str) -> str:
        digest = hashlib.sha256(
            f"{application_request_id}|{self.capability.miner_id}|{self.endpoint}".encode()
        ).hexdigest()[:60]
        return f"pay-{digest}"


class CrossMinerRouter:
    """Route to owned and external Miners and aggregate only current answers."""

    def __init__(
        self,
        capabilities: Iterable[MinerCapability],
        clients: dict[str, MinerClient],
        *,
        own_slugs: set[str],
        require_external: bool = True,
        clock: Callable[[], datetime] | None = None,
        reply_projector: Callable[[MinerReply], None] | None = None,
    ) -> None:
        self.capabilities = list(capabilities)
        self.clients = clients
        self.own_slugs = own_slugs
        self.require_external = require_external
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.reply_projector = reply_projector

    def _project_reply(self, reply: MinerReply) -> None:
        if self.reply_projector is None:
            return
        try:
            self.reply_projector(reply)
        except Exception as error:
            LOGGER.error(
                json.dumps(
                    {
                        "event": "miner_reply_projection_failed",
                        "request_id": reply.request_id,
                        "miner_id": reply.miner_id,
                        "error_type": type(error).__name__,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            raise ApplicationProjectionError(
                "the Miner reply could not be durably projected"
            ) from error

    def _reply(
        self,
        capability: MinerCapability,
        question: ForecastQuestion,
        request_id: str,
    ) -> MinerReply:
        started = time.perf_counter()
        try:
            raw = self._call_client(self.clients[capability.slug], question, request_id)
            adapter = adapter_for_miner(capability.miner_id)
            adapted: AdaptedMinerResponse = adapter.parse_response(raw, question)
            probability = adapted.probability
            comparable = adapted.has_comparable_probability
            error = None if comparable else (
                adapted.validity_reason or "response has no comparable probability"
            )
            protocol_result = raw if isinstance(raw, ProtocolResultEnvelope) else None
            raw_body = protocol_result.body if protocol_result is not None else raw
            reply = MinerReply(
                miner_id=capability.miner_id,
                slug=capability.slug,
                owned=capability.slug in self.own_slugs,
                raw_response=raw_body,
                probability=probability,
                content=extract_content(raw),
                latency_ms=(time.perf_counter() - started) * 1000,
                transport=("telegraph" if protocol_result is not None else "development_http_or_injected"),
                probability_comparable=comparable,
                error=error,
                received_at=self.clock().astimezone(UTC),
                parser_version=adapted.parser_version,
                validity_reason=adapted.validity_reason or error,
                request_id=request_id,
                protocol_result=protocol_result,
            )
            self._project_reply(reply)
            return reply
        except ApplicationPaymentError:
            # Payment state is safety-critical. Preserve an ambiguous outcome,
            # authorization failure, consent failure, or policy conflict for
            # the Application gateway instead of converting it into an ordinary
            # unavailable-Miner reply that callers might retry.
            raise
        except ApplicationProjectionError:
            raise
        except Exception as exc:
            _log_router_failure(capability, request_id=request_id, error=exc)
            failure_reason = f"{MINER_REQUEST_FAILED_REASON} ({type(exc).__name__})"
            reply = MinerReply(
                miner_id=capability.miner_id,
                slug=capability.slug,
                owned=capability.slug in self.own_slugs,
                raw_response=None,
                probability=None,
                content="",
                latency_ms=(time.perf_counter() - started) * 1000,
                transport="development_http_or_injected",
                probability_comparable=False,
                error=failure_reason,
                received_at=self.clock().astimezone(UTC),
                validity_reason=failure_reason,
                request_id=request_id,
            )
            self._project_reply(reply)
            return reply

    @staticmethod
    def _call_client(client: MinerClient, question: ForecastQuestion, request_id: str) -> Any:
        request_with_id = getattr(client, "request_with_id", None)
        if callable(request_with_id):
            return request_with_id(question, request_id)
        return client(question)

    def decide(
        self,
        question: ForecastQuestion,
        *,
        disable_owned: bool = False,
        application_request_id: str | None = None,
        decision_threshold: float = 0.5,
    ) -> ApplicationDecision:
        if not isinstance(decision_threshold, (int, float)) or not 0 <= float(decision_threshold) <= 1:
            raise ValueError("decision_threshold must be between 0 and 1")
        decision_threshold = float(decision_threshold)
        application_request_id = application_request_id or f"app-{uuid.uuid4().hex}"
        replies: list[MinerReply] = []
        for capability in self.capabilities:
            if capability.slug not in self.clients:
                continue
            if disable_owned and capability.slug in self.own_slugs:
                continue
            replies.append(self._reply(capability, question, application_request_id))

        valid = [reply for reply in replies if reply.valid]
        external_valid = [reply for reply in valid if not reply.owned]
        if self.require_external and not external_valid:
            raise RoutingError("no valid independently operated external Miner response")
        if not valid:
            raise RoutingError("no valid Miner response")

        capability_by_slug = {cap.slug: cap for cap in self.capabilities}
        weighted_total = 0.0
        weight_total = 0.0
        for reply in valid:
            capability = capability_by_slug[reply.slug]
            weight = max(0.05, capability.historical_reliability)
            weighted_total += reply.probability * weight  # type: ignore[operator]
            weight_total += weight
        aggregate = round(weighted_total / weight_total, 4)

        owned_valid = [reply for reply in valid if reply.owned]
        owned_probability = owned_valid[0].probability if owned_valid else None
        external_influence = (
            owned_probability is None or abs(aggregate - owned_probability) > 0.0001
        )
        return ApplicationDecision(
            question=question,
            aggregate_probability=aggregate,
            event_likely=aggregate >= decision_threshold,
            recommended_action=(
                "plan_for_event" if aggregate >= decision_threshold else "plan_for_no_event"
            ),
            used_external_miner=bool(external_valid),
            external_influence=external_influence,
            replies=tuple(replies),
            decided_at=self.clock().astimezone(UTC),
            application_request_id=application_request_id,
            decision_threshold=decision_threshold,
        )
