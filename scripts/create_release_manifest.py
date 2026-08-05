#!/usr/bin/env python3
"""Create a deterministic, non-secret source manifest for deployment evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ("src", "miners", "scripts", "Dockerfile", "Caddyfile", "pyproject.toml", ".env.example")


def _ignored(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    return (
        "__pycache__" in parts
        or path.suffix in {".pyc", ".pyo"}
        or path.name == ".env"
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or "artifacts" in parts
    )


def _files() -> list[Path]:
    paths: list[Path] = []
    for item in INCLUDE:
        path = ROOT / item
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
    return sorted(path for path in paths if not _ignored(path))


def build_manifest(release_id: str) -> dict[str, object]:
    entries = []
    for path in _files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    tree_bytes = "\n".join(f"{entry['path']}:{entry['sha256']}" for entry in entries).encode()
    return {
        "schema_version": 1,
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_sha256": manifest["source_sha256"]}))


if __name__ == "__main__":
    main()
