#!/usr/bin/env python3
"""Publish and verify the OathCast receipt-set head digest.

Receipts are digested, not signed. SQLite triggers block SQL-level mutation,
but anyone who can read the database file can rewrite a receipt *and* recompute
its ``receipt_sha256`` -- producing a self-consistent forgery that every
per-receipt check accepts. For a project pitched as a calibration court, that
gap matters: a Miner could quietly improve its own record after the fact.

The fix is external anchoring. Publishing the chain head to a place OathCast
does not control (a git commit, an X post, an Explorer memo) fixes a prefix in
time. Because the head is a chain rather than a set digest, a head published at
N receipts must reproduce exactly when recomputed over the first N rows -- so an
anchor never goes stale as the store grows, and any retroactive edit to those N
receipts breaks it.

**The anchor's value comes entirely from being published somewhere OathCast
cannot rewrite.** An anchor file sitting only in this repo proves nothing
against an attacker who can also edit the repo.

This command emits only digests, counts, and timestamps. It never prints
receipt rows, questions, provider payloads, or authentication material.

Usage:
    # Write a new anchor
    python3 scripts/anchor_receipt_head.py --database data/oathcast/receipts.sqlite3 \\
        --output artifacts/receipt-anchors/anchor-2026-08-10.json

    # Re-verify a previously published anchor against the live store
    python3 scripts/anchor_receipt_head.py --database data/oathcast/receipts.sqlite3 \\
        --verify artifacts/receipt-anchors/anchor-2026-08-10.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from oathcast.receipts import SqliteReceiptStore


ANCHOR_SCHEMA_VERSION = 1


def build_anchor(store: SqliteReceiptStore, *, note: str | None = None) -> dict:
    """Compute an anchor record for the store's current chain head."""

    head = store.chain_head()
    anchor = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "anchored_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "integrity_check": store.integrity_check(),
        **head,
    }
    if note:
        anchor["note"] = note
    return anchor


def verify_anchor(store: SqliteReceiptStore, anchor: dict) -> dict:
    """Recompute a published anchor's prefix against the current store.

    The recomputation is deliberately over the *first N* receipts, where N is
    the count recorded in the anchor. Comparing against the current full head
    would fail every time a new receipt arrived, making the check useless.
    """

    claimed_count = anchor.get("receipt_count")
    if not isinstance(claimed_count, int) or claimed_count < 0:
        raise ValueError("anchor is missing a valid receipt_count")
    claimed_head = anchor.get("head_sha256")
    if not isinstance(claimed_head, str) or not claimed_head:
        raise ValueError("anchor is missing a valid head_sha256")

    current = store.chain_head()
    recomputed = store.chain_head(limit=claimed_count)

    # A store with fewer receipts than the anchor claims cannot reproduce it.
    # Without this check a truncated store would silently "verify" against a
    # short prefix, which is exactly the evidence loss the anchor exists to
    # detect.
    receipts_present = recomputed["receipt_count"] >= claimed_count
    matches = receipts_present and recomputed["head_sha256"] == claimed_head

    result = {
        "anchor_head_sha256": claimed_head,
        "anchor_receipt_count": claimed_count,
        "recomputed_head_sha256": recomputed["head_sha256"],
        "recomputed_receipt_count": recomputed["receipt_count"],
        "current_head_sha256": current["head_sha256"],
        "current_receipt_count": current["receipt_count"],
        "receipts_added_since_anchor": max(0, current["receipt_count"] - claimed_count),
        "self_reported_digest_mismatches": current["self_reported_digest_mismatches"],
        "integrity_check": store.integrity_check(),
        "verified_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "ok": matches,
    }
    if not receipts_present:
        result["error"] = (
            f"store holds {recomputed['receipt_count']} receipts but the anchor "
            f"commits to {claimed_count}: receipts are missing"
        )
    elif not matches:
        result["error"] = (
            "recomputed head does not match the published anchor: the first "
            f"{claimed_count} receipts have been altered since anchoring"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="write a new anchor record here")
    parser.add_argument("--verify", type=Path, help="re-verify a previously published anchor")
    parser.add_argument("--note", help="short non-secret context recorded in the anchor")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.output and args.verify:
        parser.error("--output and --verify are separate operations; pass one")
    if not args.database.exists():
        parser.error(f"receipt database does not exist: {args.database}")
    if args.output and args.output.exists() and not args.overwrite:
        # Silently replacing a published anchor would destroy the only record
        # of what was committed to.
        parser.error(f"anchor already exists (use --overwrite to replace): {args.output}")

    store = SqliteReceiptStore(args.database)
    try:
        if args.verify:
            anchor = json.loads(args.verify.read_text(encoding="utf-8"))
            result = verify_anchor(store, anchor)
            result["anchor_file"] = str(args.verify)
            print(json.dumps(result, indent=2, sort_keys=True))
            if not result["ok"]:
                raise SystemExit(1)
            return

        record = build_anchor(store, note=args.note)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            record["anchor_file"] = str(args.output)
        print(json.dumps(record, indent=2, sort_keys=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
