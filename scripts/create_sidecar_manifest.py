#!/usr/bin/env python3
"""Create a deterministic, non-secret manifest for the payment-sidecar image."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.artifacts import atomic_write_text


INCLUDE = (
    "payment-canary/Dockerfile",
    "payment-canary/package.json",
    "payment-canary/package-lock.json",
    "payment-canary/tsconfig.json",
    "payment-canary/vitest.config.ts",
    "payment-canary/src",
)


def _files() -> list[Path]:
    paths: list[Path] = []
    for item in INCLUDE:
        path = ROOT / item
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
    return sorted(
        path for path in paths
        if "node_modules" not in path.relative_to(ROOT).parts
        and "__pycache__" not in path.relative_to(ROOT).parts
        and path.suffix not in {".pyc", ".pyo"}
    )


def build_manifest(release_id: str) -> dict[str, object]:
    entries = [
        {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in _files()
    ]
    tree_bytes = "\n".join(
        f"{entry['path']}:{entry['sha256']}" for entry in entries
    ).encode()
    return {
        "schema_version": 1,
        "artifact_type": "oathcast_payment_sidecar_manifest",
        "release_id": release_id,
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_sha256": hashlib.sha256(tree_bytes).hexdigest(),
        "files": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    manifest = build_manifest(args.release_id)
    encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
        return
    atomic_write_text(args.output, encoded)
    print(json.dumps({"output": str(args.output), "source_sha256": manifest["source_sha256"]}))


if __name__ == "__main__":
    main()
