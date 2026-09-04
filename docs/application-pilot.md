# OathCast Planning Desk pilot

The Planning Desk is the first user-facing Application surface. OathCast Miner
registration is complete and active. This local intake remains disconnected
from Telegraph routing and payment; the reviewed private Application boundary
is implemented separately and remains disabled by default.

## Run locally

From the OathCast directory:

```sh
PYTHONPATH=src python3 scripts/application_pilot.py \
  --host 127.0.0.1 \
  --port 8788 \
  --database state/pilot.sqlite3
```

Open `http://127.0.0.1:8788/` in a browser. The queue can be inspected with:

```sh
curl http://127.0.0.1:8788/api/healthz
curl http://127.0.0.1:8788/api/pilot-requests
```

Port `8787` belongs to the public decision/status UI at the Caddy edge. The
planning-only intake uses `8788` so starting it locally cannot replace that
fail-closed public surface.

The intake contract is deliberately narrow: one location, one exact UTC hour,
one cutoff before that hour, and the fixed event `measurable precipitation >
0.1 mm`. A request receives a stable `pilot-request-*` ID derived from its
canonical content, so a repeated submission is idempotent.

## What the pilot does and does not do

- Stores only the planning use case and forecast question; it does not request
  names, email addresses, phone numbers, or wallet details.
- Persists the request in a local SQLite queue with a content hash.
- Does not call an upstream weather API, Telegraph, a Miner, or a payment
  endpoint.
- Does not create qualifying hackathon traffic or claim adoption.
- Leaves each queued question ready for a later review step that will attach
  the official Telegraph/payment client and the approved observation source.

## Pilot recruitment

Recruit a small, specific cohort rather than asking for generic “weather
testing.” Good prompts are:

1. “What outdoor decision will you make if measurable rain is likely during a
   particular hour?”
2. “What location and one-hour UTC window matters to that decision?”
3. “What action would change if the forecast crosses the 50% line?”

Target planning contexts include market setup, outdoor event logistics,
delivery staging, sports-group scheduling, and small vendor operations. Keep
the tool framed as non-binding planning support.

## Transition to live Application traffic

Before routing any queued request through Telegraph, the operator must verify:

- the still-current frozen YAML and canonical Intent contract;
- the active OathCast registration and fresh availability of independent Miners;
- the official HTTPS payment/signer/settlement path;
- the approved observation source and resolution policy; and
- user permission to route the planning question.

Only then should a reviewed queue item become a paid Telegraph request. The
local queue is preparation and demand discovery, not a substitute for the
protocol's Explorer accounting.

## Private Application gateway

The reviewed live path is a separate loopback service plus a TypeScript
payment sidecar. It is not mounted behind the public UI or Caddy. The sidecar
owns the Solana signer and journal; the Python gateway receives only a bounded,
sanitized result. See [`application-payment-boundary.md`](application-payment-boundary.md)
for the threat model and activation gates.

The repository check is non-live:

```sh
PYTHONPATH=src python3 -m unittest tests.test_application_payment
cd payment-canary && npm run check
```

These tests use injected responses. They do not perform a paid request. A
real activation still requires fresh discovery and explicit authorization for
one devnet attempt.
