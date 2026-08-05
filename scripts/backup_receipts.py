#!/usr/bin/env python3
"""Back up and integrity-check the OathCast receipt database.

This command emits only operational metadata. It never prints receipt rows,
questions, provider payloads, or authentication material.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from oathcast.receipts import SqliteReceiptStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not args.database.exists():
        parser.error(f"receipt database does not exist: {args.database}")
    store = SqliteReceiptStore(args.database)
    try:
        source_integrity = store.integrity_check()
        backup = store.backup_to(args.output, overwrite=args.overwrite)
        print(
            json.dumps(
                {
                    "database": str(args.database),
                    "source_integrity_check": source_integrity,
                    "verified_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "backup": backup,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
