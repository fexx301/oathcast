"""Append-only local provenance for Application demand observations.

This ledger is an engineering and audit aid.  It deliberately cannot prove
Telegraph's official request count: only Telegraph's own node/Explorer can do
that.  In particular, local fixtures, direct upstream calls, unpaid
preflights, and responses without settlement evidence are never marked as
local candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Iterable

from oathcast.forecast import format_timestamp


UTC = timezone.utc
TRANSPORTS = frozenset({"telegraph", "direct_http", "fixture"})
PAYMENT_METHODS = frozenset({"x402", "other", "none", "unknown"})
PAYMENT_STATUSES = frozenset(
    {"settled", "paid", "paid_unverified", "required", "unpaid", "unknown", "failed"}
)
PAYMENT_EVIDENCE = frozenset(
    {
        "x402_settlement",
        "supported_method_receipt",
        "challenge_only",
        "x402_header_unverified",
        "none",
    }
)
SETTLEMENT_VERIFICATION_STATES = frozenset(
    {"not_attempted", "unverified", "verified", "invalid", "unknown"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: datetime | None = None) -> str:
    current = (value or datetime.now(tz=UTC)).astimezone(UTC)
    return format_timestamp(current)


@dataclass(frozen=True)
class DemandEvent:
    """One immutable observation about an Application-to-Miner request."""

    demand_id: str
    question_event_id: str | None
    application_request_id: str | None
    miner_id: str
    endpoint: str
    transport: str
    routed_through_telegraph: bool
    payment_method: str
    payment_status: str
    payment_evidence: str
    http_status: int | None
    is_fixture: bool
    source: str
    occurred_at: str
    local_candidate: bool = False
    payment_attempt_id: str | None = None
    settlement_artifact_sha256: str | None = None
    settlement_verification: str = "not_attempted"
    protocol_receipt_sha256: str | None = None

    @classmethod
    def create(
        cls,
        *,
        question_event_id: str | None,
        application_request_id: str | None,
        miner_id: str | int,
        endpoint: str,
        transport: str,
        routed_through_telegraph: bool,
        payment_method: str,
        payment_status: str,
        payment_evidence: str,
        http_status: int | None,
        is_fixture: bool,
        source: str,
        occurred_at: datetime | None = None,
        payment_attempt_id: str | None = None,
        settlement_artifact_sha256: str | None = None,
        settlement_verification: str = "not_attempted",
        protocol_receipt_sha256: str | None = None,
    ) -> "DemandEvent":
        if transport not in TRANSPORTS:
            raise ValueError(f"unsupported demand transport: {transport}")
        if payment_method not in PAYMENT_METHODS:
            raise ValueError(f"unsupported payment method: {payment_method}")
        if payment_status not in PAYMENT_STATUSES:
            raise ValueError(f"unsupported payment status: {payment_status}")
        if payment_evidence not in PAYMENT_EVIDENCE:
            raise ValueError(f"unsupported payment evidence: {payment_evidence}")
        if settlement_verification not in SETTLEMENT_VERIFICATION_STATES:
            raise ValueError(
                "unsupported settlement verification: "
                f"{settlement_verification}"
            )
        if not str(miner_id).strip():
            raise ValueError("miner_id is required")
        if not endpoint.strip():
            raise ValueError("endpoint is required")
        if http_status is not None and not 100 <= int(http_status) <= 599:
            raise ValueError("http_status must be an HTTP status code")
        if routed_through_telegraph and transport != "telegraph":
            raise ValueError("Telegraph-routed demand must use the telegraph transport")
        if is_fixture and source != "development-fixture":
            raise ValueError("fixture events must identify their development source")

        event = cls(
            demand_id=f"demand-{uuid.uuid4().hex}",
            question_event_id=question_event_id,
            application_request_id=application_request_id,
            miner_id=str(miner_id),
            endpoint=endpoint.lstrip("/"),
            transport=transport,
            routed_through_telegraph=bool(routed_through_telegraph),
            payment_method=payment_method,
            payment_status=payment_status,
            payment_evidence=payment_evidence,
            http_status=None if http_status is None else int(http_status),
            is_fixture=bool(is_fixture),
            source=source,
            occurred_at=_timestamp(occurred_at),
            payment_attempt_id=payment_attempt_id,
            settlement_artifact_sha256=settlement_artifact_sha256,
            settlement_verification=settlement_verification,
            protocol_receipt_sha256=protocol_receipt_sha256,
        )
        return replace(event, local_candidate=event._is_local_candidate())

    def _is_local_candidate(self) -> bool:
        """Conservative local predicate; this is not an official count."""

        return (
            self.transport == "telegraph"
            and self.routed_through_telegraph
            and self.source == "application"
            and not self.is_fixture
            and bool(self.application_request_id)
            and self.payment_status in {"settled", "paid"}
            and self.payment_evidence in {"x402_settlement", "supported_method_receipt"}
            and self.settlement_verification == "verified"
            and self.http_status is not None
            and 200 <= self.http_status < 300
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "demand_id": self.demand_id,
            "question_event_id": self.question_event_id,
            "application_request_id": self.application_request_id,
            "miner_id": self.miner_id,
            "endpoint": self.endpoint,
            "transport": self.transport,
            "routed_through_telegraph": self.routed_through_telegraph,
            "payment_method": self.payment_method,
            "payment_status": self.payment_status,
            "payment_evidence": self.payment_evidence,
            "http_status": self.http_status,
            "is_fixture": self.is_fixture,
            "source": self.source,
            "occurred_at": self.occurred_at,
            "local_candidate": self.local_candidate,
            "payment_attempt_id": self.payment_attempt_id,
            "settlement_artifact_sha256": self.settlement_artifact_sha256,
            "settlement_verification": self.settlement_verification,
            "protocol_receipt_sha256": self.protocol_receipt_sha256,
        }


class DemandLedger:
    """Durable, append-only local request provenance store."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS demand_events (
                    demand_id TEXT PRIMARY KEY,
                    question_event_id TEXT,
                    application_request_id TEXT,
                    miner_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    routed_through_telegraph INTEGER NOT NULL,
                    payment_method TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    payment_evidence TEXT NOT NULL,
                    http_status INTEGER,
                    is_fixture INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    local_candidate INTEGER NOT NULL,
                    payment_attempt_id TEXT,
                    settlement_artifact_sha256 TEXT,
                    settlement_verification TEXT NOT NULL DEFAULT 'not_attempted',
                    protocol_receipt_sha256 TEXT,
                    event_json TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(demand_events)").fetchall()
            }
            migrations = {
                "payment_attempt_id": "ALTER TABLE demand_events ADD COLUMN payment_attempt_id TEXT",
                "settlement_artifact_sha256": "ALTER TABLE demand_events ADD COLUMN settlement_artifact_sha256 TEXT",
                "settlement_verification": (
                    "ALTER TABLE demand_events ADD COLUMN settlement_verification "
                    "TEXT NOT NULL DEFAULT 'not_attempted'"
                ),
                "protocol_receipt_sha256": "ALTER TABLE demand_events ADD COLUMN protocol_receipt_sha256 TEXT",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    connection.execute(statement)
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS demand_events_no_update
                BEFORE UPDATE ON demand_events
                BEGIN
                    SELECT RAISE(ABORT, 'demand events are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS demand_events_no_delete
                BEFORE DELETE ON demand_events
                BEGIN
                    SELECT RAISE(ABORT, 'demand events are immutable');
                END;
                """
            )
            connection.commit()
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

    def append(self, event: DemandEvent) -> DemandEvent:
        payload = event.to_dict()
        event_json = _canonical_json(payload)
        event_sha256 = _sha256_json(payload)
        with self._lock:
            connection = self._connection()
            try:
                existing = connection.execute(
                    "SELECT event_json FROM demand_events WHERE demand_id = ?",
                    (event.demand_id,),
                ).fetchone()
                if existing is not None and existing[0] != event_json:
                    raise ValueError("demand_id is already bound to different evidence")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO demand_events (
                        demand_id, question_event_id, application_request_id, miner_id,
                        endpoint, transport, routed_through_telegraph, payment_method,
                        payment_status, payment_evidence, http_status, is_fixture,
                        source, occurred_at, local_candidate, payment_attempt_id,
                        settlement_artifact_sha256, settlement_verification,
                        protocol_receipt_sha256, event_json, event_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.demand_id,
                        event.question_event_id,
                        event.application_request_id,
                        event.miner_id,
                        event.endpoint,
                        event.transport,
                        int(event.routed_through_telegraph),
                        event.payment_method,
                        event.payment_status,
                        event.payment_evidence,
                        event.http_status,
                        int(event.is_fixture),
                        event.source,
                        event.occurred_at,
                        int(event.local_candidate),
                        event.payment_attempt_id,
                        event.settlement_artifact_sha256,
                        event.settlement_verification,
                        event.protocol_receipt_sha256,
                        event_json,
                        event_sha256,
                    ),
                )
                connection.commit()
                return event
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def record(self, **kwargs: Any) -> DemandEvent:
        return self.append(DemandEvent.create(**kwargs))

    def list_events(self) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connection()
            try:
                rows = connection.execute(
                    "SELECT event_json, event_sha256 FROM demand_events ORDER BY occurred_at, demand_id"
                ).fetchall()
                return [
                    {**json.loads(row[0]), "event_sha256": row[1]}
                    for row in rows
                ]
            finally:
                if self._memory_connection is None:
                    connection.close()

    def integrity_check(self) -> str:
        """Verify SQLite integrity and every stored event hash."""

        with self._lock:
            connection = self._connection()
            try:
                result_row = connection.execute("PRAGMA integrity_check").fetchone()
                result = str(result_row[0]) if result_row is not None else ""
                if result != "ok":
                    raise RuntimeError(f"demand database integrity check failed: {result}")
                rows = connection.execute(
                    "SELECT event_json, event_sha256 FROM demand_events"
                ).fetchall()
                for event_json, event_sha256 in rows:
                    if _sha256_json(json.loads(event_json)) != event_sha256:
                        raise RuntimeError("demand event hash verification failed")
                return result
            finally:
                if self._memory_connection is None:
                    connection.close()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            connection = self._connection()
            try:
                total = int(connection.execute("SELECT COUNT(*) FROM demand_events").fetchone()[0])
                candidates = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM demand_events WHERE local_candidate = 1"
                    ).fetchone()[0]
                )
                return {
                    "total_events": total,
                    "local_candidate_events": candidates,
                    "official_telegraph_count": None,
                    "warning": (
                        "Local provenance is not official Telegraph demand evidence; "
                        "verify served requests in Telegraph's node or Explorer."
                    ),
                }
            finally:
                if self._memory_connection is None:
                    connection.close()


def iter_local_candidates(events: Iterable[DemandEvent]) -> Iterable[DemandEvent]:
    """Yield conservative local candidates without calling them official."""

    return (event for event in events if event.local_candidate)
