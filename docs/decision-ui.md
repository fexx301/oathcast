# Public Track-3 decision UI

`src/oathcast/decision_ui.py` is a small Python standard-library HTTP service
for one human outdoor decision. It serves an accessible responsive page and a
bounded JSON API. It has no external browser runtime dependency and does not
call a weather provider or Telegraph by itself.

## Run locally

From the repository root:

```sh
python scripts/run_decision_ui.py --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787/>. The page and health endpoints are available, but
`POST /api/decision` intentionally returns `503` until a real Telegraph-backed
decision runner is injected.

The launcher never reads a wallet, manufactures x402 headers, creates payment
traffic, or uses a fixture as if it were live demand. A deployment that has a
reviewed real integration can construct the server in Python:

```python
from oathcast.decision_ui import make_server

server = make_server(
    "127.0.0.1",
    8787,
    decision_runner=real_telegraph_decision_runner,
)
server.serve_forever()
```

The injected callable receives a validated `DecisionInput` and must return a
`DecisionResult` or the equivalent allow-listed mapping. The callable owns
official Telegraph routing, payment authorization, settlement verification,
and any secret handling. The public UI only receives the safe decision and
public Miner evidence fields. For the explicit fail-closed readiness contract,
`TelegraphDecisionRunner` can be used with a real callable and both
`routing_configured=True` and `payment_configured=True`.

## Staging deployment

The Docker image includes this launcher so the same immutable image can run two
separate loopback-bound containers:

```sh
docker run -d --name oathcast-decision-ui --restart unless-stopped \
  --network host oathcast:<release> \
  python /app/scripts/run_decision_ui.py --host 127.0.0.1 --port 8787
```

Caddy routes `/healthz`, `/readyz`, and `/v1/*` to the Miner on port 8080 and
routes the public page, `/health`, `/status`, and `/api/decision` to this UI on
port 8787. Publishing the fail-closed shell is not Track-3 demand: its status
must remain degraded and its decision endpoint must return 503 until the
reviewed live Telegraph payment runner is injected.

## Endpoints

- `GET /` serves the UI.
- `GET /health` and `GET /status` return service readiness without secrets.
- `POST /api/decision` accepts the JSON request below.

The request body is capped at 16 KiB by default. It must be UTF-8 JSON with
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
Telegraph routing/payment is absent or the runner cannot return a usable
decision, the service returns `503` with a generic message and does not guess.

The page repeats the privacy boundary: send only details needed for one
decision, do not submit secrets or sensitive personal information, and treat a
decision as evidence for planning rather than a safety guarantee.
