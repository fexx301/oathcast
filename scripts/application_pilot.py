#!/usr/bin/env python3
"""Serve the local OathCast Planning Desk intake pilot."""

from __future__ import annotations

import argparse
import sys


from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.pilot import serve_pilot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--database", default="state/pilot.sqlite3")
    args = parser.parse_args()
    serve_pilot(host=args.host, port=args.port, database=args.database)


if __name__ == "__main__":
    main()
