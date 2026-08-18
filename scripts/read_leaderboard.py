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
overlap/length stand-in rather than the cosine + BM25 + length behavior
described in pre-launch guidance, which has not been verified as the current
Canonical Script. Both land in the same 0.4-0.7 range, which is exactly why the
comparison is tempting and wrong.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from oathcast.artifacts import atomic_write_text
from oathcast.discovery import WEATHER_INTENTS
from oathcast.leaderboard import (
    LeaderboardError,
    leaderboard_url,
    parse_leaderboards,
    renderer_target,
    urllib_fetch,
)


#: Intents declared in miners/oathcast-weather.yaml. Keep in sync with the YAML.
DECLARED_INTENTS = ("WEATHER_FORECAST",)

PROXY_WARNING = (
    "Not comparable to the local renderer proxy (an overlap/length stand-in). "
    "Do not read a proxy score against these numbers."
)


def select_boards(
    available: dict[str, object],
    requested: tuple[str, ...] | None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Choose which Intents to report, and say what was left out.

    An explicit ``--intent`` is honoured exactly: a missing one is an error,
    because the caller named it. Without one, a declared Intent going missing is
    still a hard failure -- that is a real signal about our own registration --
    while the other weather Intents are context and are skipped when the
    endpoint no longer serves them. Requiring all of ``WEATHER_INTENTS`` made
    the whole read abort once the live endpoint dropped
    ``WEATHER_RISK_ASSESSMENT``, which is a report about somebody else's Intent
    list, not about ours.
    """

    if requested is not None:
        missing = [name for name in requested if name not in available]
        if missing:
            raise LeaderboardError(
                "requested intent(s) not present in the response: "
                f"{', '.join(sorted(missing))}; available: "
                f"{', '.join(sorted(available)) or 'none'}"
            )
        return {name: available[name] for name in requested}, ()

    missing_declared = [name for name in DECLARED_INTENTS if name not in available]
    if missing_declared:
        raise LeaderboardError(
            "declared intent(s) missing from the response: "
            f"{', '.join(sorted(missing_declared))}; available: "
            f"{', '.join(sorted(available)) or 'none'}"
        )
    context = tuple(
        name
        for name in sorted(WEATHER_INTENTS)
        if name in available and name not in DECLARED_INTENTS
    )
    omitted = tuple(
        name
        for name in sorted(WEATHER_INTENTS)
        if name not in available and name not in DECLARED_INTENTS
    )
    selected = tuple(DECLARED_INTENTS) + context
    return {name: available[name] for name in selected}, omitted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intent",
        action="append",
        help=(
            "Intent to read; repeatable. A named Intent must be present or the "
            "read fails. Defaults to the declared Intents plus any other weather "
            "Intents the endpoint still serves."
        ),
    )
    parser.add_argument("--output", help="write a timestamped JSON snapshot here")
    args = parser.parse_args()

    requested = tuple(args.intent) if args.intent else None
    try:
        payload = urllib_fetch(leaderboard_url())
        available = parse_leaderboards(payload)
        boards, omitted = select_boards(available, requested)
    except LeaderboardError as error:
        print(f"leaderboard read refused: {error}", file=sys.stderr)
        return 2

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"observed_at {observed_at}   (intents selected locally)")
    if omitted:
        print(f"not served by this endpoint, skipped: {', '.join(omitted)}")
    print()

    snapshot: dict[str, object] = {
        "observed_at_utc": observed_at,
        "source": "telegraph_explorer_leaderboard_read_only",
        "selection": "exact_local_intent_key",
        "declared_intents": list(DECLARED_INTENTS),
        "reported_intents": list(boards),
        "weather_intents_not_served": list(omitted),
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
        atomic_write_text(path, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(f"\nsnapshot written to {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
