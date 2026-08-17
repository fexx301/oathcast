"""Durable, immutable forecast receipts for the Miner service.

Receipts are intentionally small and self-contained. They freeze the exact
question, normalized forecast, public response, and raw-payload provenance
needed to replay a forecast after its cutoff without asking an upstream
provider for a new answer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
import uuid


class ReceiptConflict(RuntimeError):
    """Raised when an event id is reused for a different forecast question."""


class ReceiptStoreFull(RuntimeError):
    """Raised when a *new* receipt would exceed the store's capacity cap.

    Deliberately never raised for a replay of an existing receipt: see
    ``SqliteReceiptStore.save``.
    """


class ReceiptTampering(RuntimeError):
    """Raised when stored receipt bytes do not match their recorded digest."""


def receipt_digest(receipt: dict[str, Any]) -> str:
    """Hash canonical receipt bytes, excluding the digest field itself."""

    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Domain separator for the receipt hash chain. Bumping this invalidates every
# previously published anchor, so it changes only if the chain construction
# itself changes.
RECEIPT_CHAIN_DOMAIN = "oathcast-receipt-chain-v1"


# `event_id` is caller-controlled and every receipt stores the full raw
# provider payload, so an authenticated client can grow the disk unbounded.
# These caps are a safety valve, not a security boundary. The row cap is the
# binding one in practice; the byte cap catches unusually large payloads.
DEFAULT_MAX_RECEIPT_ROWS = 200_000
DEFAULT_MAX_RECEIPT_BYTES = 512 * 1024 * 1024


class SqliteReceiptStore:
    """A small SQLite-backed append-only receipt store.

    The database schema also has SQLite triggers that reject updates and
    deletes. A repeated event id with the same question is idempotent; a
    repeated event id with different question data is a conflict.

    Capacity is bounded by refusing new writes rather than by deleting old
    receipts. Eviction would contradict the immutability the receipts exist to
    provide — and the DELETE trigger would reject it anyway.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_rows: int | None = DEFAULT_MAX_RECEIPT_ROWS,
        max_bytes: int | None = DEFAULT_MAX_RECEIPT_BYTES,
    ) -> None:
        if max_rows is not None and max_rows <= 0:
            raise ValueError("max_rows must be positive or None")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive or None")
        self.path = str(path)
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
            # Every read in this class addresses columns by name, so the
            # in-memory connection needs the same row factory as a file-backed
            # one. _connect() returns this cached connection untouched, so it
            # has to be set here or `row["..."]` raises TypeError.
            self._memory_connection.row_factory = sqlite3.Row
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS forecast_receipts (
                    event_id TEXT PRIMARY KEY,
                    question_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS forecast_receipts_no_update
                BEFORE UPDATE ON forecast_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'forecast receipts are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_receipts_no_delete
                BEFORE DELETE ON forecast_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'forecast receipts are immutable');
                END;
                """
            )
            # New writable stores get the probe table during initialization so
            # migrations/tests can attach constraints to it immediately. A
            # legacy database mounted read-only may not have this post-v5
            # table; defer that one migration to write_readiness(), where the
            # failure is reported through /readyz instead of aborting startup.
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS receipt_store_write_probe (
                        probe_token TEXT PRIMARY KEY,
                        touched INTEGER NOT NULL CHECK (touched IN (0, 1))
                    )
                    """
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
        finally:
            if self.path != ":memory:":
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

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

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT receipt_json FROM forecast_receipts WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            finally:
                if self._memory_connection is None:
                    connection.close()
        if row is None:
            return None
        receipt = json.loads(row["receipt_json"])
        if not isinstance(receipt, dict):
            raise RuntimeError("stored forecast receipt is not a JSON object")
        recorded = receipt.get("receipt_sha256")
        # A receipt with no digest field predates the field; refusing it would
        # turn a missing feature into data loss. A receipt that *has* one and
        # does not match it has been rewritten outside the app, so serving it
        # would hand out unverifiable evidence as if it were verified.
        if isinstance(recorded, str) and recorded != receipt_digest(receipt):
            raise ReceiptTampering(
                f"stored receipt for event_id {event_id!r} does not match its recorded digest"
            )
        return receipt

    def save(self, receipt: dict[str, Any]) -> dict[str, Any]:
        event_id = receipt.get("question", {}).get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("receipt question must contain a non-empty event_id")
        question_json = json.dumps(
            receipt["question"], sort_keys=True, separators=(",", ":")
        )
        receipt_json = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        created_at = receipt.get("created_at")
        if not isinstance(created_at, str) or not created_at:
            raise ValueError("receipt must contain created_at")
        candidate_bytes = sum(
            len(value.encode("utf-8"))
            for value in (event_id, question_json, receipt_json, created_at)
        )

        with self._lock:
            connection = self._connect()
            try:
                # The Python lock protects one store instance. BEGIN IMMEDIATE
                # serializes this read-check-write sequence with every other
                # process or store instance connected to the same database.
                connection.execute("BEGIN IMMEDIATE")
                # A replay of an existing receipt must always succeed, even at
                # capacity: the store is full of evidence precisely so it can
                # be re-read, and refusing a replay would break durable receipt
                # replay for events already committed to. Only genuinely new
                # rows are subject to the cap.
                row = connection.execute(
                    """
                    SELECT question_json, receipt_json
                    FROM forecast_receipts
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
                if row is None:
                    self._assert_capacity_for_new_receipt(
                        connection,
                        candidate_bytes=candidate_bytes,
                    )
                    connection.execute(
                        """
                        INSERT INTO forecast_receipts
                            (event_id, question_json, receipt_json, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (event_id, question_json, receipt_json, created_at),
                    )
                    if (
                        self.max_bytes is not None
                        and self.used_bytes(connection) > self.max_bytes
                    ):
                        raise ReceiptStoreFull(
                            f"receipt store would exceed its {self.max_bytes} byte cap; "
                            "existing receipts remain readable and replayable"
                        )
                    row = connection.execute(
                        """
                        SELECT question_json, receipt_json
                        FROM forecast_receipts
                        WHERE event_id = ?
                        """,
                        (event_id,),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("forecast receipt could not be persisted")
                if row["question_json"] != question_json:
                    raise ReceiptConflict(
                        f"event_id {event_id!r} is already bound to a different question"
                    )
                stored = json.loads(row["receipt_json"])
                if not isinstance(stored, dict):
                    raise RuntimeError("stored forecast receipt is not a JSON object")
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                if self._memory_connection is None:
                    connection.close()
        return stored

    def _assert_capacity_for_new_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_bytes: int,
    ) -> None:
        """Refuse a new receipt that would exceed a configured cap.

        Called inside an immediate write transaction and only for rows that do
        not already exist. The byte check includes the candidate's serialized
        columns, then ``save`` verifies SQLite's actual page count after insert.
        """

        if self.max_rows is not None:
            rows = connection.execute(
                "SELECT COUNT(*) AS count FROM forecast_receipts"
            ).fetchone()[0]
            if rows >= self.max_rows:
                raise ReceiptStoreFull(
                    f"receipt store has reached its {self.max_rows} row cap; "
                    "existing receipts remain readable and replayable"
                )
        if self.max_bytes is not None:
            used = self.used_bytes(connection)
            if used >= self.max_bytes or candidate_bytes > self.max_bytes - used:
                raise ReceiptStoreFull(
                    f"receipt store would exceed its {self.max_bytes} byte cap; "
                    "existing receipts remain readable and replayable"
                )

    def used_bytes(self, connection: sqlite3.Connection | None = None) -> int:
        """Return the database size in bytes.

        Uses SQLite's own page accounting rather than the file size so an
        in-memory store and a WAL-mode file both report meaningfully.
        """

        def _measure(active: sqlite3.Connection) -> int:
            page_count = active.execute("PRAGMA page_count").fetchone()[0]
            page_size = active.execute("PRAGMA page_size").fetchone()[0]
            return int(page_count) * int(page_size)

        if connection is not None:
            return _measure(connection)
        with self._lock:
            active = self._connect()
            try:
                return _measure(active)
            finally:
                if self._memory_connection is None:
                    active.close()

    def capacity(self) -> dict[str, Any]:
        """Return non-secret capacity telemetry for readiness and the canary."""

        with self._lock:
            connection = self._connect()
            try:
                rows = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM forecast_receipts"
                    ).fetchone()[0]
                )
                used = self.used_bytes(connection)
            finally:
                if self._memory_connection is None:
                    connection.close()
        report: dict[str, Any] = {
            "rows": rows,
            "max_rows": self.max_rows,
            "used_bytes": used,
            "max_bytes": self.max_bytes,
            "accepting_new_receipts": True,
        }
        if self.max_rows is not None:
            report["rows_remaining"] = max(0, self.max_rows - rows)
            if rows >= self.max_rows:
                report["accepting_new_receipts"] = False
        if self.max_bytes is not None:
            report["bytes_remaining"] = max(0, self.max_bytes - used)
            if used >= self.max_bytes:
                report["accepting_new_receipts"] = False
        return report

    def write_readiness(self) -> dict[str, Any]:
        """Verify that SQLite can write and roll back a real transaction.

        Opening the database, finding its tables, and even ``BEGIN IMMEDIATE``
        can all succeed on a connection whose first page write will fail. This
        probe therefore inserts and updates a row in a dedicated mutable table,
        verifies the result, and always rolls the transaction back. It never
        writes to the immutable receipt table and returns no path, SQL, or
        exception text that could expose deployment details.

        The result is intentionally uncached. Readiness callers can apply a
        cache policy appropriate to their request rate without weakening the
        store's write check or coupling it to HTTP concerns.
        """

        report: dict[str, Any] = {
            "ready": False,
            "probe": "sqlite_transactional_write",
            "rolled_back": False,
        }
        failure: str | None = None
        connection: sqlite3.Connection | None = None

        with self._lock:
            try:
                connection = self._connect()
                # This table is a post-v5 schema addition. Create it lazily so
                # an existing receipt database mounted read-only can still
                # start the service and report a failed write probe through
                # /readyz instead of crashing during store construction. A
                # genuinely new/uninitialized read-only database still fails
                # during the core receipt-schema initialization above.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS receipt_store_write_probe (
                        probe_token TEXT PRIMARY KEY,
                        touched INTEGER NOT NULL CHECK (touched IN (0, 1))
                    )
                    """
                )
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                probe_token = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO receipt_store_write_probe (probe_token, touched)
                    VALUES (?, 0)
                    """,
                    (probe_token,),
                )
                updated = connection.execute(
                    """
                    UPDATE receipt_store_write_probe
                    SET touched = 1
                    WHERE probe_token = ?
                    """,
                    (probe_token,),
                )
                row = connection.execute(
                    """
                    SELECT touched
                    FROM receipt_store_write_probe
                    WHERE probe_token = ?
                    """,
                    (probe_token,),
                ).fetchone()
                if updated.rowcount != 1 or row is None or int(row[0]) != 1:
                    failure = "write_verification_failed"
            except sqlite3.Error:
                failure = "write_unavailable"
            finally:
                if connection is not None:
                    try:
                        # rollback() is also safe when SQLite has already
                        # aborted the transaction after a write failure.
                        connection.rollback()
                        report["rolled_back"] = not connection.in_transaction
                    except sqlite3.Error:
                        failure = "rollback_failed"
                        report["rolled_back"] = False
                    finally:
                        if self._memory_connection is None:
                            connection.close()

        if failure is None and report["rolled_back"]:
            report["ready"] = True
        else:
            report["error"] = failure or "transaction_not_rolled_back"
        return report

    def integrity_check(self) -> str:
        """Run SQLite's full integrity check without exposing receipt data."""

        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                if self._memory_connection is None:
                    connection.close()
        result = str(row[0]) if row is not None else ""
        if result != "ok":
            raise RuntimeError(f"receipt database integrity check failed: {result}")
        return result

    def row_count(self) -> int:
        """Return the number of stored receipts without returning their contents."""

        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT COUNT(*) FROM forecast_receipts").fetchone()
            finally:
                if self._memory_connection is None:
                    connection.close()
        return int(row[0]) if row is not None else 0

    def chain_head(self, *, limit: int | None = None) -> dict[str, Any]:
        """Compute the receipt hash chain head, newest receipt last.

        ``chain[0] = sha256(domain)`` and
        ``chain[i] = sha256(chain[i-1] || event_id || recomputed_digest)``.

        Two properties make this worth publishing:

        * **Prefix commitment.** Because it is a chain rather than a digest of
          the whole set, a head published when the store held N receipts stays
          verifiable forever: recomputing over the first N rows must reproduce
          it exactly. Anchors do not go stale as the store grows.
        * **Independent of the self-reported digest.** The chain uses a digest
          recomputed from the stored bytes, not the ``receipt_sha256`` field
          inside the receipt. Rewriting a receipt *and* its own digest field --
          the exact gap that triggers this work, since SQLite triggers only
          block SQL-level mutation and not edits to the file -- still moves the
          chain head.

        Rows are ordered by ``rowid``, the true insertion order: deletes are
        trigger-blocked, so rowids are never reused and the order of any prefix
        is stable as new receipts arrive. ``created_at`` is deliberately not
        used for ordering because it is a wall-clock value and a replayed or
        clock-skewed receipt could reorder an already-published prefix.

        Only digests and counts are returned; no receipt content is exposed.
        """

        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")

        digest = hashlib.sha256(RECEIPT_CHAIN_DOMAIN.encode("utf-8")).hexdigest()
        count = 0
        mismatches: list[str] = []
        first_created_at: str | None = None
        last_created_at: str | None = None

        with self._lock:
            connection = self._connect()
            try:
                query = (
                    "SELECT event_id, receipt_json, created_at "
                    "FROM forecast_receipts ORDER BY rowid"
                )
                parameters: tuple[Any, ...] = ()
                if limit is not None:
                    query += " LIMIT ?"
                    parameters = (limit,)
                for row in connection.execute(query, parameters):
                    event_id = str(row["event_id"])
                    receipt = json.loads(row["receipt_json"])
                    if not isinstance(receipt, dict):
                        raise RuntimeError("stored forecast receipt is not a JSON object")
                    recomputed = receipt_digest(receipt)
                    recorded = receipt.get("receipt_sha256")
                    if isinstance(recorded, str) and recorded != recomputed:
                        mismatches.append(event_id)
                    digest = hashlib.sha256(
                        f"{digest}{event_id}{recomputed}".encode("utf-8")
                    ).hexdigest()
                    count += 1
                    if first_created_at is None:
                        first_created_at = str(row["created_at"])
                    last_created_at = str(row["created_at"])
            finally:
                if self._memory_connection is None:
                    connection.close()

        return {
            "algorithm": RECEIPT_CHAIN_DOMAIN,
            "head_sha256": digest,
            "receipt_count": count,
            "self_reported_digest_mismatches": mismatches,
            "first_receipt_created_at": first_created_at,
            "last_receipt_created_at": last_created_at,
        }

    def backup_to(self, destination: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
        """Create and verify a consistent SQLite backup.

        The backup uses SQLite's online backup API, so it does not rely on a
        byte-for-byte copy taken while the database is active. Existing files
        are refused by default to avoid silently replacing evidence.
        """

        if self._memory_connection is not None:
            raise ValueError("an in-memory receipt store cannot be backed up by path")
        destination_path = Path(destination)
        source_path = Path(self.path)
        if destination_path.resolve() == source_path.resolve():
            raise ValueError("backup destination must differ from the receipt database")
        if destination_path.exists() and not overwrite:
            raise FileExistsError(f"backup destination already exists: {destination_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            source = self._connect()
            target = sqlite3.connect(str(destination_path), timeout=10)
            try:
                source_check = source.execute("PRAGMA integrity_check").fetchone()
                source_integrity = str(source_check[0]) if source_check is not None else ""
                if source_integrity != "ok":
                    raise RuntimeError(
                        f"receipt database integrity check failed: {source_integrity}"
                    )
                source_count_row = source.execute(
                    "SELECT COUNT(*) FROM forecast_receipts"
                ).fetchone()
                source_count = int(source_count_row[0]) if source_count_row is not None else 0
                source.backup(target)
                target.commit()
                target_check = target.execute("PRAGMA integrity_check").fetchone()
                target_integrity = str(target_check[0]) if target_check is not None else ""
                target_count_row = target.execute(
                    "SELECT COUNT(*) FROM forecast_receipts"
                ).fetchone()
                target_count = int(target_count_row[0]) if target_count_row is not None else 0
            finally:
                target.close()
                source.close()

        if target_integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {target_integrity}")
        if target_count != source_count:
            raise RuntimeError(
                f"backup row count mismatch: source={source_count}, backup={target_count}"
            )
        return {
            "path": str(destination_path),
            "bytes": destination_path.stat().st_size,
            "integrity_check": target_integrity,
            "source_row_count": source_count,
            "backup_row_count": target_count,
            "restore_check": True,
        }
