#!/usr/bin/env python3
"""Run the leakage-safe chronological provider backtest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.artifacts import atomic_write_text
from oathcast.backtest import load_chronological_cases, run_chronological_backtest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "fixtures" / "brier_cases.json",
    )
    parser.add_argument("--warmup-cases", type=int, default=4)
    parser.add_argument("--min-history-valid-cases", type=int, default=2)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional JSON report path",
    )
    args = parser.parse_args()

    cases, fixture_sha256 = load_chronological_cases(str(args.cases))
    report = run_chronological_backtest(
        cases,
        warmup_cases=args.warmup_cases,
        min_history_valid_cases=args.min_history_valid_cases,
    )
    report["fixture_source"] = {
        "path": str(args.cases.resolve().relative_to(ROOT)),
        "sha256": fixture_sha256,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
        return

    atomic_write_text(args.output, encoded)
    holdout = report["provider_summaries"]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "holdout_brier": {
                    provider: values["holdout"]["brier_score"]
                    for provider, values in sorted(holdout.items())
                },
                "holdout_coverage": {
                    provider: values["holdout"]["coverage"]
                    for provider, values in sorted(holdout.items())
                },
                "selection_end_to_end_score": report["prequential_selection"][
                    "selected_holdout_summary"
                ]["end_to_end_score"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
