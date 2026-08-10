from pathlib import Path
import tempfile
import unittest

from oathcast.receipts import SqliteReceiptStore, receipt_digest
from scripts.anchor_receipt_head import build_anchor, verify_anchor


def _receipt(event_id: str) -> dict:
    receipt = {
        "question": {"event_id": event_id, "threshold_mm": 0.1},
        "created_at": "2026-08-10T12:00:00Z",
        "forecast": {"probability": 0.7},
    }
    receipt["receipt_sha256"] = receipt_digest(receipt)
    return receipt


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.database = Path(self._directory.name) / "receipts.sqlite3"

    def _store(self) -> SqliteReceiptStore:
        store = SqliteReceiptStore(self.database)
        self.addCleanup(store.close)
        return store

    def test_an_anchor_records_the_head_count_and_integrity_state(self):
        store = self._store()
        store.save(_receipt("anchor-0"))
        anchor = build_anchor(store, note="pre-registration")
        self.assertEqual(anchor["receipt_count"], 1)
        self.assertEqual(anchor["integrity_check"], "ok")
        self.assertEqual(anchor["note"], "pre-registration")
        self.assertTrue(anchor["head_sha256"])
        self.assertEqual(anchor["self_reported_digest_mismatches"], [])

    def test_an_anchor_still_verifies_after_more_receipts_arrive(self):
        # The whole point of a prefix commitment: an anchor published today must
        # still verify next week against a larger store.
        store = self._store()
        store.save(_receipt("grow-0"))
        anchor = build_anchor(store)

        store.save(_receipt("grow-1"))
        store.save(_receipt("grow-2"))
        result = verify_anchor(store, anchor)
        self.assertTrue(result["ok"])
        self.assertEqual(result["receipts_added_since_anchor"], 2)
        self.assertEqual(result["recomputed_head_sha256"], anchor["head_sha256"])
        self.assertNotEqual(result["current_head_sha256"], anchor["head_sha256"])

    def test_verification_fails_when_an_anchored_receipt_is_rewritten(self):
        store = self._store()
        store.save(_receipt("tamper-0"))
        anchor = build_anchor(store)
        store.close()

        # Length-preserving raw-file edit: bypasses the immutability trigger the
        # same way an attacker with file access would.
        raw = self.database.read_bytes()
        patched = raw.replace(b'"probability":0.7', b'"probability":0.9')
        self.assertNotEqual(patched, raw)
        self.database.write_bytes(patched)

        reopened = SqliteReceiptStore(self.database)
        self.addCleanup(reopened.close)
        result = verify_anchor(reopened, anchor)
        self.assertFalse(result["ok"])
        self.assertIn("altered", result["error"])

    def test_verification_fails_when_receipts_are_missing(self):
        # A truncated store must not "verify" against a shorter prefix -- that
        # would let evidence loss pass as healthy.
        store = self._store()
        store.save(_receipt("missing-0"))
        store.save(_receipt("missing-1"))
        anchor = build_anchor(store)

        smaller = SqliteReceiptStore(":memory:")
        self.addCleanup(smaller.close)
        smaller.save(_receipt("missing-0"))
        result = verify_anchor(smaller, anchor)
        self.assertFalse(result["ok"])
        self.assertIn("missing", result["error"])

    def test_verification_rejects_a_malformed_anchor(self):
        store = self._store()
        with self.assertRaises(ValueError):
            verify_anchor(store, {"head_sha256": "abc"})
        with self.assertRaises(ValueError):
            verify_anchor(store, {"receipt_count": 1})

    def test_an_anchor_contains_no_receipt_content(self):
        store = self._store()
        store.save(_receipt("privacy-0"))
        anchor = build_anchor(store)
        serialized = repr(anchor)
        self.assertNotIn("privacy-0", serialized)
        self.assertNotIn("probability", serialized)
        self.assertNotIn("threshold_mm", serialized)


if __name__ == "__main__":
    unittest.main()
