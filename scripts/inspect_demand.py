#!/usr/bin/env python3
"""Inspect local demand provenance without presenting it as official traffic."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from oathcast.demand import DemandLedger


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("OATHCAST_DEMAND_DB", ROOT / "state" / "demand.sqlite3")),
        help="SQLite provenance ledger (created if absent)",
    )
    parser.add_argument("--events", action="store_true", help="include immutable event records")
    args = parser.parse_args()

    ledger = DemandLedger(args.db)
    output = {"summary": ledger.summary()}
    if args.events:
        output["events"] = ledger.list_events()
    print(json.dumps(output, indent=2, sort_keys=True))
    print(
        "WARNING: local_candidate_events are conservative local observations only; "
        "they are not Telegraph's official request count."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
