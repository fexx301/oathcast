#!/usr/bin/env python3
"""Validate and fingerprint an observation JSON export."""

from __future__ import annotations

import argparse
import json

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.ground_truth import FileObservationSource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="JSON observation export")
    args = parser.parse_args()
    source = FileObservationSource(args.path)
    print(json.dumps(source.manifest(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
