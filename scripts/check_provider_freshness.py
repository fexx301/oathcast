#!/usr/bin/env python3
"""Check freshness of the provider-evidence collection branch.

This is a read-only monitor.  It never edits the paired-forecast dataset and
does not call either weather provider.  Collection freshness is measured from
the newest case's ``issued_at`` rather than a Git commit timestamp: resolving
an old case must not make a stalled collector look healthy.  Resolution
freshness is measured from each unresolved case's ``horizon_end``.

The scheduled workflow runs the two checks as separate jobs so a stale
collector and a late observation export produce distinct alerts.  Defaults are
intentionally conservative for the intended schedules: the data-branch
collector is requested hourly but delivered best effort, and a daily
observation-resolution process may miss one cycle before it pages an operator.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Literal, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.backtest import ChronologicalCase, load_chronological_cases
from oathcast.forecast import format_timestamp, parse_timestamp


UTC = timezone.utc
DEFAULT_DATASET = ROOT / "artifacts" / "provider-equivalence" / "paired-forecasts.json"
DEFAULT_COLLECTION_AGE_HOURS = 6.0
DEFAULT_RESOLUTION_AGE_HOURS = 48.0
CHECKS = ("collection", "resolution")
CheckName = Literal["collection", "resolution"]


class FreshnessCheckError(RuntimeError):
    """The monitor could not establish a trustworthy evidence status."""


@dataclass(frozen=True)
class CollectionStatus:
    latest_issued_at: str
    age_hours: float
    max_age_hours: float
    fresh: bool
    reason: str | None = None


@dataclass(frozen=True)
class ResolutionStatus:
    unresolved_count: int
    overdue_count: int
    max_age_hours: float
    fresh: bool
    oldest_overdue_case: dict[str, Any] | None = None


def _round_hours(value: timedelta) -> float:
    return round(value.total_seconds() / 3600.0, 3)


def _duration_from_hours(value: float | int | str, *, field_name: str) -> timedelta:
    try:
        hours = float(value)
    except (TypeError, ValueError) as exc:
        raise FreshnessCheckError(f"{field_name} must be a finite non-negative number") from exc
    if not math.isfinite(hours) or hours < 0:
        raise FreshnessCheckError(f"{field_name} must be a finite non-negative number")
    return timedelta(hours=hours)


def _now(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        return parse_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise FreshnessCheckError(f"invalid --now timestamp: {value!r}") from exc


def _require_utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FreshnessCheckError("now must include a timezone")
    return value.astimezone(UTC)


def load_cases(path: Path) -> tuple[list[ChronologicalCase], str]:
    """Load and schema-validate a provider dataset without writing to it."""

    if not path.exists():
        raise FreshnessCheckError(f"dataset does not exist: {path}")
    if not path.is_file():
        raise FreshnessCheckError(f"dataset is not a regular file: {path}")
    try:
        return load_chronological_cases(str(path))
    except Exception as exc:  # noqa: BLE001 - convert every input failure to a clean alert
        raise FreshnessCheckError(
            f"dataset failed chronological validation ({type(exc).__name__})"
        ) from exc


def check_collection(
    cases: Sequence[ChronologicalCase],
    *,
    now: datetime,
    max_age: timedelta,
) -> CollectionStatus:
    """Return collection freshness based on the newest issued case.

    A future-dated case is not accepted as fresh.  It could otherwise mask a
    stalled collector caused by a clock or data-integrity mistake.
    """

    if not cases:
        raise FreshnessCheckError("provider dataset contains no cases")
    latest = max(case.issued_at for case in cases)
    age = now - latest
    if age < timedelta(0):
        return CollectionStatus(
            latest_issued_at=format_timestamp(latest),
            age_hours=_round_hours(age),
            max_age_hours=_round_hours(max_age),
            fresh=False,
            reason="latest_issued_at_is_in_the_future",
        )
    fresh = age <= max_age
    return CollectionStatus(
        latest_issued_at=format_timestamp(latest),
        age_hours=_round_hours(age),
        max_age_hours=_round_hours(max_age),
        fresh=fresh,
        reason=None if fresh else "latest_issued_at_exceeds_max_age",
    )


def _overdue_case(case: ChronologicalCase, *, now: datetime, max_age: timedelta) -> bool:
    if case.outcome is not None:
        return False
    # Equal to the deadline is still inside the stated allowance.  The alert
    # means the case is *beyond* the grace period, not merely due at its edge.
    return now > case.horizon_end + max_age


def check_resolution(
    cases: Sequence[ChronologicalCase],
    *,
    now: datetime,
    max_age: timedelta,
) -> ResolutionStatus:
    """Return the count and oldest member of the late-unresolved set."""

    unresolved = [case for case in cases if case.outcome is None]
    overdue = sorted(
        (case for case in unresolved if _overdue_case(case, now=now, max_age=max_age)),
        key=lambda case: (case.horizon_end, case.case_id),
    )
    oldest = overdue[0] if overdue else None
    oldest_payload = None
    if oldest is not None:
        oldest_payload = {
            "case_id": oldest.case_id,
            "horizon_end": format_timestamp(oldest.horizon_end),
            "age_hours": _round_hours(now - oldest.horizon_end),
        }
    return ResolutionStatus(
        unresolved_count=len(unresolved),
        overdue_count=len(overdue),
        max_age_hours=_round_hours(max_age),
        fresh=not overdue,
        oldest_overdue_case=oldest_payload,
    )


def build_report(
    path: Path,
    *,
    now: datetime,
    max_collection_age: timedelta,
    max_resolution_age: timedelta,
    selected_checks: Sequence[CheckName] = CHECKS,
) -> dict[str, Any]:
    """Build a JSON-safe report and select independent alert codes."""

    selected = tuple(dict.fromkeys(selected_checks))
    if not selected:
        raise FreshnessCheckError("at least one freshness check must be selected")
    unknown = set(selected) - set(CHECKS)
    if unknown:
        raise FreshnessCheckError(f"unsupported checks: {', '.join(sorted(unknown))}")

    now = _require_utc_now(now)
    cases, dataset_sha256 = load_cases(path)
    collection = check_collection(cases, now=now, max_age=max_collection_age)
    resolution = check_resolution(cases, now=now, max_age=max_resolution_age)

    alerts: list[str] = []
    if "collection" in selected and not collection.fresh:
        alerts.append("collection_stale")
    if "resolution" in selected and not resolution.fresh:
        alerts.append("resolution_stale")

    return {
        "status": "alert" if alerts else "ok",
        "checks": list(selected),
        "checked_at": format_timestamp(now),
        "dataset": str(path),
        "dataset_sha256": dataset_sha256,
        "case_count": len(cases),
        "collection": asdict(collection),
        "resolution": asdict(resolution),
        "alerts": alerts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--check",
        choices=("all", *CHECKS),
        default="all",
        help="which independent check controls the exit status (default: all)",
    )
    parser.add_argument(
        "--max-collection-age-hours",
        type=float,
        default=DEFAULT_COLLECTION_AGE_HOURS,
    )
    parser.add_argument(
        "--max-resolution-age-hours",
        type=float,
        default=DEFAULT_RESOLUTION_AGE_HOURS,
    )
    parser.add_argument(
        "--now",
        default=None,
        help="UTC/ISO timestamp for deterministic checks (defaults to current UTC time)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected: Sequence[CheckName] = CHECKS if args.check == "all" else (args.check,)
    try:
        report = build_report(
            args.dataset,
            now=_now(args.now),
            max_collection_age=_duration_from_hours(
                args.max_collection_age_hours,
                field_name="--max-collection-age-hours",
            ),
            max_resolution_age=_duration_from_hours(
                args.max_resolution_age_hours,
                field_name="--max-resolution-age-hours",
            ),
            selected_checks=selected,
        )
    except FreshnessCheckError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "checks": list(selected),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
