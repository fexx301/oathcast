#!/usr/bin/env python3
"""Collect paired provider forecasts for the P4 event-equivalence test.

Neither Open-Meteo nor WeatherAPI sells a historical *forecast* archive, so the
held-out comparison between their precipitation semantics can only be built
forward: one run per scheduled interval, each appending unresolved cases that
are resolved later against an independent observation export.

Two modes:

    collect   append one case per location at a fixed lead time
    resolve   fill in outcomes for cases whose window has closed

The WeatherAPI key is read from ``WEATHERAPI_KEY`` in the environment and never
accepted as an argument, because arguments land in shell history and process
listings. The key is embedded in the request URL, and urllib copies that URL
into its exception messages, so every error path here is scrubbed before it is
printed.

This script makes ordinary upstream API calls on the operator's own provider
accounts. It is not Telegraph traffic and is not hackathon demand.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.adapters import OpenMeteoAdapter, WeatherApiAdapter
from oathcast.artifacts import atomic_write_text
from oathcast.backtest import load_chronological_cases
from oathcast.forecast import ForecastQuestion, format_timestamp, parse_timestamp
from oathcast.ground_truth import FileObservationSource, resolve_precipitation
from oathcast.service import fetch_json

HORIZON = timedelta(hours=1)
DEFAULT_LEAD_HOURS = 3
DEFAULT_DATASET = ROOT / "artifacts" / "provider-equivalence" / "paired-forecasts.json"
DEFAULT_LOCATIONS = ROOT / "fixtures" / "collection_locations.json"

# A climatology that was never sourced is a fabricated baseline, and Brier skill
# is measured against it. Refuse the placeholder rather than score against a
# number nobody chose.
UNSET_CLIMATOLOGY_SOURCE = "UNSET"


class CollectionError(RuntimeError):
    """A collection run could not produce an honest record."""


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "<redacted>") if secret else text


def load_locations(path: Path) -> list[dict[str, Any]]:
    """Load the frozen location list, rejecting an unsourced climatology."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise CollectionError(f"{path} must be a non-empty JSON list of locations")

    locations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise CollectionError(f"location {index} must be an object")
        missing = {
            "slug",
            "location_name",
            "latitude",
            "longitude",
            "climatology_probability",
            "climatology_source",
        } - set(item)
        if missing:
            raise CollectionError(
                f"location {index} is missing {', '.join(sorted(missing))}"
            )

        slug = str(item["slug"]).strip()
        if not slug or any(character in slug for character in " \t/"):
            raise CollectionError(f"location {index} has an unusable slug: {slug!r}")
        if slug in seen:
            raise CollectionError(f"duplicate location slug: {slug}")
        seen.add(slug)

        climatology = float(item["climatology_probability"])
        if not 0 <= climatology <= 1:
            raise CollectionError(f"{slug} climatology_probability must be in [0, 1]")
        if str(item["climatology_source"]).strip().upper() == UNSET_CLIMATOLOGY_SOURCE:
            raise CollectionError(
                f"{slug} has no sourced climatology. Brier skill is measured against "
                "this baseline, so it must be derived from a frozen historical record "
                "and cited in climatology_source -- never from the data being collected."
            )

        locations.append(
            {
                "slug": slug,
                "location_name": str(item["location_name"]),
                "latitude": float(item["latitude"]),
                "longitude": float(item["longitude"]),
                "climatology_probability": climatology,
                "climatology_source": str(item["climatology_source"]),
            }
        )
    return locations


def build_question(
    location: dict[str, Any], issued_at: datetime, lead_hours: int
) -> ForecastQuestion:
    horizon_start = issued_at + timedelta(hours=lead_hours)
    return ForecastQuestion(
        event_id=case_id_for(location["slug"], issued_at),
        location_name=location["location_name"],
        latitude=location["latitude"],
        longitude=location["longitude"],
        horizon_start=horizon_start,
        horizon_end=horizon_start + HORIZON,
        forecast_cutoff=issued_at,
    )


def case_id_for(slug: str, issued_at: datetime) -> str:
    return f"{slug}-{issued_at:%Y%m%dT%H%M}Z"


def _attempt(
    adapter: Any, question: ForecastQuestion, issued_at: datetime, api_key: str | None
) -> dict[str, Any]:
    """Fetch one provider, recording a non-valid status instead of raising.

    A provider that fails is part of the record: dropping the case would bias
    the comparison toward whichever provider happens to be more available.
    """

    try:
        url = adapter.build_url(question, api_key=api_key)
        payload = fetch_json(url)
        forecast = adapter.parse(payload, question, issued_at=issued_at, retrieved_at=issued_at)
    except Exception as exc:  # noqa: BLE001 - any upstream failure is a datapoint
        return {
            "probability": None,
            "status": "missing",
            "error": f"{type(exc).__name__}: {_scrub(str(exc), api_key)}",
        }
    return {
        "probability": round(float(forecast.probability), 6),
        "status": "valid",
        "adapter_version": forecast.adapter_version,
        "event_equivalence": forecast.event_equivalence,
    }


def collect_once(
    locations: list[dict[str, Any]],
    *,
    issued_at: datetime,
    lead_hours: int,
    weatherapi_key: str | None,
) -> list[dict[str, Any]]:
    """Build one case per location, sorted so equal issued_at stays ordered."""

    adapters = [
        ("open_meteo", OpenMeteoAdapter(), None),
        ("weatherapi", WeatherApiAdapter(), weatherapi_key),
    ]

    cases: list[dict[str, Any]] = []
    for location in locations:
        question = build_question(location, issued_at, lead_hours)
        forecasts = {
            name: _attempt(adapter, question, issued_at, key)
            for name, adapter, key in adapters
        }
        cases.append(
            {
                "case_id": question.event_id,
                "issued_at": format_timestamp(issued_at),
                "forecast_cutoff": format_timestamp(question.forecast_cutoff),
                "horizon_start": format_timestamp(question.horizon_start),
                "horizon_end": format_timestamp(question.horizon_end),
                "outcome": None,
                "resolved_at": None,
                "climatology_probability": location["climatology_probability"],
                "forecasts": forecasts,
                # Ignored by ChronologicalCase.from_dict; retained so `resolve`
                # can rebuild the question and so the file carries provenance.
                "location": {
                    "slug": location["slug"],
                    "location_name": location["location_name"],
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "climatology_source": location["climatology_source"],
                },
            }
        )
    cases.sort(key=lambda case: case["case_id"])
    return cases


def read_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise CollectionError(f"{path} must be a JSON list of chronological cases")
    return data


def lead_time_of(case: dict[str, Any]) -> timedelta:
    return parse_timestamp(case["horizon_start"]) - parse_timestamp(case["issued_at"])


def merge_cases(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
    *,
    allow_lead_change: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Append only unseen cases, preserving chronological ordering.

    Comparing providers at different lead times measures lead time, not the
    providers, so a changed lead is an error unless explicitly allowed.
    """

    known = {case["case_id"] for case in existing}
    if existing and new and not allow_lead_change:
        previous, current = lead_time_of(existing[-1]), lead_time_of(new[0])
        if previous != current:
            raise CollectionError(
                f"lead time changed from {previous} to {current}. Providers compared at "
                "different lead times are not comparable; pass --allow-lead-change only "
                "if you intend to start a separate series."
            )

    added = [case for case in new if case["case_id"] not in known]
    merged = existing + added
    merged.sort(key=lambda case: (case["issued_at"], case["case_id"]))
    return merged, [case["case_id"] for case in added]


def resolve_cases(
    cases: list[dict[str, Any]], source: FileObservationSource, *, now: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fill outcomes from an independent observation export.

    Only windows that have already closed are resolvable; a case resolved
    before its own horizon_end would be rejected by the schema, and rightly so.
    """

    counts = {"resolved": 0, "already_resolved": 0, "window_open": 0, "unresolved": 0}
    updated: list[dict[str, Any]] = []
    for case in cases:
        record = dict(case)
        if record.get("outcome") is not None:
            counts["already_resolved"] += 1
            updated.append(record)
            continue

        horizon_end = parse_timestamp(record["horizon_end"])
        if horizon_end > now:
            counts["window_open"] += 1
            updated.append(record)
            continue

        location = record.get("location")
        if not isinstance(location, dict):
            counts["unresolved"] += 1
            updated.append(record)
            continue

        question = ForecastQuestion(
            event_id=record["case_id"],
            location_name=location["location_name"],
            latitude=location["latitude"],
            longitude=location["longitude"],
            horizon_start=parse_timestamp(record["horizon_start"]),
            horizon_end=horizon_end,
            forecast_cutoff=parse_timestamp(record["forecast_cutoff"]),
        )
        result = resolve_precipitation(question, source.observe(question), resolved_at=now)
        if result.status != "resolved":
            counts["unresolved"] += 1
            record["resolution_issue"] = result.issue or result.status
            updated.append(record)
            continue

        record["outcome"] = result.outcome
        record["resolved_at"] = format_timestamp(result.resolved_at)
        record["observation"] = {
            "source": result.source,
            "observation_id": result.observation_id,
            "precipitation_mm": result.precipitation_mm,
        }
        record.pop("resolution_issue", None)
        counts["resolved"] += 1
        updated.append(record)
    return updated, counts


def write_dataset(path: Path, cases: list[dict[str, Any]]) -> None:
    """Write atomically, then re-load through the real loader.

    A scheduled job that dies mid-write must not leave a truncated dataset, and
    a file the backtest cannot load is worse than no file at all.
    """

    try:
        atomic_write_text(
            path,
            json.dumps(cases, indent=2, sort_keys=True) + "\n",
            validate=lambda candidate: load_chronological_cases(str(candidate)),
        )
    except Exception as exc:  # noqa: BLE001 - never install an unloadable dataset
        raise CollectionError(f"refusing to write an invalid dataset: {exc}") from exc


def _utc_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("collect", "resolve"), default="collect")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--locations", type=Path, default=DEFAULT_LOCATIONS)
    parser.add_argument("--lead-hours", type=int, default=DEFAULT_LEAD_HOURS)
    parser.add_argument(
        "--observations",
        type=Path,
        default=None,
        help="observation export (resolve mode)",
    )
    parser.add_argument("--allow-lead-change", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and print without writing the dataset",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)

    try:
        if args.mode == "resolve":
            if args.observations is None:
                raise CollectionError("resolve mode requires --observations")
            cases = read_dataset(args.dataset)
            if not cases:
                raise CollectionError(f"{args.dataset} has no cases to resolve")
            source = FileObservationSource(args.observations)
            updated, counts = resolve_cases(cases, source, now=now)
            if not args.dry_run:
                write_dataset(args.dataset, updated)
            print(
                json.dumps(
                    {
                        "mode": "resolve",
                        "counts": counts,
                        "dry_run": args.dry_run,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        if args.lead_hours < 1:
            raise CollectionError("--lead-hours must be at least 1")

        weatherapi_key = os.environ.get("WEATHERAPI_KEY") or None
        if weatherapi_key is None:
            print(
                "WEATHERAPI_KEY is not set; collecting Open-Meteo only. The paired "
                "comparison needs both providers.",
                file=sys.stderr,
            )

        locations = load_locations(args.locations)
        issued_at = _utc_hour(now)
        collected = collect_once(
            locations,
            issued_at=issued_at,
            lead_hours=args.lead_hours,
            weatherapi_key=weatherapi_key,
        )
        existing = read_dataset(args.dataset)
        merged, added = merge_cases(
            existing, collected, allow_lead_change=args.allow_lead_change
        )
        if not args.dry_run:
            write_dataset(args.dataset, merged)

        valid = sum(
            1
            for case in collected
            for forecast in case["forecasts"].values()
            if forecast["status"] == "valid"
        )
        print(
            json.dumps(
                {
                    "mode": "collect",
                    "issued_at": format_timestamp(issued_at),
                    "lead_hours": args.lead_hours,
                    "cases_collected": len(collected),
                    "cases_added": len(added),
                    "cases_total": len(merged),
                    "valid_provider_attempts": valid,
                    "expected_provider_attempts": len(collected) * 2,
                    "dataset": str(args.dataset),
                    "dry_run": args.dry_run,
                },
                indent=2,
                sort_keys=True,
            )
        )
        for case in collected:
            for provider, forecast in sorted(case["forecasts"].items()):
                if forecast["status"] != "valid":
                    print(
                        f"  {case['case_id']} {provider}: {forecast.get('error')}",
                        file=sys.stderr,
                    )
        return 0
    except CollectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
