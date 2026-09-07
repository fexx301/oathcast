#!/usr/bin/env python3
"""Run the public OathCast decision UI in fail-closed mode by default."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


# Allow ``python scripts/run_decision_ui.py`` from a source checkout without
# requiring an editable install.  The service itself remains stdlib-only.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oathcast.application_ui import LoopbackApplicationRunner  # noqa: E402
from oathcast.decision_ui import (  # noqa: E402
    DemoDecisionRunner,
    MAX_JSON_BODY_BYTES,
    TelegraphDecisionRunner,
    make_server,
)


def _public_runner():
    if os.environ.get("OATHCAST_PUBLIC_DEMO") == "true":
        return DemoDecisionRunner()
    gateway_runner = LoopbackApplicationRunner.from_environment()
    if gateway_runner is None or not gateway_runner.configured:
        return None
    return TelegraphDecisionRunner(
        gateway_runner,
        routing_configured=True,
        payment_configured=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OathCast outdoor decision UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787).")
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=MAX_JSON_BODY_BYTES,
        help=f"Maximum JSON body size (default: {MAX_JSON_BODY_BYTES}).",
    )
    args = parser.parse_args()

    runner = _public_runner()
    server = make_server(
        args.host,
        args.port,
        decision_runner=runner,
        max_body_bytes=args.max_body_bytes,
    )
    mode = server.application.public_mode
    print(
        f"OathCast decision UI listening on http://{args.host}:{server.server_address[1]} "
        f"(public mode: {mode})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
