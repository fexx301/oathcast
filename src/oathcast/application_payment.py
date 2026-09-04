"""Python control-plane client for the private Track 3 payment sidecar.

This module intentionally contains no wallet, Solana signer, x402 header
builder, or payment retry logic. It speaks a small authenticated protocol over
an AF_UNIX socket to ``payment-canary/src/application-sidecar.ts``. Keeping
that boundary explicit makes it possible to audit the component that can
spend funds separately from the component that handles user input and policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import socket
import stat
from typing import Any, Mapping, Protocol


SIDECAR_PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
MINER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENDPOINT_PATTERN = re.compile(r"^[A-Za-z0-9_~-]{1,128}$")
EXPECTED_DEVNET_NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


class ApplicationPaymentError(RuntimeError):
    """Base class for fail-closed Application payment errors."""


class PaymentBoundaryUnavailable(ApplicationPaymentError):
    """The private sidecar could not be reached or returned malformed data."""


class PaymentAuthorizationError(ApplicationPaymentError):
    """The sidecar rejected the private control-plane credential."""


class PaymentPreflightRejected(ApplicationPaymentError):
    """The unpaid challenge or route failed the sidecar policy."""


class PaymentOutcomeUnknown(ApplicationPaymentError):
    """A paid request may have been sent; retrying would be unsafe."""


class PaymentPolicyConflict(ApplicationPaymentError):
    """An idempotency key or allowlist policy was reused inconsistently."""


class PaymentConsentRequired(ApplicationPaymentError):
    """The user did not explicitly consent to a paid Application request."""


class SidecarClient(Protocol):
    def request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_request_fingerprint(
    *,
    principal_id: str,
    idempotency_key: str,
    miner_id: str,
    endpoint: str,
    params: Mapping[str, str],
) -> str:
    """Return the cross-language canonical request binding used by the sidecar."""

    return _sha256_json(
        {
            "version": SIDECAR_PROTOCOL_VERSION,
            "principal_id": principal_id,
            "idempotency_key": idempotency_key,
            "miner_id": miner_id,
            "endpoint": endpoint,
            "params": dict(params),
        }
    )


def _safe_text(value: Any, *, name: str, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PaymentPolicyConflict(f"{name} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PaymentPolicyConflict(f"{name} contains a control character")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise PaymentPolicyConflict(f"{name} is invalid")
    return value


def _safe_hash(value: Any, *, name: str) -> str:
    return _safe_text(value, name=name, maximum=64, pattern=HASH_PATTERN)


@dataclass(frozen=True)
class ApplicationPaidResponse:
    """Sanitized, replayable result returned by the sidecar."""

    body: Any
    status: int
    operation_id: str
    payment_attempt_id: str
    challenge_sha256: str
    target_sha256: str
    settlement_artifact_sha256: str
    transaction_signature: str
    response_body_sha256: str
    evidence: Mapping[str, Any]
    received_at: str
    settlement_verification: str = "verified"
    request_url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 200 <= int(self.status) <= 299:
            raise PaymentBoundaryUnavailable("sidecar returned a non-success status")
        for name, value in (
            ("operation_id", self.operation_id),
            ("payment_attempt_id", self.payment_attempt_id),
        ):
            _safe_text(value, name=name, maximum=128, pattern=ID_PATTERN)
        for name, value in (
            ("challenge_sha256", self.challenge_sha256),
            ("target_sha256", self.target_sha256),
            ("settlement_artifact_sha256", self.settlement_artifact_sha256),
            ("response_body_sha256", self.response_body_sha256),
        ):
            _safe_hash(value, name=name)
        if self.settlement_verification != "verified":
            raise PaymentBoundaryUnavailable("sidecar did not return verified settlement")
        if not isinstance(self.evidence, Mapping):
            raise PaymentBoundaryUnavailable("sidecar evidence is malformed")

    @property
    def settlement_proof(self) -> None:
        """Raw payment headers never cross into the Python control plane."""

        return None

    @property
    def settlement_verified(self) -> bool:
        return True


class UnixSocketPaymentClient:
    """Bounded newline JSON client for the private sidecar."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        auth_token: str,
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self.socket_path = os.fspath(socket_path)
        if not os.path.isabs(self.socket_path):
            raise ValueError("socket_path must be absolute")
        if not isinstance(auth_token, str) or not 32 <= len(auth_token.encode()) <= 512:
            raise ValueError("auth_token must be 32-512 bytes")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("socket client limits must be positive")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    @property
    def configured(self) -> bool:
        try:
            return stat.S_ISSOCK(os.stat(self.socket_path).st_mode)
        except OSError:
            return False

    def request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        message = dict(payload)
        message["version"] = SIDECAR_PROTOCOL_VERSION
        message["authorization"] = self.auth_token
        try:
            encoded = (_canonical_json(message) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PaymentPolicyConflict("sidecar request is not JSON-safe") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise PaymentPolicyConflict("sidecar request is too large")

        chunks: list[bytes] = []
        total = 0
        request_may_have_run = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(self.socket_path)
                # Once sendall begins, the sidecar may have received enough of
                # the request to spend funds even if this process loses the
                # response. Treat every subsequent transport failure as
                # ambiguous and require explicit reconciliation.
                request_may_have_run = True
                connection.sendall(encoded)
                while True:
                    chunk = connection.recv(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        raise PaymentBoundaryUnavailable("sidecar response exceeds its size cap")
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
        except PaymentBoundaryUnavailable as exc:
            if request_may_have_run:
                raise PaymentOutcomeUnknown(
                    "the private payment sidecar outcome is unknown"
                ) from exc
            raise
        except (OSError, TimeoutError) as exc:
            if request_may_have_run:
                raise PaymentOutcomeUnknown(
                    "the private payment sidecar outcome is unknown"
                ) from exc
            raise PaymentBoundaryUnavailable("the private payment sidecar is unavailable") from exc

        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw:
            if request_may_have_run:
                raise PaymentOutcomeUnknown("the private payment sidecar outcome is unknown")
            raise PaymentBoundaryUnavailable("the payment sidecar returned no response")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            if request_may_have_run:
                raise PaymentOutcomeUnknown(
                    "the private payment sidecar outcome is unknown"
                ) from exc
            raise PaymentBoundaryUnavailable("the payment sidecar returned malformed JSON") from exc
        if not isinstance(value, dict):
            if request_may_have_run:
                raise PaymentOutcomeUnknown("the private payment sidecar outcome is unknown")
            raise PaymentBoundaryUnavailable("the payment sidecar returned a non-object")
        return value


def _error_from_sidecar(payload: Mapping[str, Any]) -> ApplicationPaymentError:
    raw_error = payload.get("error")
    if not isinstance(raw_error, Mapping):
        return PaymentBoundaryUnavailable("the payment sidecar returned an invalid error")
    code = raw_error.get("code")
    if not isinstance(code, str):
        code = "SIDECAR_ERROR"
    messages = {
        "AUTHORIZATION_FAILED": "sidecar authorization failed",
        "PAYMENT_OUTCOME_UNKNOWN": "the payment outcome requires explicit reconciliation",
        "PAYMENT_IN_PROGRESS": "the payment attempt is already in progress",
        "PAYMENT_BUDGET_EXHAUSTED": "the Application payment budget is exhausted",
        "IDEMPOTENCY_CONFLICT": "the Application idempotency key conflicts with stored evidence",
        "IDEMPOTENCY_CONSUMED": "the Application idempotency key was already consumed",
        "ALLOWLIST_REJECTED": "the requested Miner route is not allowlisted",
        "TARGET_REJECTED": "the requested Miner route was rejected",
        "REQUEST_FINGERPRINT_MISMATCH": "the Application request fingerprint is invalid",
        "PREFLIGHT_FETCH_FAILED": "the Miner preflight was unavailable",
        "PREFLIGHT_NOT_PAYMENT_REQUIRED": "the Miner did not return a payment challenge",
        "INCOMPLETE_SETTLEMENT_EVIDENCE": "the payment outcome requires explicit reconciliation",
        "RECONCILED_PAYMENT_RESPONSE_UNAVAILABLE": "the reconciled payment response is not replayable",
        "JOURNAL_FAILURE": "the private payment journal is unavailable",
    }
    message = messages.get(code, "the private payment sidecar rejected the request")
    if code in {"AUTHORIZATION_FAILED"}:
        return PaymentAuthorizationError(message)
    if code in {
        "PAYMENT_OUTCOME_UNKNOWN",
        "PAYMENT_IN_PROGRESS",
        "INCOMPLETE_SETTLEMENT_EVIDENCE",
        "RECONCILED_PAYMENT_RESPONSE_UNAVAILABLE",
        "JOURNAL_FAILURE",
    }:
        return PaymentOutcomeUnknown(message)
    if code in {
        "IDEMPOTENCY_CONFLICT",
        "IDEMPOTENCY_CONSUMED",
        "PAYMENT_BUDGET_EXHAUSTED",
    }:
        return PaymentPolicyConflict(message)
    return PaymentPreflightRejected(message)


def _validate_paid_evidence(
    payload: Mapping[str, Any],
    *,
    status: int,
    operation_id: str,
    challenge_sha256: str,
    target_sha256: str,
    settlement_artifact_sha256: str,
    transaction_signature: str,
    response_body_sha256: str,
) -> Mapping[str, Any]:
    evidence = payload["evidence"]
    if not isinstance(evidence, Mapping) or evidence.get("ok") is not True:
        raise PaymentBoundaryUnavailable("the payment sidecar evidence is not verified")
    if evidence.get("mode") != "execute" or evidence.get("operation_id") != operation_id:
        raise PaymentBoundaryUnavailable("the payment sidecar evidence is mismatched")

    preflight = evidence.get("preflight")
    if not isinstance(preflight, Mapping):
        raise PaymentBoundaryUnavailable("the payment sidecar preflight evidence is malformed")
    if (
        preflight.get("challenge_validated") is not True
        or preflight.get("payment_attempted") is not True
        or preflight.get("challenge_sha256") != challenge_sha256
    ):
        raise PaymentBoundaryUnavailable("the payment sidecar preflight evidence is unverified")

    settlement = evidence.get("settlement")
    if not isinstance(settlement, Mapping) or (
        settlement.get("success") is not True
        or settlement.get("network") != EXPECTED_DEVNET_NETWORK
        or settlement.get("header_sha256") != settlement_artifact_sha256
        or settlement.get("transaction_signature") != transaction_signature
    ):
        raise PaymentBoundaryUnavailable("the payment sidecar settlement evidence is malformed")

    verification = evidence.get("verification")
    if not isinstance(verification, Mapping):
        raise PaymentBoundaryUnavailable("the payment sidecar verification evidence is malformed")
    token_movement = verification.get("token_movement")
    if (
        verification.get("confirmed_transaction") is not True
        or verification.get("transaction_error") is not False
        or verification.get("transaction_signature_matches") is not True
        or verification.get("fee_payer_verified") is not True
        or not isinstance(token_movement, Mapping)
        or token_movement.get("status") != "verified"
    ):
        raise PaymentBoundaryUnavailable("the payment sidecar settlement is not independently verified")

    if evidence.get("paid_response_status") != status:
        raise PaymentBoundaryUnavailable("the payment sidecar response status is mismatched")
    if evidence.get("paid_response_body_sha256") != response_body_sha256:
        raise PaymentBoundaryUnavailable("the payment sidecar response body hash is mismatched")
    target = evidence.get("target")
    if not isinstance(target, Mapping) or target.get("request_url_sha256") != target_sha256:
        raise PaymentBoundaryUnavailable("the payment sidecar target evidence is mismatched")
    return evidence


def _paid_response_from_mapping(payload: Mapping[str, Any]) -> ApplicationPaidResponse:
    if payload.get("version") != SIDECAR_PROTOCOL_VERSION or payload.get("ok") is not True:
        raise _error_from_sidecar(payload)
    required = (
        "body",
        "status",
        "operation_id",
        "payment_attempt_id",
        "body_sha256",
        "challenge_sha256",
        "target_sha256",
        "settlement_artifact_sha256",
        "transaction_signature",
        "received_at",
        "verification",
        "evidence",
    )
    if any(key not in payload for key in required):
        raise PaymentBoundaryUnavailable("the payment sidecar response is incomplete")
    try:
        status = int(payload["status"])
    except (TypeError, ValueError) as exc:
        raise PaymentBoundaryUnavailable("the payment sidecar status is invalid") from exc
    operation_id = _safe_text(payload["operation_id"], name="operation_id", maximum=128, pattern=ID_PATTERN)
    payment_attempt_id = _safe_text(
        payload["payment_attempt_id"],
        name="payment_attempt_id",
        maximum=128,
        pattern=ID_PATTERN,
    )
    challenge_sha256 = _safe_hash(payload["challenge_sha256"], name="challenge_sha256")
    target_sha256 = _safe_hash(payload["target_sha256"], name="target_sha256")
    settlement_artifact_sha256 = _safe_hash(
        payload["settlement_artifact_sha256"],
        name="settlement_artifact_sha256",
    )
    transaction_signature = _safe_text(
        payload["transaction_signature"],
        name="transaction_signature",
        maximum=128,
    )
    response_body_sha256 = _safe_hash(payload["body_sha256"], name="body_sha256")
    evidence = _validate_paid_evidence(
        payload,
        status=status,
        operation_id=operation_id,
        challenge_sha256=challenge_sha256,
        target_sha256=target_sha256,
        settlement_artifact_sha256=settlement_artifact_sha256,
        transaction_signature=transaction_signature,
        response_body_sha256=response_body_sha256,
    )
    received_at = _safe_text(payload["received_at"], name="received_at", maximum=64)
    return ApplicationPaidResponse(
        body=payload["body"],
        status=status,
        operation_id=operation_id,
        payment_attempt_id=payment_attempt_id,
        challenge_sha256=challenge_sha256,
        target_sha256=target_sha256,
        settlement_artifact_sha256=settlement_artifact_sha256,
        transaction_signature=transaction_signature,
        response_body_sha256=response_body_sha256,
        evidence=evidence,
        received_at=received_at,
    )


class ApplicationPaymentBoundary:
    """Validate Application policy before delegating to the private sidecar."""

    def __init__(
        self,
        client: SidecarClient,
        *,
        allowed_miner_ids: set[str] | frozenset[str],
        allowed_endpoints: set[str] | frozenset[str],
        max_params: int = 32,
        max_param_value_length: int = 512,
    ) -> None:
        if not allowed_miner_ids or not allowed_endpoints:
            raise ValueError("a non-empty payment allowlist is required")
        self.client = client
        self.allowed_miner_ids = frozenset(allowed_miner_ids)
        self.allowed_endpoints = frozenset(allowed_endpoints)
        self.max_params = max_params
        self.max_param_value_length = max_param_value_length

    @property
    def configured(self) -> bool:
        configured = getattr(self.client, "configured", None)
        return True if configured is None else bool(configured)

    def request_miner(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        miner_id: str,
        endpoint: str,
        params: Mapping[str, str],
        consent: bool,
    ) -> ApplicationPaidResponse:
        if consent is not True:
            raise PaymentConsentRequired("explicit consent is required for a paid request")
        _safe_text(principal_id, name="principal_id", maximum=128, pattern=ID_PATTERN)
        _safe_text(idempotency_key, name="idempotency_key", maximum=128, pattern=ID_PATTERN)
        _safe_hash(request_fingerprint, name="request_fingerprint")
        _safe_text(miner_id, name="miner_id", maximum=64, pattern=MINER_ID_PATTERN)
        _safe_text(endpoint, name="endpoint", maximum=128, pattern=ENDPOINT_PATTERN)
        if miner_id not in self.allowed_miner_ids or endpoint not in self.allowed_endpoints:
            raise PaymentPolicyConflict("the requested Miner route is not allowlisted")
        if len(params) > self.max_params:
            raise PaymentPolicyConflict("too many Miner parameters")
        normalized_params: dict[str, str] = {}
        for key, value in params.items():
            _safe_text(key, name="parameter name", maximum=64, pattern=re.compile(r"^[A-Za-z0-9_.~-]+$"))
            normalized_params[key] = _safe_text(
                value,
                name="parameter value",
                maximum=self.max_param_value_length,
            )
        expected = canonical_request_fingerprint(
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            miner_id=miner_id,
            endpoint=endpoint,
            params=normalized_params,
        )
        if expected != request_fingerprint:
            raise PaymentPolicyConflict("request_fingerprint does not match the canonical request")
        payload = {
            "kind": "paid_miner_request",
            "principal_id": principal_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "miner_id": miner_id,
            "endpoint": endpoint,
            "params": normalized_params,
        }
        try:
            response = self.client.request(payload)
        except ApplicationPaymentError:
            raise
        except Exception as exc:
            raise PaymentBoundaryUnavailable("the private payment sidecar is unavailable") from exc
        return _paid_response_from_mapping(response)
