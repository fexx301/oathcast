from pathlib import Path
import sqlite3
import tempfile
import unittest

from oathcast.receipts import (
    DEFAULT_MAX_RECEIPT_BYTES,
    DEFAULT_MAX_RECEIPT_ROWS,
    ReceiptStoreFull,
    SqliteReceiptStore,
)


def _receipt(event_id: str) -> dict:
    return {
        "question": {"event_id": event_id, "threshold_mm": 0.1},
        "created_at": "2026-08-10T12:00:00Z",
        "forecast": {"probability": 0.7},
    }


class ReceiptCapacityTests(unittest.TestCase):
    """The store bounds growth by refusing new writes, never by evicting.

    Receipts are immutable by trigger and exist to be replayed after cutoff, so
    a retention policy that deletes them would destroy the property they are
    built to provide (and the DELETE trigger would reject it anyway).
    """

    def test_a_new_receipt_past_the_row_cap_is_refused(self):
        store = SqliteReceiptStore(":memory:", max_rows=2)
        try:
            store.save(_receipt("cap-1"))
            store.save(_receipt("cap-2"))
            with self.assertRaises(ReceiptStoreFull):
                store.save(_receipt("cap-3"))
            self.assertEqual(store.row_count(), 2)
        finally:
            store.close()

    def test_replaying_an_existing_receipt_still_succeeds_at_capacity(self):
        # The load-bearing case. A full store must not break durable replay for
        # events the Miner has already publicly committed to.
        store = SqliteReceiptStore(":memory:", max_rows=1)
        try:
            store.save(_receipt("cap-replay"))
            with self.assertRaises(ReceiptStoreFull):
                store.save(_receipt("cap-other"))
            replayed = store.save(_receipt("cap-replay"))
            self.assertEqual(replayed, _receipt("cap-replay"))
            self.assertEqual(store.get("cap-replay"), _receipt("cap-replay"))
        finally:
            store.close()

    def test_a_new_receipt_past_the_byte_cap_is_refused(self):
        store = SqliteReceiptStore(":memory:", max_rows=None, max_bytes=1)
        try:
            # An empty database already occupies at least one page, so the
            # first new receipt is refused on bytes alone.
            with self.assertRaises(ReceiptStoreFull):
                store.save(_receipt("bytes-1"))
        finally:
            store.close()

    def test_capacity_reports_when_the_store_stops_accepting_new_receipts(self):
        store = SqliteReceiptStore(":memory:", max_rows=2)
        try:
            store.save(_receipt("report-1"))
            report = store.capacity()
            self.assertTrue(report["accepting_new_receipts"])
            self.assertEqual(report["rows"], 1)
            self.assertEqual(report["max_rows"], 2)
            self.assertEqual(report["rows_remaining"], 1)
            self.assertGreater(report["used_bytes"], 0)

            store.save(_receipt("report-2"))
            full = store.capacity()
            self.assertFalse(full["accepting_new_receipts"])
            self.assertEqual(full["rows_remaining"], 0)
        finally:
            store.close()

    def test_caps_can_be_disabled_independently(self):
        store = SqliteReceiptStore(":memory:", max_rows=None, max_bytes=None)
        try:
            for index in range(5):
                store.save(_receipt(f"uncapped-{index}"))
            report = store.capacity()
            self.assertTrue(report["accepting_new_receipts"])
            self.assertIsNone(report["max_rows"])
            self.assertIsNone(report["max_bytes"])
            self.assertNotIn("rows_remaining", report)
            self.assertNotIn("bytes_remaining", report)
        finally:
            store.close()

    def test_nonpositive_caps_are_rejected_at_construction(self):
        # A zero or negative cap would mean "accept nothing", which is a
        # misconfiguration rather than a policy. Fail at startup, not on the
        # first forecast.
        with self.assertRaises(ValueError):
            SqliteReceiptStore(":memory:", max_rows=0)
        with self.assertRaises(ValueError):
            SqliteReceiptStore(":memory:", max_bytes=-1)

    def test_defaults_are_applied_when_no_caps_are_passed(self):
        store = SqliteReceiptStore(":memory:")
        try:
            report = store.capacity()
            self.assertEqual(report["max_rows"], DEFAULT_MAX_RECEIPT_ROWS)
            self.assertEqual(report["max_bytes"], DEFAULT_MAX_RECEIPT_BYTES)
            self.assertTrue(report["accepting_new_receipts"])
        finally:
            store.close()

    def test_a_capped_store_survives_reopening_the_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            first = SqliteReceiptStore(database, max_rows=1)
            try:
                first.save(_receipt("persist-1"))
            finally:
                first.close()

            second = SqliteReceiptStore(database, max_rows=1)
            try:
                self.assertFalse(second.capacity()["accepting_new_receipts"])
                with self.assertRaises(ReceiptStoreFull):
                    second.save(_receipt("persist-2"))
                # ...and the existing receipt is still readable and replayable.
                self.assertEqual(second.get("persist-1"), _receipt("persist-1"))
                self.assertEqual(second.save(_receipt("persist-1")), _receipt("persist-1"))
            finally:
                second.close()


class ReceiptWriteReadinessTests(unittest.TestCase):
    def test_transactional_write_probe_succeeds(self):
        store = SqliteReceiptStore(":memory:")
        try:
            self.assertEqual(
                store.write_readiness(),
                {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                },
            )
        finally:
            store.close()

    def test_transactional_write_probe_rolls_back_without_accumulating_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(_receipt("probe-preserves-receipts"))
                for _ in range(3):
                    self.assertTrue(store.write_readiness()["ready"])
                self.assertEqual(store.row_count(), 1)
            finally:
                store.close()

            connection = sqlite3.connect(database)
            try:
                probe_rows = connection.execute(
                    "SELECT COUNT(*) FROM receipt_store_write_probe"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(probe_rows, 0)

    def test_transactional_write_probe_rolls_back_when_update_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            # The probe table is intentionally created lazily so an existing
            # read-only database can start and report an unready write probe.
            # Initialize it before installing a trigger that simulates a
            # rejected update.
            self.assertTrue(store.write_readiness()["ready"])
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TRIGGER receipt_store_write_probe_no_update
                    BEFORE UPDATE ON receipt_store_write_probe
                    BEGIN
                        SELECT RAISE(ABORT, 'probe update rejected');
                    END;
                    """
                )
            finally:
                connection.close()

            try:
                status = store.write_readiness()
            finally:
                store.close()

            self.assertFalse(status["ready"])
            self.assertTrue(status["rolled_back"])
            self.assertEqual(status["error"], "write_unavailable")

            connection = sqlite3.connect(database)
            try:
                probe_rows = connection.execute(
                    "SELECT COUNT(*) FROM receipt_store_write_probe"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(probe_rows, 0)

    def test_transactional_write_probe_rejects_a_read_only_connection(self):
        class ReadOnlyReceiptStore(SqliteReceiptStore):
            def _connect(self) -> sqlite3.Connection:
                connection = sqlite3.connect(
                    f"file:{self.path}?mode=ro",
                    uri=True,
                    timeout=10,
                )
                connection.row_factory = sqlite3.Row
                return connection

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            writable = SqliteReceiptStore(database)
            writable.close()

            store = ReadOnlyReceiptStore(database)
            try:
                # This is the production failure mode the probe must catch:
                # acquiring a write transaction appears healthy, but the first
                # actual write reports SQLITE_READONLY.
                connection = store._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.rollback()
                finally:
                    connection.close()

                status = store.write_readiness()
            finally:
                store.close()

            self.assertEqual(
                status,
                {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "error": "write_unavailable",
                },
            )
            self.assertNotIn(str(database), str(status))

    def test_legacy_read_only_store_starts_and_reports_missing_probe_table(self):
        """A post-v5 migration fault belongs on /readyz, not at startup."""

        class ReadOnlyReceiptStore(SqliteReceiptStore):
            def _connect(self) -> sqlite3.Connection:
                connection = sqlite3.connect(
                    f"file:{self.path}?mode=ro",
                    uri=True,
                    timeout=10,
                )
                connection.row_factory = sqlite3.Row
                return connection

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy-receipts.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE forecast_receipts (
                        event_id TEXT PRIMARY KEY,
                        question_json TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TRIGGER forecast_receipts_no_update
                    BEFORE UPDATE ON forecast_receipts
                    BEGIN
                        SELECT RAISE(ABORT, 'forecast receipts are immutable');
                    END;

                    CREATE TRIGGER forecast_receipts_no_delete
                    BEFORE DELETE ON forecast_receipts
                    BEGIN
                        SELECT RAISE(ABORT, 'forecast receipts are immutable');
                    END;
                    """
                )
            finally:
                connection.close()

            store = ReadOnlyReceiptStore(database)
            try:
                status = store.write_readiness()
            finally:
                store.close()

            self.assertEqual(
                status,
                {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "error": "write_unavailable",
                },
            )


class ReceiptBackupTests(unittest.TestCase):
    def test_integrity_backup_and_restore_check_preserve_receipt_count(self):
        receipt = {
            "question": {"event_id": "backup-1", "threshold_mm": 0.1},
            "created_at": "2026-08-04T12:00:00Z",
            "forecast": {"probability": 0.7},
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            backup = Path(directory) / "backups" / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(receipt)
                self.assertEqual(store.integrity_check(), "ok")
                evidence = store.backup_to(backup)
            finally:
                store.close()

            self.assertEqual(evidence["integrity_check"], "ok")
            self.assertEqual(evidence["source_row_count"], 1)
            self.assertEqual(evidence["backup_row_count"], 1)
            self.assertTrue(evidence["restore_check"])

            restored = SqliteReceiptStore(backup)
            try:
                self.assertEqual(restored.integrity_check(), "ok")
                self.assertEqual(restored.row_count(), 1)
                self.assertEqual(restored.get("backup-1"), receipt)
            finally:
                restored.close()

    def test_backup_refuses_to_replace_existing_evidence_by_default(self):
        receipt = {
            "question": {"event_id": "backup-2"},
            "created_at": "2026-08-04T12:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            backup = Path(directory) / "receipts.backup.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(receipt)
                store.backup_to(backup)
                with self.assertRaises(FileExistsError):
                    store.backup_to(backup)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
