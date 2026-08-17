#!/usr/bin/env python3
"""Back up and integrity-check the OathCast receipt database.

This command emits only operational metadata. It never prints receipt rows,
questions, provider payloads, or authentication material.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3



def backup_read_only(
    source_path: Path,
    destination_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Back up a live SQLite file without opening the source read-write."""

    source_path = source_path.resolve()
    destination_path = destination_path.resolve()
    if source_path == destination_path:
        raise ValueError("backup destination must differ from the receipt database")
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"backup destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=10)
    target = sqlite3.connect(str(destination_path), timeout=10)
    try:
        source_integrity_row = source.execute("PRAGMA integrity_check").fetchone()
        source_integrity = (
            str(source_integrity_row[0]) if source_integrity_row is not None else ""
        )
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
        target_integrity_row = target.execute("PRAGMA integrity_check").fetchone()
        target_integrity = (
            str(target_integrity_row[0]) if target_integrity_row is not None else ""
        )
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
        "sha256": hashlib.sha256(destination_path.read_bytes()).hexdigest(),
        "integrity_check": target_integrity,
        "source_row_count": source_count,
        "backup_row_count": target_count,
        "restore_check": True,
        "source_open_mode": "ro",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not args.database.exists():
        parser.error(f"receipt database does not exist: {args.database}")
    backup = backup_read_only(args.database, args.output, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "database": str(args.database),
                "source_integrity_check": "ok",
                "verified_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "backup": backup,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
