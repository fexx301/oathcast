#!/usr/bin/env python3
"""Read the per-Intent Miner leaderboard and report the score to beat.

Read-only: no payment, no signature, no registration side effect. This replaces
the hand-run curl commands that produced the numbers in the strategy notes and
could not be reproduced.

    PYTHONPATH=src python3 scripts/read_leaderboard.py
    PYTHONPATH=src python3 scripts/read_leaderboard.py --output snapshot.json

The Explorer returns one epoch snapshot containing all Intents. This reader
fetches that response once and selects requested Intent keys locally; it does
not rely on query filters that the server may silently ignore.

The scores printed here are other Miners' Telegraph scores. They are NOT
comparable to the local renderer proxy in `benchmark_renderer.py`, which is an
overlap/length stand-in rather than Telegraph's cosine + BM25 + length
composite. Both land in the same 0.4-0.7 range, which is exactly why the
comparison is tempting and wrong.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from oathcast.leaderboard import (
    LeaderboardError,
    fetch_weather_leaderboards,
    renderer_target,
)


#: Intents declared in miners/oathcast-weather.yaml. Keep in sync with the YAML.
DECLARED_INTENTS = ("WEATHER_FORECAST",)

PROXY_WARNING = (
    "Not comparable to the local renderer proxy (overlap/length stand-in, not "
    "cosine + BM25). Do not read a proxy score against these numbers."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intent",
        action="append",
        help="Intent to read; repeatable. Defaults to all weather Intents.",
    )
    parser.add_argument("--output", help="write a timestamped JSON snapshot here")
    args = parser.parse_args()

    intents = tuple(args.intent) if args.intent else None
    try:
        boards = fetch_weather_leaderboards(intents)
    except LeaderboardError as error:
        print(f"leaderboard read refused: {error}", file=sys.stderr)
        return 2

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"observed_at {observed_at}   (intents selected locally)")
    print()

    snapshot: dict[str, object] = {
        "observed_at_utc": observed_at,
        "source": "telegraph_explorer_leaderboard_read_only",
        "selection": "exact_local_intent_key",
        "declared_intents": list(DECLARED_INTENTS),
        "comparability_warning": PROXY_WARNING,
        "intents": {},
    }

    for name, board in boards.items():
        declared = " [declared]" if name in DECLARED_INTENTS else ""
        plural = "miner" if board.population == 1 else "miners"
        print(
            f"{name}{declared}  ({board.population} returned {plural}, epoch {board.epoch})"
        )
        for item in board.entries:
            marker = "  <- best active" if item is board.leader() else ""
            print(f"    {item.describe()}{marker}")
        actives = len(board.active_entries)
        note = "  <- zero margin: we would be the third" if actives == 2 else ""
        print(f"    active miners: {actives}{note}")
        print()

        snapshot["intents"][name] = {
            "population": board.population,
            "epoch": board.epoch,
            "active_miners": actives,
            "target_score": board.target_score(),
            "entries": [
                {
                    "miner_slug": item.slug,
                    "activation_status": item.activation_status,
                    "score": item.score,
                    "rank": item.rank,
                }
                for item in board.entries
            ],
        }

    target = renderer_target(boards, DECLARED_INTENTS)
    snapshot["renderer_target"] = target
    if target is None:
        print("no active miner in any declared Intent — no target available")
    else:
        hardest = max(
            (n for n in DECLARED_INTENTS if n in boards and boards[n].target_score() is not None),
            key=lambda n: boards[n].target_score(),
        )
        print(f"renderer target: {target:.4f}  (hardest declared Intent: {hardest})")
        print("Clearing the hardest declared Intent clears the others. A mean across")
        print("Intents would sit between the real bars and be wrong in both directions.")
    print()
    print(PROXY_WARNING)

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nsnapshot written to {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
