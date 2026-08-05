"""Durable, immutable forecast receipts for the Miner service.

Receipts are intentionally small and self-contained. They freeze the exact
question, normalized forecast, public response, and raw-payload provenance
needed to replay a forecast after its cutoff without asking an upstream
provider for a new answer.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


class ReceiptConflict(RuntimeError):
    """Raised when an event id is reused for a different forecast question."""


class SqliteReceiptStore:
    """A small SQLite-backed append-only receipt store.

    The database schema also has SQLite triggers that reject updates and
    deletes. A repeated event id with the same question is idempotent; a
    repeated event id with different question data is a conflict.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
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

        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO forecast_receipts
                        (event_id, question_json, receipt_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, question_json, receipt_json, created_at),
                )
                connection.commit()
                row = connection.execute(
                    """
                    SELECT question_json, receipt_json
                    FROM forecast_receipts
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
            finally:
                if self._memory_connection is None:
                    connection.close()
        if row is None:
            raise RuntimeError("forecast receipt could not be persisted")
        if row["question_json"] != question_json:
            raise ReceiptConflict(
                f"event_id {event_id!r} is already bound to a different question"
            )
        stored = json.loads(row["receipt_json"])
        if not isinstance(stored, dict):
            raise RuntimeError("stored forecast receipt is not a JSON object")
        return stored

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
