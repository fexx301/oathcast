#!/usr/bin/env python3
"""Inspect one Telegraph Miner challenge without signing or paying."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from oathcast.forecast import ForecastQuestion, format_timestamp
from oathcast.payment import TelegraphX402Client


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_miner import rolling_horizon  # noqa: E402  (needs ROOT on the path first)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--miner-id", default="18")
    parser.add_argument("--endpoint", default="predict")
    parser.add_argument("--dispatcher-url", default=None)
    parser.add_argument(
        "--question-file",
        type=Path,
        default=None,
        help=(
            "pin a specific question. Defaults to a rolling horizon (12:00-13:00 UTC "
            "tomorrow) so a preflight never fails on a stale fixture date."
        ),
    )
    args = parser.parse_args()

    # The fixture's horizon is a fixed 2026-08-17T15:00Z, which goes stale the
    # same way it did for the canary: past a provider's rolling window now, and
    # behind its own forecast_cutoff after 2026-08-17T12:00Z. This script exists
    # to diagnose *dispatcher* problems on registration day -- Track 1 opens
    # 2026-08-17 -- so a preflight failing on its own stale fixture would be
    # actively misleading at exactly the moment it is needed. The fixture still
    # supplies location and threshold; only the dates roll.
    if args.question_file is not None:
        question = ForecastQuestion.from_dict(
            json.loads(args.question_file.read_text(encoding="utf-8"))
        )
        start, end, cutoff = (
            question.horizon_start,
            question.horizon_end,
            question.forecast_cutoff,
        )
        event_id = question.event_id
    else:
        question = ForecastQuestion.from_dict(
            json.loads((ROOT / "fixtures" / "question.json").read_text())
        )
        start, end, cutoff = rolling_horizon(datetime.now(timezone.utc))
        # Keep the label honest: the fixture's event_id names 2026-08-17T15:00Z,
        # and this one goes to Telegraph rather than staying local.
        event_id = f"preflight-lagos-{start:%Y%m%dT%H%M}z"
    params = {
        "event_id": event_id,
        "location_name": question.location_name,
        "lat": f"{question.latitude:.6f}",
        "lon": f"{question.longitude:.6f}",
        "horizon_start": format_timestamp(start),
        "horizon_end": format_timestamp(end),
        "forecast_cutoff": format_timestamp(cutoff),
        "threshold_mm": f"{question.threshold_mm:g}",
    }
    client_kwargs = {}
    if args.dispatcher_url:
        client_kwargs["dispatcher_url"] = args.dispatcher_url
    result = TelegraphX402Client(**client_kwargs).preflight_miner(
        args.miner_id,
        args.endpoint,
        params,
    )
    output = {
        "status": result.status,
        "request_url": result.request_url,
        "paid": False,
    }
    if result.challenge is not None:
        output["x402_version"] = result.challenge.version
        output["accepts"] = [
            {
                key: option.get(key)
                for key in (
                    "scheme",
                    "network",
                    "asset",
                    "amount",
                    "maxAmountRequired",
                    "payTo",
                    "resource",
                )
                if key in option
            }
            for option in result.challenge.accepts
        ]
    elif result.response is not None:
        output["response"] = result.response.body
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
