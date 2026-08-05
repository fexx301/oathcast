#!/usr/bin/env python3
"""Read-only discovery of active external weather Miners.

This calls Telegraph's unpriced integrations endpoint. It does not make a
paid Miner request, sign a wallet challenge, or write a registry snapshot.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from oathcast.discovery import discover_weather_miners, integration_records
from oathcast.payment import TelegraphX402Client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--own-slug",
        action="append",
        default=["oathcast-weather"],
        help="owned slug to exclude; may be repeated",
    )
    parser.add_argument(
        "--own-id",
        action="append",
        default=[],
        help="owned Miner id to exclude; may be repeated",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON path for a timestamped read-only observation snapshot",
    )
    args = parser.parse_args()

    payload = TelegraphX402Client().discover_integrations()
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    capabilities = discover_weather_miners(
        integration_records(payload),
        own_slugs=set(args.own_slug),
        own_ids=set(args.own_id),
    )
    result = {
        "observed_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "telegraph_integrations_read_only",
        "qualifying_traffic": False,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "weather_capabilities": [
                {
                    "id": capability.miner_id,
                    "slug": capability.slug,
                    "name": capability.name,
                    "endpoint": capability.endpoint_name,
                    "minimum_price_micro_usdc": capability.min_price_micro_usdc,
                    "intents": sorted(capability.intents),
                }
                for capability in capabilities
            ],
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
