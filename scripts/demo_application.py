#!/usr/bin/env python3
"""Run the local Application decision-to-resolution evidence loop.

This is deliberately fixture-only. It demonstrates the product behavior and
evidence shape without claiming Telegraph traffic, payment, or live ground
truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any

from oathcast.application import CrossMinerRouter
from oathcast.cases import SqliteCaseStore
from oathcast.discovery import MinerCapability, discover_weather_miners, load_registry_snapshot
from oathcast.forecast import ForecastQuestion
from oathcast.ground_truth import MappingObservationSource, PrecipitationObservation
from oathcast.presentation import render_application_demo_markdown
from oathcast.workflow import ApplicationWorkflow


ROOT = Path(__file__).resolve().parents[1]


def run_demo(
    *,
    disable_owned: bool = False,
    compare_owned_fallback: bool = False,
    database: Path | None = None,
) -> dict[str, Any]:
    question = ForecastQuestion.from_dict(
        json.loads((ROOT / "fixtures" / "question.json").read_text(encoding="utf-8"))
    )
    records = load_registry_snapshot(ROOT / "fixtures" / "miner_registry.json")
    own = next(MinerCapability.from_dict(record) for record in records if record.get("owner") == "oathcast")
    external = discover_weather_miners(records, own_slugs={own.slug})
    capabilities = [own, *external]
    probabilities = {
        own.slug: 0.90,
        "independent-weather-alpha": 0.20,
        "independent-weather-beta": 0.30,
    }
    clients = {
        capability.slug: (lambda _question, probability=probabilities.get(capability.slug, 0.25): {
            "probability": probability,
            "content": f"Fixture probability: {probability:.0%}",
        })
        for capability in capabilities
    }
    observation = PrecipitationObservation(
        event_id=question.event_id,
        latitude=question.latitude,
        longitude=question.longitude,
        window_start=question.horizon_start,
        window_end=question.horizon_end,
        precipitation_mm=0.25,
        source="development-fixture-observation",
        observation_id="fixture-observation-1",
        observed_at=question.horizon_end,
    )

    temporary_directory = None
    fallback_temporary_directory = None
    if database is None:
        temporary_directory = tempfile.TemporaryDirectory()
        database = Path(temporary_directory.name) / "application.sqlite3"
    try:
        store = SqliteCaseStore(database)
        workflow = ApplicationWorkflow(
            CrossMinerRouter(
                capabilities,
                clients,
                own_slugs={own.slug},
                require_external=True,
            ),
            store,
            MappingObservationSource({question.event_id: observation}),
        )
        decision = workflow.decide(question, disable_owned=disable_owned)
        resolution = workflow.resolve(question)
        fallback_evidence = None
        if compare_owned_fallback:
            fallback_temporary_directory = tempfile.TemporaryDirectory()
            fallback_database = Path(fallback_temporary_directory.name) / "fallback.sqlite3"
            fallback_store = SqliteCaseStore(fallback_database)
            fallback_workflow = ApplicationWorkflow(
                CrossMinerRouter(
                    capabilities,
                    clients,
                    own_slugs={own.slug},
                    require_external=True,
                ),
                fallback_store,
                MappingObservationSource({question.event_id: observation}),
            )
            fallback_decision = fallback_workflow.decide(question, disable_owned=True)
            fallback_resolution = fallback_workflow.resolve(question)
            valid_fallback_replies = [reply for reply in fallback_decision.replies if reply.valid]
            fallback_ok = bool(valid_fallback_replies) and all(
                not reply.owned for reply in valid_fallback_replies
            ) and fallback_decision.used_external_miner
            if not fallback_ok:
                raise RuntimeError("owned-Miner fallback did not use a valid external response")
            fallback_evidence = {
                "ok": True,
                "owned_miner_disabled": True,
                "decision": fallback_decision.to_dict(),
                "resolution": fallback_resolution.to_dict(),
                "case_evidence": fallback_store.get(question.event_id),
            }
        return {
            "fixture_set": "development-only",
            "qualifying_traffic": False,
            "owned_miner_disabled": disable_owned,
            "decision": decision.to_dict(),
            "resolution": resolution.to_dict(),
            "case_evidence": store.get(question.event_id),
            "owned_miner_fallback": fallback_evidence,
        }
    finally:
        if fallback_temporary_directory is not None:
            fallback_temporary_directory.cleanup()
        if temporary_directory is not None:
            temporary_directory.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable-owned", action="store_true")
    parser.add_argument(
        "--compare-owned-fallback",
        action="store_true",
        help="also run the same fixture case with the owned Miner disabled",
    )
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="presentation format; markdown is the human-readable demo shell",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    payload = run_demo(
        disable_owned=args.disable_owned,
        compare_owned_fallback=args.compare_owned_fallback,
        database=args.database,
    )
    output = (
        render_application_demo_markdown(payload)
        if args.format == "markdown"
        else json.dumps(payload, indent=2, sort_keys=True)
    )
    if args.output is None:
        print(output)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
