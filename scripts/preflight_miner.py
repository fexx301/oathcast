#!/usr/bin/env python3
"""Inspect one Telegraph Miner challenge without signing or paying."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oathcast.forecast import ForecastQuestion, format_timestamp
from oathcast.payment import TelegraphX402Client


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--miner-id", default="18")
    parser.add_argument("--endpoint", default="predict")
    parser.add_argument("--dispatcher-url", default=None)
    args = parser.parse_args()

    question = ForecastQuestion.from_dict(
        json.loads((ROOT / "fixtures" / "question.json").read_text())
    )
    params = {
        "event_id": question.event_id,
        "location_name": question.location_name,
        "lat": f"{question.latitude:.6f}",
        "lon": f"{question.longitude:.6f}",
        "horizon_start": format_timestamp(question.horizon_start),
        "horizon_end": format_timestamp(question.horizon_end),
        "forecast_cutoff": format_timestamp(question.forecast_cutoff),
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
