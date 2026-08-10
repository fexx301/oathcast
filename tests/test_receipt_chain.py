import hashlib
from pathlib import Path
import tempfile
import unittest

from oathcast.receipts import (
    RECEIPT_CHAIN_DOMAIN,
    ReceiptTampering,
    SqliteReceiptStore,
    receipt_digest,
)


def _receipt(event_id: str, *, created_at: str = "2026-08-10T12:00:00Z") -> dict:
    receipt = {
        "question": {"event_id": event_id, "threshold_mm": 0.1},
        "created_at": created_at,
        "forecast": {"probability": 0.7},
    }
    receipt["receipt_sha256"] = receipt_digest(receipt)
    return receipt


def _expected_chain_head(rows) -> str:
    digest = hashlib.sha256(RECEIPT_CHAIN_DOMAIN.encode("utf-8")).hexdigest()
    for event_id, receipt in rows:
        digest = hashlib.sha256(
            f"{digest}{event_id}{receipt_digest(receipt)}".encode("utf-8")
        ).hexdigest()
    return digest


def _rewrite_raw_file(database: Path, event_id: str, *, recompute_digest: bool) -> dict:
    """Mutate a stored receipt by editing the database file bytes directly.

    The app-level UPDATE trigger cannot be used to stage this: blocking SQL
    mutation is exactly what it does, and that protection is real. The threat
    the hash chain exists to detect is a mutation of the *file*, made while the
    Miner is not running or against a copy of the database -- which bypasses the
    trigger entirely. Editing raw bytes reproduces that path.

    Every substitution is length-preserving so the SQLite page structure stays
    valid: ``0.7`` -> ``0.9`` and one 64-char hex digest for another.
    """

    original = _receipt(event_id)
    mutated = _receipt(event_id)
    mutated["forecast"]["probability"] = 0.9
    mutated["receipt_sha256"] = (
        receipt_digest(mutated) if recompute_digest else original["receipt_sha256"]
    )

    raw = database.read_bytes()
    patched = raw.replace(b'"probability":0.7', b'"probability":0.9')
    if patched == raw:
        raise AssertionError("probability bytes not found in the database file")
    if recompute_digest:
        before = patched
        patched = patched.replace(
            original["receipt_sha256"].encode(), mutated["receipt_sha256"].encode()
        )
        if patched == before:
            raise AssertionError("digest bytes not found in the database file")
    if len(patched) != len(raw):
        raise AssertionError("raw rewrite must be length-preserving")
    database.write_bytes(patched)
    return mutated


class ChainHeadTests(unittest.TestCase):
    def test_empty_store_chain_head_is_the_domain_anchor(self):
        store = SqliteReceiptStore(":memory:")
        try:
            head = store.chain_head()
            self.assertEqual(head["head_sha256"], _expected_chain_head([]))
            self.assertEqual(head["receipt_count"], 0)
            self.assertEqual(head["self_reported_digest_mismatches"], [])
            self.assertIsNone(head["first_receipt_created_at"])
            self.assertIsNone(head["last_receipt_created_at"])
        finally:
            store.close()

    def test_chain_head_matches_manual_recomputation_and_moves_per_receipt(self):
        store = SqliteReceiptStore(":memory:")
        try:
            rows = []
            seen = set()
            for index in range(3):
                receipt = _receipt(f"chain-{index}")
                store.save(receipt)
                rows.append((f"chain-{index}", receipt))
                head = store.chain_head()
                self.assertEqual(head["receipt_count"], index + 1)
                self.assertEqual(head["head_sha256"], _expected_chain_head(rows))
                self.assertNotIn(head["head_sha256"], seen)
                seen.add(head["head_sha256"])
        finally:
            store.close()

    def test_chain_order_follows_insertion_not_wall_clock(self):
        # rowid order is stable for any published prefix; created_at is not.
        # A replayed or clock-skewed receipt must not reorder an already
        # published prefix, so the chain must not sort on created_at.
        store = SqliteReceiptStore(":memory:")
        try:
            later = _receipt("reorder-b", created_at="2026-08-10T09:00:00Z")
            earlier = _receipt("reorder-a", created_at="2026-08-10T08:00:00Z")
            store.save(later)
            store.save(earlier)
            head = store.chain_head()
            self.assertEqual(
                head["head_sha256"],
                _expected_chain_head([("reorder-b", later), ("reorder-a", earlier)]),
            )
            self.assertEqual(head["first_receipt_created_at"], "2026-08-10T09:00:00Z")
            self.assertEqual(head["last_receipt_created_at"], "2026-08-10T08:00:00Z")
        finally:
            store.close()

    def test_a_published_prefix_head_stays_reproducible_as_the_store_grows(self):
        # The property that makes an anchor worth publishing: a head committed
        # when the store held N receipts must still verify months later.
        store = SqliteReceiptStore(":memory:")
        try:
            first = _receipt("grow-0")
            second = _receipt("grow-1")
            store.save(first)
            store.save(second)
            published = store.chain_head(limit=2)
            self.assertEqual(
                published["head_sha256"],
                _expected_chain_head([("grow-0", first), ("grow-1", second)]),
            )

            third = _receipt("grow-2")
            store.save(third)
            self.assertEqual(store.chain_head(limit=2)["head_sha256"], published["head_sha256"])
            self.assertEqual(
                store.chain_head()["head_sha256"],
                _expected_chain_head(
                    [("grow-0", first), ("grow-1", second), ("grow-2", third)]
                ),
            )
        finally:
            store.close()

    def test_chain_head_moves_when_a_receipt_and_its_own_digest_are_rewritten(self):
        # The failure mode that motivates S5. Rewriting a receipt *and* its
        # self-reported receipt_sha256 defeats the per-receipt digest check,
        # because the receipt then attests to its own forged content. The chain
        # still moves, because it recomputes from stored bytes and a published
        # head cannot be retroactively changed.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(_receipt("tamper-0"))
                before = store.chain_head()["head_sha256"]
            finally:
                store.close()

            mutated = _rewrite_raw_file(database, "tamper-0", recompute_digest=True)

            reopened = SqliteReceiptStore(database)
            try:
                head = reopened.chain_head()
                # Self-consistent forgery: the per-receipt check sees nothing.
                self.assertEqual(head["self_reported_digest_mismatches"], [])
                # ...but the anchor no longer matches what was published.
                self.assertNotEqual(head["head_sha256"], before)
                self.assertEqual(
                    head["head_sha256"], _expected_chain_head([("tamper-0", mutated)])
                )
            finally:
                reopened.close()

    def test_chain_head_reports_a_receipt_whose_digest_field_was_left_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(_receipt("mismatch-0"))
            finally:
                store.close()

            _rewrite_raw_file(database, "mismatch-0", recompute_digest=False)

            reopened = SqliteReceiptStore(database)
            try:
                head = reopened.chain_head()
                self.assertEqual(head["self_reported_digest_mismatches"], ["mismatch-0"])
            finally:
                reopened.close()

    def test_chain_head_never_returns_receipt_content(self):
        store = SqliteReceiptStore(":memory:")
        try:
            store.save(_receipt("privacy-0"))
            head = store.chain_head()
            self.assertEqual(
                set(head),
                {
                    "algorithm",
                    "head_sha256",
                    "receipt_count",
                    "self_reported_digest_mismatches",
                    "first_receipt_created_at",
                    "last_receipt_created_at",
                },
            )
        finally:
            store.close()

    def test_chain_head_rejects_a_negative_limit(self):
        store = SqliteReceiptStore(":memory:")
        try:
            with self.assertRaises(ValueError):
                store.chain_head(limit=-1)
        finally:
            store.close()


class ReceiptTamperingTests(unittest.TestCase):
    def test_get_refuses_a_receipt_whose_bytes_no_longer_match_its_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.sqlite3"
            store = SqliteReceiptStore(database)
            try:
                store.save(_receipt("get-tamper"))
            finally:
                store.close()

            _rewrite_raw_file(database, "get-tamper", recompute_digest=False)

            reopened = SqliteReceiptStore(database)
            try:
                with self.assertRaises(ReceiptTampering):
                    reopened.get("get-tamper")
            finally:
                reopened.close()

    def test_a_receipt_without_a_digest_field_is_still_readable(self):
        # Older receipts predate the digest field. Refusing them would turn a
        # missing feature into data loss.
        store = SqliteReceiptStore(":memory:")
        try:
            legacy = {
                "question": {"event_id": "legacy-0"},
                "created_at": "2026-08-10T12:00:00Z",
            }
            store.save(legacy)
            self.assertEqual(store.get("legacy-0"), legacy)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
