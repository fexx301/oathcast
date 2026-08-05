#!/usr/bin/env python3
"""Run the local Script Author benchmark against adversarial fixtures.

The report is development evidence only. It is not Telegraph's Canonical
Script score, Track 2 result, or proof of WASM compatibility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oathcast.script_benchmark import load_script_benchmark_cases, run_script_benchmark


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "fixtures" / "script_author_adversarial.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional JSON report path",
    )
    parser.add_argument("--good-score-threshold", type=float, default=0.55)
    args = parser.parse_args()

    cases, fixture_sha256 = load_script_benchmark_cases(str(args.cases))
    report = run_script_benchmark(cases, good_score_threshold=args.good_score_threshold)
    report["fixture_source"] = {
        "path": str(args.cases.resolve().relative_to(ROOT)),
        "sha256": fixture_sha256,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        summary = report["summary"]
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "candidate_behavior_accuracy": summary["candidate_behavior_accuracy"],
                    "behavior_accuracy_improvement": summary["behavior_accuracy_improvement"],
                    "candidate_adversarial_rejection_rate": summary[
                        "candidate_adversarial_rejection_rate"
                    ],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
