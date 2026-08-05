#!/usr/bin/env python3
"""Validate local Miner draft invariants before official Telegraph validation.

This intentionally does not pretend to be Telegraph's YAML parser. It checks
only the project-owned invariants that are safe to enforce before the official
schema and registration flow are released.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PRICE_USDC = Decimal("0.01")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _top_level_scalar(lines: list[str], key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.*?)\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return _unquote(match.group(1))
    return None


def _nested_scalar(lines: list[str], section: str, key: str) -> str | None:
    in_section = False
    for line in lines:
        if re.match(rf"^{re.escape(section)}:\s*$", line):
            in_section = True
            continue
        if in_section and line and not line[0].isspace() and not line.lstrip().startswith("#"):
            in_section = False
        if in_section:
            match = re.match(rf"^\s+{re.escape(key)}:\s*(.*?)\s*$", line)
            if match:
                return _unquote(match.group(1))
    return None


def validate_draft(path: Path, *, canonical: bool = False) -> dict[str, Any]:
    path = path.resolve()
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    version = _top_level_scalar(lines, "version")
    kind = _top_level_scalar(lines, "kind")
    slug = _top_level_scalar(lines, "slug")
    base_url = _top_level_scalar(lines, "base_url")
    price_text = _nested_scalar(lines, "on_chain", "min_price_usdc")

    if version != "1":
        errors.append("version must be \"1\"")
    if kind != "miner":
        errors.append("kind must be miner")
    if not slug:
        errors.append("slug is required")
    if not base_url:
        errors.append("base_url is required")
    if "auth:" not in lines:
        errors.append("auth block is required")
    if "endpoints:" not in lines:
        errors.append("endpoints block is required")
    if price_text is None:
        errors.append("on_chain.min_price_usdc is required")
    else:
        try:
            price = Decimal(price_text)
            if not price.is_finite() or price < MINIMUM_PRICE_USDC:
                errors.append("on_chain.min_price_usdc must be at least 0.01")
        except InvalidOperation:
            errors.append("on_chain.min_price_usdc must be numeric")
    if canonical:
        if slug != "oathcast-weather":
            errors.append("canonical draft must use slug oathcast-weather")
        if not base_url.startswith("https://"):
            errors.append("canonical base_url must use HTTPS")
        if "REPLACE" in base_url:
            errors.append("canonical base_url still contains a placeholder")
    return {
        "path": str(path.relative_to(ROOT)),
        "slug": slug,
        "base_url": base_url,
        "min_price_usdc": price_text,
        "canonical": canonical,
        "errors": errors,
        "valid": not errors,
    }


def validate_paths(paths: Iterable[Path], canonical: Path) -> dict[str, Any]:
    canonical = canonical.resolve()
    drafts = [validate_draft(path, canonical=path.resolve() == canonical) for path in paths]
    errors: list[str] = []
    slugs = [draft["slug"] for draft in drafts if draft["slug"]]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        errors.append(f"duplicate Miner slugs: {', '.join(duplicates)}")
    if not any(draft["canonical"] for draft in drafts):
        errors.append(f"canonical draft not found: {canonical}")
    errors.extend(
        f"{draft['path']}: {error}"
        for draft in drafts
        for error in draft["errors"]
    )
    return {"valid": not errors, "drafts": drafts, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, default=ROOT / "miners" / "oathcast-weather.yaml")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT / "miners").glob("*.yaml"))
    result = validate_paths(paths, args.canonical)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
