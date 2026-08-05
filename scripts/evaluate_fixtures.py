#!/usr/bin/env python3
"""Run the development reference evaluator over contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.reference_evaluator import evaluate_reference


def main() -> None:
    cases = json.loads((ROOT / "fixtures" / "evaluation_cases.json").read_text())
    results = []
    for case in cases:
        result = evaluate_reference(case["question"], case["ground_truth"], case["raw_response"])
        results.append({"case_id": case["case_id"], **result.to_dict()})
    print(
        json.dumps(
            {
                "fixture_set": "development-only",
                "warning": "This proxy is not the unreleased official Telegraph scorer.",
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
