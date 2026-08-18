"""Legacy Base-Sepolia x402 transport boundary retained for regression tests.

Telegraph's live Hackathon API currently challenges for Solana-devnet USDC, so
new canary and Application work must use ``payment-canary/``.  This module is
not a live-compatible signer.  It remains useful for the older EVM policy and
journal tests and deliberately cannot fake wallet signing.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import threading
from typing import Any, Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from oathcast.protocol import outbound_headers


DEFAULT_DISPATCHER_URL = os.getenv(
    "OATHCAST_DISPATCHER_URL",
    "http://13.237.89.59:7044/miner-dispatcher",
)
BASE_SEPOLIA_NETWORK = "eip155:84532"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
UTC = timezone.utc
MINIMUM_USDC_MICROUNITS = 10_000
try:
    DEFAULT_MAX_PAYMENT_MICRO_USDC = int(
        os.getenv("OATHCAST_MAX_PAYMENT_MICRO_USDC", str(MINIMUM_USDC_MICROUNITS))
    )
except ValueError:
    DEFAULT_MAX_PAYMENT_MICRO_USDC = MINIMUM_USDC_MICROUNITS
DEFAULT_MAX_RESPONSE_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class PaymentChallenge:
    encoded_header: str
    payload: dict[str, Any]

    @classmethod
    def decode(cls, encoded_header: str) -> "PaymentChallenge":
        padded = encoded_header + ("=" * (-len(encoded_header) % 4))
        try:
            decoded = base64.b64decode(padded.encode("ascii"), validate=False)
            payload = json.loads(decoded.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Payment-Required header") from exc
        if not isinstance(payload, dict):
            raise ValueError("Payment-Required payload must be a JSON object")
        return cls(encoded_header=encoded_header, payload=payload)

    @property
    def accepts(self) -> list[dict[str, Any]]:
        accepts = self.payload.get("accepts", [])
        return accepts if isinstance(accepts, list) else []

    def supports_base_sepolia_usdc(self) -> bool:
        return any(
            isinstance(option, dict)
            and option.get("network") == BASE_SEPOLIA_NETWORK
            and option.get("asset", "").lower() == BASE_SEPOLIA_USDC.lower()
            for option in self.accepts
        )

    @property
    def version(self) -> int | None:
        value = self.payload.get("x402Version", self.payload.get("x402_version"))
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _amount(option: dict[str, Any]) -> int | None:
        value = option.get("amount", option.get("maxAmountRequired"))
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            amount = value
        elif isinstance(value, str) and value.strip().isdigit():
            # x402 exact amounts are integer base units (micro-USDC here).
            # Do not guess whether a decimal such as "0.01" means USDC or
            # micro-USDC; the challenge must be explicit.
            amount = int(value.strip())
        else:
            return None
        return amount if amount >= 0 else None

    @staticmethod
    def _deadline_epoch(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value) if float(value) >= 0 else None
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                numeric = float(text)
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    return None
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    return None
                return parsed.astimezone(UTC).timestamp()
            return numeric if numeric >= 0 else None
        return None

    @property
    def deadline_epoch_seconds(self) -> float | None:
        for key in ("deadline", "expiresAt", "expires_at", "expiration"):
            if key in self.payload:
                return self._deadline_epoch(self.payload[key])
        return None

    def validate_deadline(
        self,
        *,
        now: datetime | None = None,
        required: bool = False,
    ) -> str | None:
        """Validate an optional challenge deadline without guessing units."""

        deadline = self.deadline_epoch_seconds
        if deadline is None:
            if required and any(
                key in self.payload
                for key in ("deadline", "expiresAt", "expires_at", "expiration")
            ):
                raise PaymentPolicyError("payment challenge has an invalid deadline")
            if required:
                raise PaymentPolicyError("payment challenge has no deadline")
            return None
        current = now or datetime.now(tz=UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("payment deadline comparison requires a timezone")
        if deadline <= current.astimezone(UTC).timestamp():
            raise PaymentPolicyError("payment challenge deadline has expired")
        return datetime.fromtimestamp(deadline, tz=UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _resource_url(option: dict[str, Any], payload: dict[str, Any]) -> str | None:
        resource = option.get("resource", payload.get("resource"))
        if isinstance(resource, dict):
            resource = resource.get("url")
        return resource if isinstance(resource, str) and resource else None

    def validate_for_request(
        self,
        request_url: str,
        *,
        expected_pay_to: str,
        max_amount_micro_usdc: int,
        allowed_versions: frozenset[int] = frozenset({2}),
        require_resource: bool = False,
        now: datetime | None = None,
        require_deadline: bool = False,
    ) -> dict[str, Any]:
        """Return one safe exact payment option or fail before signing."""

        if self.version not in allowed_versions:
            raise PaymentPolicyError(
                f"unsupported x402 version: {self.payload.get('x402Version')}"
            )
        if max_amount_micro_usdc < MINIMUM_USDC_MICROUNITS:
            raise PaymentPolicyError("payment cap is below Telegraph's 0.01 USDC floor")
        self.validate_deadline(now=now, required=require_deadline)

        for option in self.accepts:
            if not isinstance(option, dict):
                continue
            if str(option.get("scheme", "")).lower() != "exact":
                continue
            if option.get("network") != BASE_SEPOLIA_NETWORK:
                continue
            if str(option.get("asset", "")).lower() != BASE_SEPOLIA_USDC.lower():
                continue

            amount = self._amount(option)
            if amount is None or amount < MINIMUM_USDC_MICROUNITS:
                raise PaymentPolicyError("payment option has an invalid amount")
            if amount > max_amount_micro_usdc:
                raise PaymentPolicyError(
                    f"payment amount {amount} exceeds configured cap {max_amount_micro_usdc}"
                )
            pay_to = option.get("payTo", option.get("pay_to"))
            if not isinstance(pay_to, str) or not pay_to:
                raise PaymentPolicyError("payment option has no recipient")
            if pay_to.lower() != expected_pay_to.lower():
                raise PaymentPolicyError("payment recipient does not match the approved recipient")

            resource_url = self._resource_url(option, self.payload)
            if require_resource and resource_url is None:
                raise PaymentPolicyError("payment challenge omitted a required resource URL")
            if resource_url is not None and resource_url != request_url:
                raise PaymentPolicyError("payment resource does not match the requested URL")
            return option

        raise PaymentPolicyError("no approved Base Sepolia USDC exact payment option")


@dataclass(frozen=True)
class PaymentResponse:
    status: int
    body: Any
    headers: dict[str, str]
    request_url: str | None = None
    received_at: str | None = None
    payment_attempt_id: str | None = None
    challenge_sha256: str | None = None
    challenge_deadline: str | None = None
    settlement_verified: bool = False
    settlement_verification: str = "not_attempted"

    @property
    def settlement_proof(self) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == "x-payment-settle-response":
                return value if value.strip() else None
        return None

    @property
    def settlement_artifact_sha256(self) -> str | None:
        artifact = self.settlement_proof
        if artifact is None:
            return None
        return hashlib.sha256(artifact.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaymentPreflight:
    request_url: str
    status: int
    challenge: PaymentChallenge | None
    response: PaymentResponse | None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidatedPaymentAuthorization:
    """The one exact payment option a signer is allowed to authorize.

    The original challenge and selected option are stored as canonical JSON,
    not mutable dictionaries. Properties return fresh copies. The constrained
    challenge exposed to a signer contains only the validated option, so a
    signer cannot silently choose a different option from a multi-option
    challenge.
    """

    request_url: str
    challenge_sha256: str
    version: int
    scheme: str
    network: str
    asset: str
    amount_micro_usdc: int
    pay_to: str
    resource_url: str | None
    _challenge_payload_json: str
    _option_json: str

    @classmethod
    def from_challenge(
        cls,
        challenge: PaymentChallenge,
        request_url: str,
        option: dict[str, Any],
    ) -> "ValidatedPaymentAuthorization":
        version = challenge.version
        if version is None:
            raise ValueError("validated payment challenge has no numeric version")
        amount = PaymentChallenge._amount(option)
        pay_to = option.get("payTo", option.get("pay_to"))
        if amount is None or not isinstance(pay_to, str) or not pay_to:
            raise ValueError("validated payment option is incomplete")
        resource_url = PaymentChallenge._resource_url(option, challenge.payload)
        challenge_json = _canonical_json(challenge.payload)
        option_json = _canonical_json(option)
        return cls(
            request_url=request_url,
            challenge_sha256=_sha256_text(challenge_json),
            version=version,
            scheme=str(option.get("scheme", "")).lower(),
            network=str(option.get("network", "")),
            asset=str(option.get("asset", "")).lower(),
            amount_micro_usdc=amount,
            pay_to=pay_to,
            resource_url=resource_url,
            _challenge_payload_json=challenge_json,
            _option_json=option_json,
        )

    @property
    def option(self) -> dict[str, Any]:
        value = json.loads(self._option_json)
        if not isinstance(value, dict):
            raise RuntimeError("validated payment option is not a JSON object")
        return value

    @property
    def challenge(self) -> PaymentChallenge:
        payload = json.loads(self._challenge_payload_json)
        if not isinstance(payload, dict):
            raise RuntimeError("validated payment challenge is not a JSON object")
        payload["accepts"] = [self.option]
        return PaymentChallenge(encoded_header="", payload=payload)

    @property
    def authorization_sha256(self) -> str:
        return _sha256_text(
            _canonical_json(
                {
                    "request_url": self.request_url,
                    "challenge_sha256": self.challenge_sha256,
                    "version": self.version,
                    "scheme": self.scheme,
                    "network": self.network,
                    "asset": self.asset,
                    "amount_micro_usdc": self.amount_micro_usdc,
                    "pay_to": self.pay_to,
                    "resource_url": self.resource_url,
                    "option_json": self._option_json,
                }
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_url": self.request_url,
            "challenge_sha256": self.challenge_sha256,
            "version": self.version,
            "scheme": self.scheme,
            "network": self.network,
            "asset": self.asset,
            "amount_micro_usdc": self.amount_micro_usdc,
            "pay_to": self.pay_to,
            "resource_url": self.resource_url,
            "option": self.option,
            "authorization_sha256": self.authorization_sha256,
        }


class PaymentSigner(Protocol):
    def __call__(self, authorization: ValidatedPaymentAuthorization) -> str:
        """Return a base64-encoded PAYMENT-SIGNATURE proof."""


SettlementVerifier = Callable[[PaymentResponse, ValidatedPaymentAuthorization], bool]


Transport = Callable[[str, str, dict[str, str]], HttpResult]


def _read_bounded_response(
    response: Any,
    *,
    max_body_bytes: int,
    source: str,
) -> bytes:
    """Read one dispatcher body with a hard byte cap and overflow detection."""

    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    response_headers = getattr(response, "headers", None)
    declared = (
        response_headers.get("Content-Length")
        if response_headers is not None
        else None
    )
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


def urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    max_response_body_bytes: int = DEFAULT_MAX_RESPONSE_BODY_BYTES,
) -> HttpResult:
    """Transport for the x402 flow.

    A non-2xx response is *returned* rather than raised, deliberately: the 402
    challenge lives in the error response's headers and body, so raising here
    would break the payment handshake. That also means a bot-filter 403 would
    arrive as an ordinary `HttpResult` rather than an obvious failure, which is
    why the agent is set explicitly here — see `protocol.USER_AGENT`. Telegraph's
    dispatcher does not filter agents today; it is a bare IP with no CDN, and the
    planned HTTPS switch could put it behind the same edge as the Explorer.
    """

    if max_response_body_bytes <= 0:
        raise ValueError("max_response_body_bytes must be positive")
    request = Request(url, method=method, headers=outbound_headers(headers))
    try:
        with urlopen(request, timeout=20) as response:
            return HttpResult(
                response.status,
                dict(response.headers.items()),
                _read_bounded_response(
                    response,
                    max_body_bytes=max_response_body_bytes,
                    source="dispatcher",
                ),
            )
    except HTTPError as error:
        return HttpResult(
            error.code,
            dict(error.headers.items()),
            _read_bounded_response(
                error,
                max_body_bytes=max_response_body_bytes,
                source="dispatcher",
            ),
        )


class PaymentRequiredError(RuntimeError):
    def __init__(self, challenge: PaymentChallenge):
        super().__init__("Telegraph returned HTTP 402 Payment Required")
        self.challenge = challenge


class PaymentPolicyError(RuntimeError):
    """Raised when a challenge is unsafe to sign."""


class PaymentBudgetExceeded(PaymentPolicyError):
    """Raised before signing after the configured paid-request budget is used."""


class DuplicatePaymentError(PaymentPolicyError):
    """Raised before signing the same request URL twice."""


class PaymentOutcomeUnknown(RuntimeError):
    """Raised after a payment proof was sent but settlement is not confirmed."""

    def __init__(self, request_url: str, message: str):
        super().__init__(message)
        self.request_url = request_url


PAYMENT_RESERVED = "RESERVED"
PAYMENT_SUBMITTED = "SUBMITTED"
PAYMENT_SETTLED = "SETTLED"
PAYMENT_UNKNOWN = "UNKNOWN"
PAYMENT_ABORTED = "ABORTED"
PAYMENT_BLOCKING_STATES = frozenset(
    {PAYMENT_RESERVED, PAYMENT_SUBMITTED, PAYMENT_SETTLED, PAYMENT_UNKNOWN}
)


def _timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(tz=UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("payment journal timestamps must include a timezone")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


class SqlitePaymentJournal:
    """Durable single-Application payment state and spend guard.

    The journal deliberately stores hashes and settlement evidence, never a
    private key or replayable payment proof. `UNKNOWN` and `SUBMITTED` records
    are blocking until an external reconciliation path clears them.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
        elif self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        connection = self._connection()
        try:
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS payment_journal (
                    request_url TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    amount_micro_usdc INTEGER NOT NULL,
                    pay_to TEXT NOT NULL,
                    network TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    challenge_sha256 TEXT NOT NULL,
                    authorization_sha256 TEXT NOT NULL,
                    settlement_evidence TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        finally:
            if self._memory_connection is None:
                connection.close()

    def _connection(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        return sqlite3.connect(self.path, timeout=10)

    def close(self) -> None:
        with self._lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def get(self, request_url: str) -> dict[str, Any] | None:
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM payment_journal WHERE request_url = ?",
                    (request_url,),
                ).fetchone()
                return self._row_to_dict(row)
            finally:
                if self._memory_connection is None:
                    connection.close()

    def _assert_available_in_transaction(
        self,
        connection: sqlite3.Connection,
        request_url: str,
        *,
        max_paid_requests: int,
        max_total_payment_micro_usdc: int,
        additional_amount_micro_usdc: int = 0,
    ) -> None:
        row = connection.execute(
            "SELECT status FROM payment_journal WHERE request_url = ?",
            (request_url,),
        ).fetchone()
        if row is not None and row[0] in PAYMENT_BLOCKING_STATES:
            status = row[0]
            if status == PAYMENT_UNKNOWN:
                raise DuplicatePaymentError(
                    "this request has an unresolved payment outcome and is blocked"
                )
            raise DuplicatePaymentError("this request URL already has a payment journal entry")

        count, total = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(amount_micro_usdc), 0)
            FROM payment_journal
            WHERE status IN (?, ?, ?, ?)
            """,
            (PAYMENT_RESERVED, PAYMENT_SUBMITTED, PAYMENT_SETTLED, PAYMENT_UNKNOWN),
        ).fetchone()
        if count >= max_paid_requests:
            raise PaymentBudgetExceeded("paid-request budget is exhausted")
        if total + additional_amount_micro_usdc > max_total_payment_micro_usdc:
            raise PaymentBudgetExceeded("cumulative payment budget is exhausted")

    def assert_available(
        self,
        request_url: str,
        *,
        max_paid_requests: int,
        max_total_payment_micro_usdc: int,
    ) -> None:
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                self._assert_available_in_transaction(
                    connection,
                    request_url,
                    max_paid_requests=max_paid_requests,
                    max_total_payment_micro_usdc=max_total_payment_micro_usdc,
                )
            finally:
                if self._memory_connection is None:
                    connection.close()

    def reserve(
        self,
        authorization: ValidatedPaymentAuthorization,
        *,
        max_paid_requests: int,
        max_total_payment_micro_usdc: int,
        now: datetime | None = None,
    ) -> None:
        timestamp = _timestamp(now)
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                self._assert_available_in_transaction(
                    connection,
                    authorization.request_url,
                    max_paid_requests=max_paid_requests,
                    max_total_payment_micro_usdc=max_total_payment_micro_usdc,
                    additional_amount_micro_usdc=authorization.amount_micro_usdc,
                )
                existing = connection.execute(
                    "SELECT status FROM payment_journal WHERE request_url = ?",
                    (authorization.request_url,),
                ).fetchone()
                if existing is not None and existing[0] == PAYMENT_ABORTED:
                    connection.execute(
                        "DELETE FROM payment_journal WHERE request_url = ?",
                        (authorization.request_url,),
                    )
                connection.execute(
                    """
                    INSERT INTO payment_journal (
                        request_url, status, amount_micro_usdc, pay_to, network, asset,
                        request_sha256, challenge_sha256, authorization_sha256,
                        settlement_evidence, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        authorization.request_url,
                        PAYMENT_RESERVED,
                        authorization.amount_micro_usdc,
                        authorization.pay_to,
                        authorization.network,
                        authorization.asset,
                        _sha256_text(authorization.request_url),
                        authorization.challenge_sha256,
                        authorization.authorization_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def _transition(
        self,
        request_url: str,
        *,
        from_status: str,
        to_status: str,
        settlement_evidence: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        with self._lock:
            connection = self._connection()
            try:
                timestamp = _timestamp(now)
                updated = connection.execute(
                    """
                    UPDATE payment_journal
                    SET status = ?, settlement_evidence = ?, error = ?, updated_at = ?
                    WHERE request_url = ? AND status = ?
                    """,
                    (to_status, settlement_evidence, error, timestamp, request_url, from_status),
                ).rowcount
                if updated != 1:
                    raise PaymentPolicyError(
                        f"payment journal transition {from_status}->{to_status} failed"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def mark_submitted(
        self,
        authorization: ValidatedPaymentAuthorization,
        *,
        now: datetime | None = None,
    ) -> None:
        self._transition(
            authorization.request_url,
            from_status=PAYMENT_RESERVED,
            to_status=PAYMENT_SUBMITTED,
            now=now,
        )

    def mark_settled(
        self,
        request_url: str,
        settlement_evidence: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        self._transition(
            request_url,
            from_status=PAYMENT_SUBMITTED,
            to_status=PAYMENT_SETTLED,
            settlement_evidence=settlement_evidence,
            now=now,
        )

    def mark_unknown(
        self,
        request_url: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._transition(
            request_url,
            from_status=PAYMENT_SUBMITTED,
            to_status=PAYMENT_UNKNOWN,
            error=error[:1000],
            now=now,
        )

    def abort_reservation(
        self,
        request_url: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._transition(
            request_url,
            from_status=PAYMENT_RESERVED,
            to_status=PAYMENT_ABORTED,
            error=error[:1000],
            now=now,
        )


class TelegraphX402Client:
    """Discover Miners and perform challenge/retry requests through a dispatcher."""

    def __init__(
        self,
        *,
        dispatcher_url: str = DEFAULT_DISPATCHER_URL,
        transport: Transport = urllib_transport,
        signer: PaymentSigner | None = None,
        expected_pay_to: str | None = None,
        allowed_miner_ids: set[str] | None = None,
        allowed_endpoints: set[str] | None = None,
        max_payment_micro_usdc: int = DEFAULT_MAX_PAYMENT_MICRO_USDC,
        max_paid_requests: int = 1,
        max_total_payment_micro_usdc: int | None = None,
        allow_insecure_transport: bool = False,
        require_settlement: bool = True,
        settlement_verifier: SettlementVerifier | None = None,
        require_challenge_deadline: bool = False,
        journal: SqlitePaymentJournal | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.dispatcher_url = dispatcher_url.rstrip("/")
        self.transport = transport
        self.signer = signer
        self.expected_pay_to = expected_pay_to or os.getenv("OATHCAST_EXPECTED_PAY_TO") or None
        self.allowed_miner_ids = None if allowed_miner_ids is None else {str(item) for item in allowed_miner_ids}
        self.allowed_endpoints = None if allowed_endpoints is None else {
            str(item).lstrip("/") for item in allowed_endpoints
        }
        self.max_payment_micro_usdc = max_payment_micro_usdc
        self.max_paid_requests = max_paid_requests
        configured_total = os.getenv("OATHCAST_MAX_TOTAL_PAYMENT_MICRO_USDC", "")
        if max_total_payment_micro_usdc is None and configured_total.strip():
            try:
                max_total_payment_micro_usdc = int(configured_total)
            except ValueError as exc:
                raise ValueError(
                    "OATHCAST_MAX_TOTAL_PAYMENT_MICRO_USDC must be an integer"
                ) from exc
        self.max_total_payment_micro_usdc = (
            max_total_payment_micro_usdc
            if max_total_payment_micro_usdc is not None
            else max_payment_micro_usdc * max_paid_requests
        )
        self.allow_insecure_transport = allow_insecure_transport
        self.require_settlement = require_settlement
        self.settlement_verifier = settlement_verifier
        self.require_challenge_deadline = require_challenge_deadline
        self.journal = journal
        self.clock = clock or (lambda: datetime.now(tz=UTC))

        if self.max_paid_requests < 1:
            raise ValueError("max_paid_requests must be positive")
        if self.max_total_payment_micro_usdc < MINIMUM_USDC_MICROUNITS:
            raise ValueError("cumulative payment cap is below Telegraph's 0.01 USDC floor")
        if self.signer is not None and self.journal is None:
            raise PaymentPolicyError(
                "a durable SQLite payment journal is required before signing"
            )
        if self.signer is not None and self.allow_insecure_transport:
            raise PaymentPolicyError("insecure payment transport overrides are disabled")
        if self.signer is not None and not self.require_settlement:
            raise PaymentPolicyError(
                "signed requests cannot disable settlement confirmation"
            )
        if self.signer is not None and self.settlement_verifier is None:
            raise PaymentPolicyError(
                "an independent settlement verifier is required before signing"
            )

    def _validate_target(self, miner_id: str | int, endpoint: str) -> tuple[str, str]:
        normalized_id = str(miner_id)
        normalized_endpoint = endpoint.lstrip("/")
        if self.allowed_miner_ids is not None and normalized_id not in self.allowed_miner_ids:
            raise PaymentPolicyError("Miner id is not in the approved payment target set")
        if self.allowed_endpoints is not None and normalized_endpoint not in self.allowed_endpoints:
            raise PaymentPolicyError("Miner endpoint is not in the approved payment target set")
        return normalized_id, normalized_endpoint

    def _validate_signing_policy(self, request_url: str) -> None:
        if self.signer is None:
            return
        if self.expected_pay_to is None:
            raise PaymentPolicyError("expected_pay_to is required before signing")
        if self.allowed_miner_ids is None or self.allowed_endpoints is None:
            raise PaymentPolicyError("approved Miner ids and endpoints are required before signing")
        if not self.allow_insecure_transport and not self.dispatcher_url.lower().startswith("https://"):
            raise PaymentPolicyError("refusing to send payment authorization over non-HTTPS transport")
        if self.journal is None:
            raise PaymentPolicyError("a durable payment journal is required before signing")
        self.journal.assert_available(
            request_url,
            max_paid_requests=self.max_paid_requests,
            max_total_payment_micro_usdc=self.max_total_payment_micro_usdc,
        )

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query = f"?{urlencode(params)}" if params else ""
        return f"{self.dispatcher_url}/{path.lstrip('/')}" + query

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> PaymentResponse:
        url = self._build_url(path, params)
        headers = {"Accept": "application/json", **(request_headers or {})}
        self._validate_signing_policy(url)
        first = self.transport("GET", url, headers)
        if first.status != 402:
            return self._response_or_error(
                first,
                request_url=url,
                received_at=_timestamp(self.clock()),
            )

        encoded_challenge = next(
            (value for key, value in first.headers.items() if key.lower() == "payment-required"),
            None,
        )
        if not encoded_challenge:
            raise RuntimeError("HTTP 402 response did not include Payment-Required")
        challenge = PaymentChallenge.decode(encoded_challenge)
        if self.signer is None:
            raise PaymentRequiredError(challenge)
        option = challenge.validate_for_request(
            url,
            expected_pay_to=self.expected_pay_to or "",
            max_amount_micro_usdc=self.max_payment_micro_usdc,
            now=self.clock(),
            require_deadline=self.require_challenge_deadline,
        )
        authorization = ValidatedPaymentAuthorization.from_challenge(challenge, url, option)
        assert self.journal is not None
        self.journal.reserve(
            authorization,
            max_paid_requests=self.max_paid_requests,
            max_total_payment_micro_usdc=self.max_total_payment_micro_usdc,
            now=self.clock(),
        )
        try:
            proof = self.signer(authorization)
            if not isinstance(proof, str) or not proof:
                raise ValueError("payment signer returned an empty proof")
        except Exception as exc:
            self.journal.abort_reservation(url, str(exc), now=self.clock())
            raise
        self.journal.mark_submitted(authorization, now=self.clock())
        try:
            paid = self.transport(
                "GET",
                url,
                {**headers, "PAYMENT-SIGNATURE": proof},
            )
            response = self._response_or_error(
                paid,
                request_url=url,
                received_at=_timestamp(self.clock()),
                payment_attempt_id=authorization.authorization_sha256,
                challenge_sha256=authorization.challenge_sha256,
                challenge_deadline=challenge.validate_deadline(now=self.clock()),
            )
        except Exception as exc:
            self.journal.mark_unknown(url, str(exc), now=self.clock())
            raise PaymentOutcomeUnknown(url, f"payment outcome unknown: {exc}") from exc
        if response.settlement_proof is None:
            self.journal.mark_unknown(
                url,
                "successful response had no settlement header",
                now=self.clock(),
            )
            raise PaymentOutcomeUnknown(
                url,
                "payment outcome unknown: successful response had no settlement header",
            )
        verification = "unverified"
        if self.settlement_verifier is not None:
            try:
                verification = (
                    "verified"
                    if self.settlement_verifier(response, authorization)
                    else "invalid"
                )
            except Exception as exc:
                verification = "unknown"
                self.journal.mark_unknown(
                    url,
                    f"settlement verification failed: {exc}",
                    now=self.clock(),
                )
                raise PaymentOutcomeUnknown(
                    url,
                    f"payment outcome unknown: settlement verification failed: {exc}",
                ) from exc
        if verification != "verified":
            self.journal.mark_unknown(
                url,
                "settlement header was present but was not independently verified",
                now=self.clock(),
            )
            raise PaymentOutcomeUnknown(
                url,
                "payment outcome unknown: settlement header was not independently verified",
            )
        response = replace(
            response,
            settlement_verified=True,
            settlement_verification="verified",
        )
        try:
            self.journal.mark_settled(
                url,
                response.settlement_artifact_sha256,
                now=self.clock(),
            )
        except Exception as exc:
            raise PaymentOutcomeUnknown(
                url,
                f"payment outcome unknown: settlement evidence could not be journaled: {exc}",
            ) from exc
        return response

    def preflight_miner(
        self,
        miner_id: str | int,
        endpoint: str,
        params: dict[str, Any],
    ) -> PaymentPreflight:
        """Make only the unpaid request and return its challenge for review."""

        normalized_id, normalized_endpoint = self._validate_target(miner_id, endpoint)
        url = self._build_url(f"v1/{normalized_id}/{normalized_endpoint}", params)
        first = self.transport("GET", url, {"Accept": "application/json"})
        if first.status != 402:
            return PaymentPreflight(
                request_url=url,
                status=first.status,
                challenge=None,
                response=self._response_or_error(
                    first,
                    request_url=url,
                    received_at=_timestamp(self.clock()),
                ),
            )
        encoded_challenge = next(
            (value for key, value in first.headers.items() if key.lower() == "payment-required"),
            None,
        )
        if not encoded_challenge:
            raise RuntimeError("HTTP 402 response did not include Payment-Required")
        challenge = PaymentChallenge.decode(encoded_challenge)
        if self.expected_pay_to is not None:
            challenge.validate_for_request(
                url,
                expected_pay_to=self.expected_pay_to,
                max_amount_micro_usdc=self.max_payment_micro_usdc,
                now=self.clock(),
                require_deadline=self.require_challenge_deadline,
            )
        return PaymentPreflight(
            request_url=url,
            status=first.status,
            challenge=challenge,
            response=None,
        )

    @staticmethod
    def _response_or_error(
        response: HttpResult,
        *,
        request_url: str | None = None,
        received_at: str | None = None,
        payment_attempt_id: str | None = None,
        challenge_sha256: str | None = None,
        challenge_deadline: str | None = None,
    ) -> PaymentResponse:
        if not 200 <= response.status < 300:
            detail = response.body.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Telegraph request failed with HTTP {response.status}: {detail}")
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError):
            body = response.body.decode("utf-8", errors="replace")
        return PaymentResponse(
            response.status,
            body,
            response.headers,
            request_url=request_url,
            received_at=received_at,
            payment_attempt_id=payment_attempt_id,
            challenge_sha256=challenge_sha256,
            challenge_deadline=challenge_deadline,
            settlement_verification=(
                "unverified" if any(key.lower() == "x-payment-settle-response" for key in response.headers)
                else "not_attempted"
            ),
        )

    def discover_integrations(self) -> Any:
        url = self._build_url("integrations")
        response = self.transport("GET", url, {"Accept": "application/json"})
        return self._response_or_error(response).body

    def request_miner(
        self,
        miner_id: str | int,
        endpoint: str,
        params: dict[str, Any],
        *,
        request_headers: dict[str, str] | None = None,
    ) -> PaymentResponse:
        normalized_id, normalized_endpoint = self._validate_target(miner_id, endpoint)
        return self._request(
            f"v1/{normalized_id}/{normalized_endpoint}",
            params,
            request_headers=request_headers,
        )
