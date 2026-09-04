#!/usr/bin/env python3
"""Run the private, loopback-only Track 3 Application gateway.

This launcher intentionally refuses to start unless the operator has enabled
the paid path explicitly and a separately running TypeScript sidecar is
present. The Python process never reads or prints the Solana private key; the
sidecar owns that secret.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oathcast.application_gateway import LiveApplicationService, make_application_gateway
from oathcast.application_payment import UnixSocketPaymentClient, ApplicationPaymentBoundary
from oathcast.cases import SqliteCaseStore
from oathcast.demand import DemandLedger
from oathcast.discovery import MinerCapability


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _is_socket(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def main() -> None:
    if os.environ.get("OATHCAST_APPLICATION_ENABLE_PAID") != "true":
        raise RuntimeError(
            "OATHCAST_APPLICATION_ENABLE_PAID must be true; the live Application path is disabled by default"
        )
    socket_path = os.environ.get(
        "OATHCAST_APPLICATION_SOCKET",
        "/var/run/oathcast/application-payment.sock",
    )
    if not _is_socket(socket_path):
        raise RuntimeError("the private payment sidecar Unix socket is not available")
    app_token = _required("OATHCAST_APPLICATION_TOKEN")
    sidecar_token = _required("OATHCAST_APPLICATION_SIDECAR_TOKEN")
    dispatcher_url = _required("OATHCAST_DISPATCHER_URL")
    miner_id = os.environ.get("OATHCAST_APPLICATION_ALLOWED_MINER_IDS", "212").split(",")[0].strip()
    endpoint = os.environ.get("OATHCAST_APPLICATION_ALLOWED_ENDPOINTS", "forecast").split(",")[0].strip()
    if miner_id != "212" or endpoint != "forecast":
        raise RuntimeError("the first live Application rollout is pinned to external Miner 212/forecast")

    capability = MinerCapability(
        miner_id="212",
        slug="weatherapi",
        name="WeatherAPI",
        base_url=dispatcher_url,
        intents=frozenset({"WEATHER_FORECAST"}),
        endpoint_path="/v1/212/forecast",
        endpoint_name="forecast",
        registry_snapshot_sha256=os.environ.get("OATHCAST_APPLICATION_REGISTRY_SNAPSHOT_SHA256"),
    )
    payment_client = UnixSocketPaymentClient(socket_path, sidecar_token)
    payment_boundary = ApplicationPaymentBoundary(
        payment_client,
        allowed_miner_ids={"212"},
        allowed_endpoints={"forecast"},
    )
    case_store = SqliteCaseStore(
        os.environ.get("OATHCAST_APPLICATION_CASE_DB", "state/application.sqlite3")
    )
    demand_ledger = DemandLedger(
        os.environ.get("OATHCAST_DEMAND_DB", "state/demand.sqlite3")
    )
    service = LiveApplicationService(
        capabilities=(capability,),
        payment_boundary=payment_boundary,
        case_store=case_store,
        demand_ledger=demand_ledger,
    )
    host = os.environ.get("OATHCAST_APPLICATION_HOST", "127.0.0.1")
    port = int(os.environ.get("OATHCAST_APPLICATION_PORT", "8790"))
    server = make_application_gateway(service, app_token=app_token, host=host, port=port)
    print(f"OathCast Application gateway listening on {host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        case_store.close()
        demand_ledger.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
