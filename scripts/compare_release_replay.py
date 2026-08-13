#!/usr/bin/env python3
"""Prove one stored Miner response survived a release cutover unchanged."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FINGERPRINT_FIELDS = (
    "event_id",
    "receipt_sha256",
    "public_response_sha256",
)


def _forecast_check(report: dict[str, Any]) -> dict[str, Any] | None:
    checks = report.get("checks")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "authenticated_forecast":
            return check
    return None


def compare_release_replay(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Compare safe smoke fingerprints from two distinct releases."""

    errors: list[str] = []
    before_release = before.get("release_id")
    after_release = after.get("release_id")
    if not isinstance(before_release, str) or not before_release:
        errors.append("before report has no release_id")
    if not isinstance(after_release, str) or not after_release:
        errors.append("after report has no release_id")
    if before_release == after_release and isinstance(before_release, str):
        errors.append("before and after release IDs are identical")

    before_check = _forecast_check(before)
    after_check = _forecast_check(after)
    if before_check is None:
        errors.append("before report has no authenticated_forecast check")
    elif before_check.get("ok") is not True:
        errors.append("before authenticated forecast did not pass")
    if after_check is None:
        errors.append("after report has no authenticated_forecast check")
    elif after_check.get("ok") is not True:
        errors.append("after authenticated forecast did not pass")

    fingerprints: dict[str, Any] = {}
    if before_check is not None and after_check is not None:
        for field in FINGERPRINT_FIELDS:
            left = before_check.get(field)
            right = after_check.get(field)
            if not isinstance(left, str) or not left:
                errors.append(f"before report has no {field}")
                continue
            if not isinstance(right, str) or not right:
                errors.append(f"after report has no {field}")
                continue
            if left != right:
                errors.append(f"{field} changed across the release")
                continue
            fingerprints[field] = left

    return {
        "ok": not errors,
        "before_release_id": before_release,
        "after_release_id": after_release,
        "fingerprints": fingerprints,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise SystemExit("smoke reports must be JSON objects")
    result = compare_release_replay(before, after)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "ok": result["ok"]}))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
