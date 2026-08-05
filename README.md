# OathCast preparation spike

This folder contains the first implementation spike for OathCast: three
provider adapters, one shared forecast contract, deterministic public text
rendering, local development fixtures, and a leakage-safe Brier benchmark.

It now also contains the first vertical-slice scaffolding: a standard-library
HTTP Miner service, an Application-side cross-Miner router, registry capability
filtering, and a development reference evaluator for the future Script Author
input contract.

The project deliberately separates two scoring paths:

1. Telegraph's current Script Author path receives the question, ground truth,
   and raw Miner response, then uses a 0–1 semantic composite based on cosine
   similarity, BM25 overlap, and length quality.
2. OathCast's local domain benchmark evaluates calibrated binary probabilities
   with Brier score, Brier skill against frozen climatology, and coverage.

Brier is not being presented as Telegraph's current protocol metric.

## Provider boundary

The intended flow is:

provider response → provider adapter → canonical JSON → short public content
renderer / local Brier evaluator

The Application path is separate:

registry capabilities → OathCast + independent Miners → raw responses + provenance
→ live decision → post-resolution evaluation

The public response is intentionally small:

    {"content":"At Lagos, the probability of measurable precipitation > 0.1 mm from 2026-08-17T15:00:00Z to 2026-08-17T16:00:00Z is 70%.","probability":0.7}

Provider model names, retrieval timestamps, raw hashes, and native event
definitions remain internal. All three adapters require an exact native hourly
point; they refuse to choose a nearest point or invent a max/mean aggregation.
The first spike accepts one-hour UTC windows only.

Open-Meteo is the only adapter currently marked as a documented event match.
WeatherAPI and OpenWeather are intentionally marked unverified until their
probability semantics pass a held-out chronological test against the same
observation definition.

## Run the local checks

From the OathCast directory:

    PYTHONPATH=src python3 -m unittest discover -s tests -v
    PYTHONPATH=src python3 scripts/score_fixtures.py
    PYTHONPATH=src python3 scripts/backtest_providers.py \
      --output artifacts/benchmarks/chronological-provider-backtest.json
    PYTHONPATH=src python3 scripts/evaluate_fixtures.py
    PYTHONPATH=src python3 scripts/benchmark_script_author.py \
      --output artifacts/benchmarks/script-author-adversarial-report.json
    PYTHONPATH=src python3 scripts/validate_miner_drafts.py
    PYTHONPATH=src python3 scripts/create_release_manifest.py --release-id local-check
    PYTHONPATH=src python3 scripts/demo_application.py
    PYTHONPATH=src python3 scripts/demo_application.py --compare-owned-fallback
    PYTHONPATH=src python3 scripts/demo_application.py \
      --compare-owned-fallback --format markdown \
      --output artifacts/application-demo/oathcast-application-demo.md
    PYTHONPATH=src python3 scripts/application_pilot.py \
      --host 127.0.0.1 --port 8787 --database state/pilot.sqlite3
    PYTHONPATH=src python3 scripts/validate_observations.py \
      fixtures/observation_export.json
    PYTHONPATH=src python3 scripts/create_registration_draft.py
    PYTHONPATH=src python3 scripts/public_canary.py --skip-authenticated
    PYTHONPATH=src python3 scripts/backup_receipts.py \
      --database state/receipts.sqlite3 \
      --output state/backups/receipts-backup.sqlite3
    PYTHONPATH=src python3 scripts/discover_live_miners.py
    PYTHONPATH=src python3 scripts/inspect_demand.py --db state/demand.sqlite3
    PYTHONPATH=src python3 scripts/preflight_miner.py --miner-id 18 --endpoint predict

To run the local Miner HTTP service:

    OATHCAST_REQUIRE_AUTH=false PYTHONPATH=src python3 -m oathcast.service

It exposes /healthz and /v1/forecast/point. The service uses Open-Meteo by
default. WeatherAPI/OpenWeather remain local adapter experiments and are
blocked as production failovers until their event semantics are validated.
The service rejects new forecasts at or after the declared cutoff, persists an
immutable SQLite receipt keyed by `event_id`, and returns the receipt hash in
`X-OathCast-Receipt-SHA256`. For local unauthenticated development, set
`OATHCAST_REQUIRE_AUTH=false`; the deployed service must provision
`OATHCAST_MINER_API_KEY`. During rotation, a comma-separated
`OATHCAST_MINER_API_KEYS` overlap is accepted temporarily. `/readyz` verifies
that the receipt store and authentication configuration are present, and the
service emits non-secret release and request IDs for evidence.

The fixtures are development-only. They are not Telegraph traffic and must not
be used to inflate the hackathon request count.

`validate_miner_drafts.py` is a project-owned pre-schema check only; it does
not replace Telegraph's official registration validator. `smoke_miner.py` is a
non-destructive release check covering health, readiness, auth rejection, an
authenticated forecast, receipt provenance, and release/request headers.

`public_canary.py` is the same no-state check intended to run from an
independent scheduler such as GitHub Actions; `.github/workflows/oathcast-canary.yml`
contains the no-cost scheduled path and expects the API key in a repository
secret. See `docs/repository-and-canary.md` for the safe repository/secret
setup. `backup_receipts.py` uses SQLite's online backup API, runs integrity
checks on both copies, and verifies that the row count survives restoration.
The `--compare-owned-fallback` demo mode proves the Application can make a
decision from external Miners when the owned OathCast Miner is disabled.

`application_pilot.py` serves the local Planning Desk intake surface. It queues
privacy-minimal planning questions in SQLite and makes no Miner, Telegraph, or
payment call. See `docs/application-pilot.md` before sharing it with pilot
users. `FileObservationSource` and `validate_observations.py` provide the
provider-neutral ingestion boundary for a later independent observation
export; the bundled observation file is a development fixture and its
independence is not asserted.

`discover_live_miners.py` makes a read-only request to Telegraph's public
`/miner-dispatcher/integrations` endpoint and prints active external weather
capabilities. It does not sign a wallet challenge, make a paid request, or
claim demand. Pass `--output path.json` to retain a timestamped, hashed
observation snapshot. Run it again immediately before live routing because the
registry is volatile.

`DemandLedger` and `inspect_demand.py` retain append-only local provenance for
Application requests. A conservative local candidate requires an Application
correlation ID, Telegraph transport, a successful response, and settlement
evidence; fixtures, direct HTTP, unpaid preflights, and unverified payment
responses are excluded. The ledger intentionally reports no official count:
only Telegraph's node or Explorer can establish qualifying hackathon traffic.

`preflight_miner.py` uses the shared development question to make one unpaid
request and print the 402 challenge. It never supplies a signer or
`PAYMENT-SIGNATURE`; use it to verify a live target before requesting payment
authorization.

The reference evaluator is a contract/proxy test only. Ahmed confirmed that
Telegraph has not released the public WASM boilerplates or harness yet; replace
the proxy with the official module and rerun its tests when released.

`benchmark_script_author.py` compares that baseline proxy with a transparent
development candidate across good, wrong-outcome, malformed, overlong,
wrong-window, contradictory, and keyword-stuffed responses. Its fixed corpus
and JSON report are regression evidence for local robustness only; neither the
candidate score nor the report is Telegraph's Canonical Script, a WASM result,
or qualifying hackathon traffic. The Brier benchmark remains a separate lane.

`backtest_providers.py` runs the Brier fixture in chronological order with a
frozen warmup/holdout split. Provider history is available to the prequential
selector only when `resolved_at <= issued_at`; equal-issued-time cases are
batched, unresolved events are excluded, and missing/late/invalid/abstained
attempts contribute zero end-to-end utility rather than zero Brier loss. The
report includes conditional Brier, coverage, common-case Brier, and the
prior-only selection trace. The timestamped fixture is synthetic development
evidence, not real provider performance or Telegraph traffic.

The paid boundary is in `src/oathcast/payment.py`. It understands Base Sepolia
USDC x402 challenge/retry semantics but requires a real injected signer; it
never fabricates `PAYMENT-SIGNATURE` values. The signer receives an immutable
validated authorization containing exactly one approved challenge option.
Signed clients also require an Application-side SQLite payment journal with
durable `RESERVED -> SUBMITTED -> SETTLED/UNKNOWN` state, spend caps, restart-
persistent duplicate blocking, and no private keys or replayable proofs.
`preflight_miner()` performs only the unpaid request and never reserves a
journal entry. Live signing remains blocked until an official HTTPS dispatcher,
real signer/SDK, funded Base Sepolia wallet, and settlement/reconciliation
evidence are available. If a challenge supplies a resource URL, it must match
the exact request URL; the current live challenge shape omits that field, so
target and endpoint allowlists remain mandatory.

When deployed behind a host such as Railway, the service honors the host's
`PORT` value. Set `OATHCAST_MINER_API_KEY` to enable the Bearer protection that
matches the canonical YAML's `auth` block; keep that secret in the host secret
store, never in the repository.

## Miner drafts

The three provider-specific YAMLs under miners/ remain adapter experiments. The
recommended registration unit is the single oathcast-weather.yaml draft, which
wraps those adapters behind one public Miner service. Before registration:

- keep the provider adapters behind the one public service and verify their event semantics;
- replace the canonical draft's `id` and `base_url` with registration values;
- keep upstream API keys in the host secret store;
- validate the canonical YAML against a running Telegraph node and the released Intent/harness;
- define the canonical request price; the current Discord clarification sets the minimum at 0.01 USDC.

The hackathon currently uses Base Sepolia USDC. Each Miner YAML must define a
price, and the minimum allowed price is 0.01 USDC. The explorer is at
https://explorer.telegraphprotocol.com/.

## Official provider references

- Open-Meteo: https://open-meteo.com/en/docs
- WeatherAPI: https://www.weatherapi.com/docs/
- OpenWeather One Call 3.0: https://openweathermap.org/api/one-call-3
- Telegraph Miner YAML: https://docs.telegraphprotocol.com/docs/miners/yaml-config

Read handoff.md before making strategic or protocol decisions.
