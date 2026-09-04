"""Durable Application case lifecycle for OathCast evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
import os
import sqlite3
import threading
from typing import Any, Mapping

from oathcast.application import ApplicationDecision
from oathcast.forecast import ForecastQuestion, format_timestamp
from oathcast.ground_truth import GroundTruthResult, PrecipitationObservation


UTC = timezone.utc


class CaseConflict(RuntimeError):
    """Raised when an event id is reused with different evidence."""


class CaseStateError(RuntimeError):
    """Raised when a case lifecycle transition is invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reply_id(event_id: str, reply: dict[str, Any]) -> str:
    """Derive one stable evidence id for one Application request/reply pair."""

    return _sha256_json(
        {
            "event_id": event_id,
            "miner_id": reply.get("miner_id"),
            "request_id": reply.get("request_id"),
            "raw_response": reply.get("raw_response"),
            "parser_version": reply.get("parser_version", "probability_extractor_v1"),
        }
    )


def _protocol_projection_fields(reply: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return the receipt JSON, exact paid-body hash, and request id to persist."""

    request_id = reply.get("request_id")
    request_id = request_id if isinstance(request_id, str) and request_id else None
    protocol = reply.get("protocol_result")
    if not isinstance(protocol, Mapping):
        return request_id, None, None
    receipt = protocol.get("receipt")
    if not isinstance(receipt, Mapping):
        return request_id, None, None
    response_body_sha256 = receipt.get("response_body_sha256")
    response_body_sha256 = (
        response_body_sha256
        if isinstance(response_body_sha256, str) and response_body_sha256
        else None
    )
    return request_id, _canonical_json(receipt), response_body_sha256


def _timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(tz=UTC)
    return format_timestamp(current)


class SqliteCaseStore:
    """SQLite case store with idempotent transitions and question conflicts."""

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
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS application_cases (
                    event_id TEXT PRIMARY KEY,
                    question_json TEXT NOT NULL,
                    question_sha256 TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision_json TEXT,
                    decision_sealed_at TEXT,
                    frozen_at TEXT,
                    ground_truth_json TEXT,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS application_request_bindings (
                    principal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (principal_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS miner_replies (
                    reply_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    miner_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    owned INTEGER NOT NULL,
                    raw_response_json TEXT NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    received_at TEXT,
                    latency_ms REAL,
                    probability_x10000 INTEGER,
                    parser_version TEXT NOT NULL,
                    validity_reason TEXT,
                    request_id TEXT,
                    protocol_receipt_json TEXT,
                    response_body_sha256 TEXT,
                    FOREIGN KEY (event_id) REFERENCES application_cases(event_id)
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    reply_ids_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    decision_sha256 TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES application_cases(event_id)
                );

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    observation_sha256 TEXT NOT NULL,
                    source TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    precipitation_micrometres INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES application_cases(event_id)
                );

                CREATE TABLE IF NOT EXISTS resolutions (
                    resolution_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    resolution_json TEXT NOT NULL,
                    resolution_sha256 TEXT NOT NULL,
                    observation_id TEXT,
                    resolver_version TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES application_cases(event_id)
                );
                """
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(miner_replies)").fetchall()
            }
            migrations = {
                "request_id": "ALTER TABLE miner_replies ADD COLUMN request_id TEXT",
                "protocol_receipt_json": (
                    "ALTER TABLE miner_replies ADD COLUMN protocol_receipt_json TEXT"
                ),
                "response_body_sha256": (
                    "ALTER TABLE miner_replies ADD COLUMN response_body_sha256 TEXT"
                ),
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    connection.execute(statement)
            connection.commit()
        finally:
            if self._memory_connection is None:
                connection.close()

    def _connection(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            connection = self._memory_connection
            close_on_error = False
        else:
            connection = sqlite3.connect(self.path, timeout=10)
            close_on_error = True
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            enabled = connection.execute("PRAGMA foreign_keys").fetchone()
            if enabled is None or enabled[0] != 1:
                raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
            return connection
        except Exception:
            if close_on_error:
                connection.close()
            raise

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

    def _get_row(self, event_id: str) -> sqlite3.Row | None:
        connection = self._connection()
        try:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM application_cases WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            if self._memory_connection is None:
                connection.close()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        record = dict(row)
        for field in ("question_json", "decision_json", "ground_truth_json"):
            if record[field] is not None:
                record[field[:-5] if field.endswith("_json") else field] = json.loads(record[field])
            del record[field]
        return record

    def _children(self, event_id: str) -> dict[str, list[dict[str, Any]]]:
        connection = self._connection()
        try:
            connection.row_factory = sqlite3.Row
            replies = [dict(item) for item in connection.execute(
                "SELECT * FROM miner_replies WHERE event_id = ? ORDER BY received_at, reply_id",
                (event_id,),
            ).fetchall()]
            for reply in replies:
                reply["raw_response"] = json.loads(reply.pop("raw_response_json"))
                protocol_receipt_json = reply.pop("protocol_receipt_json", None)
                if protocol_receipt_json is not None:
                    reply["protocol_receipt"] = json.loads(protocol_receipt_json)
            decisions = [dict(item) for item in connection.execute(
                "SELECT * FROM decisions WHERE event_id = ? ORDER BY decided_at, decision_id",
                (event_id,),
            ).fetchall()]
            for decision in decisions:
                decision["decision"] = json.loads(decision.pop("decision_json"))
                decision["reply_ids"] = json.loads(decision.pop("reply_ids_json"))
                decision["policy"] = json.loads(decision.pop("policy_json"))
            observations = [dict(item) for item in connection.execute(
                "SELECT * FROM observations WHERE event_id = ? ORDER BY observed_at, observation_id",
                (event_id,),
            ).fetchall()]
            for observation in observations:
                observation["observation"] = json.loads(observation.pop("observation_json"))
            resolutions = [dict(item) for item in connection.execute(
                "SELECT * FROM resolutions WHERE event_id = ? ORDER BY resolved_at, resolution_id",
                (event_id,),
            ).fetchall()]
            for resolution in resolutions:
                resolution["resolution"] = json.loads(resolution.pop("resolution_json"))
            return {
                "miner_replies": replies,
                "decisions": decisions,
                "observations": observations,
                "resolutions": resolutions,
            }
        finally:
            if self._memory_connection is None:
                connection.close()

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._record(self._get_row(event_id))
            if record is None:
                return None
            record.update(self._children(event_id))
            return record

    def create(self, question: ForecastQuestion, *, created_at: datetime | None = None) -> dict[str, Any]:
        question_json = _canonical_json(question.to_dict())
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO application_cases
                        (event_id, question_json, question_sha256, contract_version, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        question.event_id,
                        question_json,
                        _sha256_json(question.to_dict()),
                        "forecast_contract_v1",
                        _timestamp(created_at),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM application_cases WHERE event_id = ?",
                    (question.event_id,),
                ).fetchone()
                if row is None:
                    raise CaseStateError("case could not be created")
                if row["question_json"] != question_json:
                    raise CaseConflict(
                        f"event_id {question.event_id!r} is already bound to a different question"
                    )
                connection.commit()
                return self.get(question.event_id) or {}
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def bind_application_request(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        request_sha256: str,
        event_id: str,
        created_at: datetime | None = None,
    ) -> None:
        """Bind an authenticated Application idempotency key to one request."""

        values = (principal_id, idempotency_key, request_sha256, event_id)
        with self._lock:
            connection = self._connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO application_request_bindings (
                        principal_id, idempotency_key, request_sha256, event_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (*values, _timestamp(created_at)),
                )
                existing = connection.execute(
                    """
                    SELECT request_sha256, event_id
                    FROM application_request_bindings
                    WHERE principal_id = ? AND idempotency_key = ?
                    """,
                    (principal_id, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise CaseStateError("Application idempotency binding could not be stored")
                if existing[0] != request_sha256 or existing[1] != event_id:
                    raise CaseConflict(
                        "Application idempotency key is bound to a different request"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def record_reply(self, event_id: str, reply: dict[str, Any]) -> None:
        """Project one received reply before a decision is sealed.

        The projector is deliberately idempotent: a process can persist a
        response, crash before sealing the decision, and safely replay the
        same Application request later without duplicating or rewriting the
        evidence row.
        """

        if not isinstance(reply, dict):
            raise CaseConflict("projected Miner reply must be an object")
        raw_response = reply.get("raw_response")
        raw_response_json = _canonical_json(raw_response)
        received_at = reply.get("received_at")
        reply_id = _reply_id(event_id, reply)
        request_id, protocol_receipt_json, response_body_sha256 = _protocol_projection_fields(reply)
        probability = reply.get("probability")
        probability_x10000 = (
            None if probability is None else int(round(float(probability) * 10000))
        )
        with self._lock:
            connection = self._connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                case = connection.execute(
                    "SELECT 1 FROM application_cases WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if case is None:
                    raise CaseStateError("case must be created before projecting a reply")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO miner_replies (
                        reply_id, event_id, miner_id, slug, owned,
                        raw_response_json, raw_response_sha256, received_at,
                        latency_ms, probability_x10000, parser_version,
                        validity_reason, request_id, protocol_receipt_json,
                        response_body_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reply_id,
                        event_id,
                        str(reply.get("miner_id", "")),
                        str(reply.get("slug", "")),
                        int(bool(reply.get("owned"))),
                        raw_response_json,
                        _sha256_json(raw_response),
                        received_at,
                        reply.get("latency_ms"),
                        probability_x10000,
                        str(reply.get("parser_version", "probability_extractor_v1")),
                        reply.get("validity_reason") or reply.get("error"),
                        request_id,
                        protocol_receipt_json,
                        response_body_sha256,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def seal_decision(
        self,
        event_id: str,
        decision: ApplicationDecision | dict[str, Any],
        *,
        sealed_at: datetime | None = None,
    ) -> dict[str, Any]:
        payload = decision.to_dict() if isinstance(decision, ApplicationDecision) else decision
        decision_json = _canonical_json(payload)
        if payload.get("event_id") != event_id:
            raise CaseConflict("decision event_id does not match the case")
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM application_cases WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    raise CaseStateError("case must be created before sealing a decision")
                if row["decision_json"] is not None:
                    if row["decision_json"] != decision_json:
                        raise CaseConflict("case already has a different sealed decision")
                else:
                    replies = payload.get("replies", [])
                    if not isinstance(replies, list):
                        raise CaseConflict("decision replies must be a list")
                    reply_ids: list[str] = []
                    for reply in replies:
                        if not isinstance(reply, dict):
                            raise CaseConflict("decision contains a malformed Miner reply")
                        raw_response_json = _canonical_json(reply.get("raw_response"))
                        received_at = reply.get("received_at") or payload.get("decided_at")
                        reply_id = _reply_id(event_id, reply)
                        request_id, protocol_receipt_json, response_body_sha256 = (
                            _protocol_projection_fields(reply)
                        )
                        reply_ids.append(reply_id)
                        probability = reply.get("probability")
                        probability_x10000 = (
                            None
                            if probability is None
                            else int(round(float(probability) * 10000))
                        )
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO miner_replies (
                                reply_id, event_id, miner_id, slug, owned,
                                raw_response_json, raw_response_sha256, received_at,
                                latency_ms, probability_x10000, parser_version,
                                validity_reason, request_id, protocol_receipt_json,
                                response_body_sha256
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                reply_id,
                                event_id,
                                str(reply.get("miner_id", "")),
                                str(reply.get("slug", "")),
                                int(bool(reply.get("owned"))),
                                raw_response_json,
                                _sha256_json(reply.get("raw_response")),
                                received_at,
                                reply.get("latency_ms"),
                                probability_x10000,
                                str(reply.get("parser_version", "probability_extractor_v1")),
                                reply.get("validity_reason") or reply.get("error"),
                                request_id,
                                protocol_receipt_json,
                                response_body_sha256,
                            ),
                        )
                    decided_at = str(payload["decided_at"])
                    decision_threshold = float(payload.get("decision_threshold", 0.5))
                    if not 0 <= decision_threshold <= 1:
                        raise CaseConflict("decision threshold must be between 0 and 1")
                    policy = {
                        "policy_version": "cross_miner_weighted_v2",
                        "decision_threshold_x10000": int(round(decision_threshold * 10000)),
                        "aggregate_probability_x10000": int(
                            round(float(payload["aggregate_probability"]) * 10000)
                        ),
                        "used_external_miner": bool(payload["used_external_miner"]),
                        "external_influence": bool(payload["external_influence"]),
                    }
                    decision_id = _sha256_json(
                        {"event_id": event_id, "decision": payload, "reply_ids": reply_ids}
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO decisions (
                            decision_id, event_id, decision_json, reply_ids_json,
                            policy_json, decision_sha256, decided_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            decision_id,
                            event_id,
                            decision_json,
                            _canonical_json(reply_ids),
                            _canonical_json(policy),
                            _sha256_json(payload),
                            decided_at,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE application_cases
                        SET decision_json = ?, decision_sealed_at = ?, frozen_at = ?
                        WHERE event_id = ? AND decision_json IS NULL
                        """,
                        (decision_json, _timestamp(sealed_at), _timestamp(sealed_at), event_id),
                    )
                connection.commit()
                return self.get(event_id) or {}
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def _insert_observation_in_transaction(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        observation: PrecipitationObservation,
    ) -> None:
        if observation.event_id != event_id:
            raise CaseConflict("observation event_id does not match the case")
        observation_json = _canonical_json(observation.to_dict())
        existing = connection.execute(
            "SELECT observation_json FROM observations WHERE observation_id = ?",
            (observation.observation_id,),
        ).fetchone()
        if existing is not None and existing[0] != observation_json:
            raise CaseConflict("observation_id is already bound to different evidence")
        connection.execute(
            """
            INSERT OR IGNORE INTO observations (
                observation_id, event_id, observation_json, observation_sha256,
                source, window_start, window_end, precipitation_micrometres, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                event_id,
                observation_json,
                _sha256_json(observation.to_dict()),
                observation.source,
                format_timestamp(observation.window_start),
                format_timestamp(observation.window_end),
                observation.precipitation_micrometres,
                format_timestamp(observation.observed_at),
            ),
        )

    def record_observation(
        self,
        event_id: str,
        observation: PrecipitationObservation,
    ) -> dict[str, Any]:
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM application_cases WHERE event_id = ?", (event_id,)
                ).fetchone() is None:
                    raise CaseStateError("case must be created before recording an observation")
                self._insert_observation_in_transaction(connection, event_id, observation)
                connection.commit()
                return self.get(event_id) or {}
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()

    def resolve(
        self,
        event_id: str,
        result: GroundTruthResult | dict[str, Any],
        *,
        observation: PrecipitationObservation | None = None,
        resolver_version: str = "strict_precipitation_v1",
    ) -> dict[str, Any]:
        payload = result.to_dict() if isinstance(result, GroundTruthResult) else result
        ground_truth_json = _canonical_json(payload)
        if payload.get("event_id") != event_id:
            raise CaseConflict("ground-truth event_id does not match the case")
        with self._lock:
            connection = self._connection()
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM application_cases WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    raise CaseStateError("case must be created before resolution")
                if row["decision_json"] is None:
                    raise CaseStateError("decision must be sealed before resolution")
                if payload.get("status") == "resolved" and observation is None:
                    raise CaseStateError(
                        "a resolved ground truth must retain its source observation"
                    )
                if observation is not None:
                    if payload.get("observation_id") != observation.observation_id:
                        raise CaseConflict(
                            "ground-truth observation_id does not match retained observation"
                        )
                    self._insert_observation_in_transaction(connection, event_id, observation)
                if row["ground_truth_json"] is None:
                    connection.execute(
                        """
                        UPDATE application_cases
                        SET ground_truth_json = ?, resolved_at = ?
                        WHERE event_id = ? AND ground_truth_json IS NULL
                        """,
                        (ground_truth_json, payload["resolved_at"], event_id),
                    )
                # The first resolution remains canonical on the case row. Later
                # resolutions, including divergent ones, are retained below as
                # append-only history for audit without rewriting that snapshot.
                resolution_id = _sha256_json(
                    {
                        "event_id": event_id,
                        "resolution": payload,
                        "observation_id": payload.get("observation_id"),
                        "resolver_version": resolver_version,
                    }
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO resolutions (
                        resolution_id, event_id, resolution_json, resolution_sha256,
                        observation_id, resolver_version, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolution_id,
                        event_id,
                        ground_truth_json,
                        _sha256_json(payload),
                        payload.get("observation_id"),
                        resolver_version,
                        payload["resolved_at"],
                    ),
                )
                connection.commit()
                return self.get(event_id) or {}
            except Exception:
                connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()
