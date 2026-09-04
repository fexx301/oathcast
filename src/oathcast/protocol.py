"""Protocol-level result envelopes and receipt provenance.

OathCast has two different evidence loops:

* the Miner forecast receipt, which freezes a weather answer; and
* the Telegraph protocol receipt, which describes routing and payment.

They must not be collapsed into one boolean or one hash.  This module keeps
the protocol envelope explicit while remaining independent of Telegraph's
unreleased receipt verifier and Hackathon 1 schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


#: Agent string for every outbound OathCast request to a host we do not own.
#:
#: This is not cosmetic. Probing the Explorer on 2026-08-12 showed it rejects
#: urllib's default `Python-urllib/*` agent with HTTP 403 on an anchored,
#: case-sensitive match of that literal prefix, while accepting any descriptive
#: string. Telegraph's dispatcher does not filter today, but it is currently a
#: bare IP with no CDN in front of it; the moment it moves behind the same edge
#: as the Explorer, any caller still sending the default agent starts failing.
#: Sending one honest string everywhere means that change is a non-event.
#:
#: It names the project and links the source so an operator reading their logs
#: can tell who is calling. It deliberately does **not** impersonate a browser:
#: the point is to be identifiable, not to evade a bot filter.
USER_AGENT = "OathCast/1.0 (+https://github.com/fexx301/oathcast)"


def outbound_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Standard headers for an outbound call, with `User-Agent` always set.

    Callers may override any value, including the agent, by passing it in
    `extra` — a caller that must present a different identity should do so
    explicitly rather than by omission, which is how the default agent reached
    the Explorer in the first place.
    """

    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    headers.update(extra or {})
    return headers


ROUTE_MODES = frozenset({"telegraph", "direct", "auto", "fixture", "unknown"})
SETTLEMENT_VERIFICATION_STATES = frozenset(
    {"not_attempted", "unverified", "verified", "invalid", "unknown"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProtocolReceipt:
    """Evidence metadata for one protocol-routed response.

    ``settlement_artifact_sha256`` identifies the returned settlement artifact
    without retaining the raw header in the Application evidence store.
    Presence of that artifact is not verification; callers must inspect
    ``settlement_verification``.
    """

    request_url: str | None
    route_mode: str
    response_status: int | None
    received_at: str | None
    challenge_sha256: str | None = None
    payment_attempt_id: str | None = None
    settlement_artifact_sha256: str | None = None
    settlement_verification: str = "not_attempted"
    signal_receipt_sha256: str | None = None
    registry_snapshot_sha256: str | None = None
    challenge_deadline: str | None = None
    response_sha256: str | None = None
    response_body_sha256: str | None = None
    request_url_sha256: str | None = None
    settlement_transaction_signature: str | None = None

    def __post_init__(self) -> None:
        if self.route_mode not in ROUTE_MODES:
            raise ValueError(f"unsupported protocol route mode: {self.route_mode}")
        if self.settlement_verification not in SETTLEMENT_VERIFICATION_STATES:
            raise ValueError(
                "unsupported settlement verification state: "
                f"{self.settlement_verification}"
            )
        if self.response_status is not None and not 100 <= int(self.response_status) <= 599:
            raise ValueError("response_status must be an HTTP status code")

    @property
    def settlement_verified(self) -> bool:
        return self.settlement_verification == "verified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_url": self.request_url,
            "route_mode": self.route_mode,
            "response_status": self.response_status,
            "received_at": self.received_at,
            "challenge_sha256": self.challenge_sha256,
            "payment_attempt_id": self.payment_attempt_id,
            "settlement_artifact_sha256": self.settlement_artifact_sha256,
            "settlement_verification": self.settlement_verification,
            "signal_receipt_sha256": self.signal_receipt_sha256,
            "registry_snapshot_sha256": self.registry_snapshot_sha256,
            "challenge_deadline": self.challenge_deadline,
            "response_sha256": self.response_sha256,
            "response_body_sha256": self.response_body_sha256,
            "request_url_sha256": self.request_url_sha256,
            "settlement_transaction_signature": self.settlement_transaction_signature,
        }

    @property
    def receipt_sha256(self) -> str:
        return _sha256_json(self.to_dict())


@dataclass(frozen=True)
class ProtocolResultEnvelope:
    """Response body plus protocol provenance.

    The small mapping-like helpers preserve the old local test ergonomics for
    JSON object responses while ensuring Application routing can retain the
    receipt metadata alongside the body.
    """

    body: Any
    receipt: ProtocolReceipt

    @classmethod
    def from_payment_response(
        cls,
        response: Any,
        *,
        route_mode: str = "telegraph",
        registry_snapshot_sha256: str | None = None,
    ) -> "ProtocolResultEnvelope":
        raw_settlement = getattr(response, "settlement_proof", None)
        artifact_hash = getattr(response, "settlement_artifact_sha256", None)
        if artifact_hash is None and isinstance(raw_settlement, str) and raw_settlement:
            artifact_hash = hashlib.sha256(raw_settlement.encode("utf-8")).hexdigest()
        verification = getattr(response, "settlement_verification", None)
        if not isinstance(verification, str):
            verification = (
                "verified"
                if bool(getattr(response, "settlement_verified", False))
                else ("unverified" if raw_settlement else "not_attempted")
            )
        headers = getattr(response, "headers", {})
        signal_receipt_hash = None
        if isinstance(headers, dict):
            for key, value in headers.items():
                if key.lower() in {"x-signal-receipt", "x-telegraph-signal-receipt"}:
                    if isinstance(value, str) and value:
                        signal_receipt_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
                    break
        body = getattr(response, "body", None)
        response_hash = _sha256_json(body)
        receipt = ProtocolReceipt(
            request_url=getattr(response, "request_url", None),
            route_mode=route_mode,
            response_status=getattr(response, "status", None),
            received_at=getattr(response, "received_at", None),
            challenge_sha256=getattr(response, "challenge_sha256", None),
            payment_attempt_id=getattr(response, "payment_attempt_id", None),
            settlement_artifact_sha256=artifact_hash,
            settlement_verification=verification,
            signal_receipt_sha256=signal_receipt_hash,
            registry_snapshot_sha256=registry_snapshot_sha256,
            challenge_deadline=getattr(response, "challenge_deadline", None),
            response_sha256=response_hash,
            response_body_sha256=getattr(response, "response_body_sha256", None),
            request_url_sha256=getattr(response, "target_sha256", None),
            settlement_transaction_signature=getattr(response, "transaction_signature", None),
        )
        return cls(body=body, receipt=receipt)

    def __getitem__(self, key: str) -> Any:
        if not isinstance(self.body, dict):
            raise TypeError("protocol response body is not a JSON object")
        return self.body[key]

    def get(self, key: str, default: Any = None) -> Any:
        if not isinstance(self.body, dict):
            return default
        return self.body.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "receipt": self.receipt.to_dict(),
            "receipt_sha256": self.receipt.receipt_sha256,
        }
