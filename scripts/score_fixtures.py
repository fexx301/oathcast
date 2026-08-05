#!/usr/bin/env python3
"""Run the development-only Brier benchmark for each provider fixture."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.scoring import BrierCase, evaluate_brier


def main() -> None:
    fixture_path = ROOT / "fixtures" / "brier_cases.json"
    rows = json.loads(fixture_path.read_text())
    provider_names = sorted(rows[0]["forecasts"])
    results = {}

    for provider in provider_names:
        cases = [
            BrierCase(
                case_id=row["case_id"],
                probability=row["forecasts"][provider]["probability"],
                outcome=row["outcome"],
                climatology_probability=row["climatology_probability"],
                status=row["forecasts"][provider]["status"],
            )
            for row in rows
        ]
        results[provider] = evaluate_brier(cases).to_dict()

    print(
        json.dumps(
            {
                "fixture_set": "development-only",
                "warning": "These fixtures are not qualifying Telegraph traffic.",
                "providers": results,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
