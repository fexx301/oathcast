from pathlib import Path
import tempfile
import unittest

from oathcast.receipts import SqliteReceiptStore


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
