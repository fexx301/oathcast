# Public status and Planning Desk UI

`src/oathcast/decision_ui.py` is a small Python standard-library HTTP service.
Its default public page is a read-only product/status surface. An explicit
`OATHCAST_PUBLIC_DEMO=true` deployment opens the judge-facing Planning Desk:
the form is fully interactive, but its deterministic scenario is still
payment-free and explicitly labeled as not Telegraph-routed, unpaid,
non-qualifying demand, and not a safety guarantee.

For a reviewed live deployment, `scripts/run_decision_ui.py` can attach
`oathcast.application_ui.LoopbackApplicationRunner`. That adapter only accepts
the fixed loopback Application gateway path, rejects redirects, keeps the
gateway token server-side, derives an idempotency key from the normalized brief,
and requires the gateway health contract before the UI advertises live mode.
The gateway and sidecar remain separate processes; the public UI never reads a
wallet key.

The header uses the transparent web asset at
`src/oathcast/assets/oathcast-mark.webp`. It is derived from the supplied square
artwork with near-black pixels softly removed, so the scarlet mark blends into
the page's pure-black background without a visible image tile.

To regenerate the asset from an approved source image, install the developer
extra with `python -m pip install -e '.[logo-tools]'`, then run
`python scripts/prepare_logo_asset.py SOURCE OUTPUT`. The generator keeps
red-dominant texture while removing neutral black and never overwrites the
source image.

The bounded JSON API is retained for the reviewed integration. When no runner
is configured, the endpoint returns `503` before reading or parsing a request
body. Demo mode opens the same narrow API with a clearly labeled local runner;
it does not create protocol traffic.

## Run locally

From the repository root:

```sh
python scripts/run_decision_ui.py --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787/>. To preview the judge-facing flow locally without
payment or network traffic:

```sh
OATHCAST_PUBLIC_DEMO=true python scripts/run_decision_ui.py \
  --host 127.0.0.1 --port 8787
```

Without that explicit flag, `POST /api/decision` returns `503` before reading
the body. A live deployment must provide `OATHCAST_APPLICATION_UI_TOKEN` and a
healthy private gateway; the launcher otherwise stays read-only.

Port `8787` is reserved for this public edge service. The separate local
Planning Desk pilot defaults to `8788` and must not be placed behind Caddy's
catch-all route.

The launcher never reads a wallet, manufactures x402 headers, creates payment
traffic, or uses a fixture as if it were live demand. A deployment that has a
reviewed real integration can construct the server in Python:

```python
from oathcast.decision_ui import TelegraphDecisionRunner, make_server

runner = TelegraphDecisionRunner(
    real_telegraph_decision_runner,
    routing_configured=True,
    payment_configured=True,
)

server = make_server(
    "127.0.0.1",
    8787,
    decision_runner=runner,
)
server.serve_forever()
```

The injected integration receives a validated `DecisionInput` and must return
a `DecisionResult` or the equivalent allow-listed mapping. A bare callable is
not sufficient to make the public API ready. Use the capability-bearing
`TelegraphDecisionRunner` only with a real callable and both
`routing_configured=True` and `payment_configured=True`. The integration owns
official Telegraph routing, payment authorization, settlement verification,
and secret handling. The public UI only receives allow-listed decision and
public Miner evidence fields.

## Staging deployment

The Docker image includes this launcher so the same immutable image can run a
loopback-bound UI container. For a judge-facing payment-free deployment, add
the explicit demo flag:

```sh
docker run -d --name oathcast-decision-ui --restart unless-stopped \
  -p 127.0.0.1:8787:8787 \
  -e OATHCAST_IMAGE_DIGEST=<image-id> \
  --health-cmd='python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8787/health\", timeout=3).read()"' \
  --health-interval=30s --health-timeout=5s --health-retries=3 \
  -e OATHCAST_PUBLIC_DEMO=true \
  oathcast-ui:<release> \
  python /app/scripts/run_decision_ui.py --host 0.0.0.0 --port 8787
```

The image-level Docker health check targets the Miner on port 8080, so a UI
container must override it as shown above. Bridge networking plus loopback-only
publishing keeps the UI private to the host while preventing its health check
from succeeding against the Miner or another host service. Caddy routes
`/healthz`, `/readyz`, exact registered `/predict`, and `/v1/*` to the Miner on
port 8080, and routes the public page, `/health`, `/status`, and `/api/decision`
to this UI on port 8787. Because `/predict` and `/v1/*` belong to the Miner at
the edge, use `/api/decision` for public fail-closed checks.

Publishing demo mode is not Track-3 demand. Its status is healthy for the
interactive demo, while `telegraph_routing_and_payment_configured` remains
false. A live route must be armed separately and only after the payment
boundary has passed its deployment checks.

## Endpoints

- `GET /` serves the read-only status and development-fixture UI.
- `GET /health` and `GET /status` return service readiness and non-secret
  release identity.
- `POST /api/decision` accepts the JSON request below.

After a reviewed runner is configured, the request body is capped at 16 KiB by
default. It must be UTF-8 JSON with
`Content-Type: application/json`, a `Content-Length`, no duplicate object keys,
and no unknown fields. A successful request requires `consent: true`.

```json
{
  "activity": "trail run",
  "location": "Lagos outdoor track",
  "latitude": 6.5244,
  "longitude": 3.3792,
  "local_datetime": "2026-08-17T16:00:00+01:00",
  "risk_threshold_percent": 30,
  "consent": true
}
```

`latitude` must be between -90 and 90; `longitude` between -180 and 180;
`risk_threshold_percent` between 0 and 100; and `local_datetime` must include
an explicit UTC offset. The API also accepts the documented short aliases
`lat`, `lon`, `local_date_time`, and `risk_threshold`, one alias per field.

On success the response includes `action` and `decision`, whose value is one
of `go`, `delay`, `relocate`, or `contingency`, plus a summary, rationale,
risk estimate, threshold, request ID, and `miner_evidence`. Each evidence
record is limited to a public Miner ID, status, optional percentage, public
evidence ID, Telegraph-route flag, and verified-payment flag. Raw Miner
responses, payment challenges, authorization headers, wallet addresses, and
private keys cannot be returned through this interface.

Validation errors return `422`; an oversized body returns `413`; missing or
invalid JSON metadata returns `400`, `411`, or `415` as appropriate. If real
Telegraph routing/payment is absent, the service returns `503` before reading
the request body. If a configured runner cannot return a usable decision, the
service returns `503` with a generic message and does not guess.

The page repeats the privacy boundary: send only details needed for one
decision, do not submit secrets or sensitive personal information, and treat a
decision as evidence for planning rather than a safety guarantee.

The default public page accepts no planning details. The input contract opens
in explicit demo mode or after a reviewed live integration is deliberately
enabled.
