"""Application-side cross-Miner routing and decision policy.

This layer is intentionally separate from the Miner service. The Miner serves
forecasts; the Application discovers competitors, calls them, retains raw
responses, and makes a live decision without access to future ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import time
import uuid
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion, format_timestamp
from oathcast.protocol import ProtocolResultEnvelope


UTC = timezone.utc
PROBABILITY_PATTERN = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
PROBABILITY_KEYS = (
    "probability",
    "precipitation_probability",
    "probability_of_precipitation",
    "rain_probability",
    "probability_of_rain",
    "pop",
)
PERCENT_PROBABILITY_KEYS = ("chance_of_rain", "rain_chance", "precipitation_chance")


class MinerClient(Protocol):
    def __call__(self, question: ForecastQuestion) -> Any:
        ...


class RoutingError(RuntimeError):
    """Raised when the Application cannot satisfy its cross-Miner policy."""


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


def extract_probability(raw_response: Any) -> float | None:
    if isinstance(raw_response, ProtocolResultEnvelope):
        raw_response = raw_response.body
    if isinstance(raw_response, dict):
        for key in PROBABILITY_KEYS:
            probability = raw_response.get(key)
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probability = float(probability)
                if 0 <= probability <= 1:
                    return probability
                if 1 < probability <= 100 and key != "probability":
                    return probability / 100
        for key in PERCENT_PROBABILITY_KEYS:
            probability = raw_response.get(key)
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probability = float(probability)
                if 0 <= probability <= 100:
                    return probability / 100
        for key in ("data", "result", "forecast", "prediction", "output"):
            nested = raw_response.get(key)
            probability = extract_probability(nested)
            if probability is not None:
                return probability
        choices = raw_response.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                probability = extract_probability(choice)
                if probability is not None:
                    return probability
    content = extract_content(raw_response)
    match = PROBABILITY_PATTERN.search(content)
    if match is None:
        return None
    percentage = float(match.group(1))
    return percentage / 100 if 0 <= percentage <= 100 else None


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
    error: str | None = None
    received_at: datetime | None = None
    parser_version: str = "probability_extractor_v1"
    validity_reason: str | None = None
    request_id: str | None = None
    protocol_result: ProtocolResultEnvelope | None = None

    @property
    def valid(self) -> bool:
        return self.error is None and self.probability is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "miner_id": self.miner_id,
            "slug": self.slug,
            "owned": self.owned,
            "raw_response": self.raw_response,
            "probability": self.probability,
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
        }


class HttpMinerClient:
    """Small development client for a Miner HTTP endpoint.

    Payment headers are deliberately not fabricated here. The Base Sepolia
    USDC/x402 transport will be attached once the official Application flow is
    available; a caller can inject headers through the constructor for local
    integration tests.
    """

    def __init__(
        self,
        capability: MinerCapability,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.capability = capability
        self.headers = headers or {}
        self.timeout_seconds = timeout_seconds

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
        headers = {"Accept": "application/json", **self.headers}
        if request_id:
            headers["X-Request-ID"] = request_id
            headers["X-OathCast-Application-Request-ID"] = request_id
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
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
        self.endpoint = endpoint or capability.endpoint_name
        self.demand_ledger = demand_ledger

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
        request_headers = None
        if request_id:
            request_headers = {
                "X-Request-ID": request_id,
                "X-OathCast-Application-Request-ID": request_id,
            }
        response = self.payment_client.request_miner(
            self.capability.miner_id,
            self.endpoint,
            params,
            request_headers=request_headers,
        ) if request_headers is not None else self.payment_client.request_miner(
            self.capability.miner_id,
            self.endpoint,
            params,
        )
        if self.demand_ledger is not None:
            protocol_result = ProtocolResultEnvelope.from_payment_response(
                response,
                route_mode="telegraph",
                registry_snapshot_sha256=self.capability.registry_snapshot_sha256,
            )
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
        return ProtocolResultEnvelope.from_payment_response(
            response,
            route_mode="telegraph",
            registry_snapshot_sha256=self.capability.registry_snapshot_sha256,
        )


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
    ) -> None:
        self.capabilities = list(capabilities)
        self.clients = clients
        self.own_slugs = own_slugs
        self.require_external = require_external
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    def _reply(
        self,
        capability: MinerCapability,
        question: ForecastQuestion,
        request_id: str,
    ) -> MinerReply:
        started = time.perf_counter()
        try:
            raw = self._call_client(self.clients[capability.slug], question, request_id)
            probability = extract_probability(raw)
            error = None if probability is not None else "response has no valid probability"
            protocol_result = raw if isinstance(raw, ProtocolResultEnvelope) else None
            raw_body = protocol_result.body if protocol_result is not None else raw
            return MinerReply(
                miner_id=capability.miner_id,
                slug=capability.slug,
                owned=capability.slug in self.own_slugs,
                raw_response=raw_body,
                probability=probability,
                content=extract_content(raw),
                latency_ms=(time.perf_counter() - started) * 1000,
                transport="development_http_or_injected",
                error=error,
                received_at=self.clock().astimezone(UTC),
                validity_reason=error,
                request_id=request_id,
                protocol_result=protocol_result,
            )
        except Exception as exc:
            return MinerReply(
                miner_id=capability.miner_id,
                slug=capability.slug,
                owned=capability.slug in self.own_slugs,
                raw_response=None,
                probability=None,
                content="",
                latency_ms=(time.perf_counter() - started) * 1000,
                transport="development_http_or_injected",
                error=str(exc),
                received_at=self.clock().astimezone(UTC),
                validity_reason=str(exc),
                request_id=request_id,
            )

    @staticmethod
    def _call_client(client: MinerClient, question: ForecastQuestion, request_id: str) -> Any:
        request_with_id = getattr(client, "request_with_id", None)
        if callable(request_with_id):
            return request_with_id(question, request_id)
        return client(question)

    def decide(self, question: ForecastQuestion, *, disable_owned: bool = False) -> ApplicationDecision:
        application_request_id = f"app-{uuid.uuid4().hex}"
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
            event_likely=aggregate >= 0.5,
            recommended_action="plan_for_event" if aggregate >= 0.5 else "plan_for_no_event",
            used_external_miner=bool(external_valid),
            external_influence=external_influence,
            replies=tuple(replies),
            decided_at=self.clock().astimezone(UTC),
            application_request_id=application_request_id,
        )
