# OathCast preparation spike

[![CI](https://github.com/fexx301/oathcast/actions/workflows/ci.yml/badge.svg)](https://github.com/fexx301/oathcast/actions/workflows/ci.yml)

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

    {"content":"Measurable precipitation > 0.1 mm is likely to occur in Lagos in the hour from 15:00 to 16:00 UTC on 17 August 2026. Probability: 70%.","probability":0.7}

The wording is not incidental. Miner performance is scored on the **response
text**, so `src/oathcast/render.py` is a scored surface rather than a
presentation detail. The current renderer (`semantic_text_v2`) uses IPCC AR6
calibrated uncertainty language and readable UTC clock time; the previous
version, which emitted ISO-8601 stamps, measured materially worse on local
proxies because those stamps tokenize into fragments that match nothing a
resolution text would contain. The rationale, measurements, and limitations are
in `docs/renderer-experiment.md`, and the earlier renderer is retained as
`render_forecast_content_v1` so the change stays measurable and reversible.

An unresolved forecast never asserts a resolved outcome — the renderer will not
emit "Yes"/"No" or claim an event did or did not happen, at any probability.

Provider model names, retrieval timestamps, raw hashes, and native event
definitions remain internal. All three adapters require an exact native hourly
point; they refuse to choose a nearest point or invent a max/mean aggregation.
The first spike accepts one-hour UTC windows only.

Open-Meteo is the only adapter currently marked as a documented event match.
WeatherAPI and OpenWeather are intentionally marked unverified until their
probability semantics pass a held-out chronological test against the same
observation definition.

`collect_provider_pairs.py` gathers the evidence for that test. Neither free
provider tier sells a historical *forecast* archive, so paired cases can only
accumulate forward:

    set -a; source .secrets/weatherapi.env; set +a
    PYTHONPATH=src python3 scripts/collect_provider_pairs.py --mode collect
    PYTHONPATH=src python3 scripts/collect_provider_pairs.py --mode resolve \
      --observations fixtures/observation_export.json

`collect` appends one unresolved case per location at a fixed lead time;
`resolve` fills outcomes only after a window has closed, and is idempotent. The
key is read from `WEATHERAPI_KEY` in the environment and never accepted as an
argument; every error path scrubs it, because urllib copies the request URL —
which carries the key as a query parameter — into connection-failure messages.
A provider that fails is recorded with `status: "missing"` rather than dropped,
so availability differences cannot bias the comparison. Datasets are validated
through the real backtest loader before replacing the previous file.

The baseline in `fixtures/collection_locations.json` is a frozen 0.2305,
derived from 7,440 ERA5 August hours (2015-2024) at the Lagos point using the
same 0.1 mm threshold the question asks about. That reanalysis is retrieved
through Open-Meteo, so it is not fully independent of the `open_meteo`
provider; the file records that limitation alongside the value.

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
    PYTHONPATH=src python3 scripts/read_leaderboard.py
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
that the receipt store and authentication configuration are present, reports
receipt-store capacity, and the service emits non-secret release and request IDs
for evidence.

The receipt store is capacity-bounded rather than pruned: growth is capped by
refusing **new** writes (HTTP 507, and `/readyz` reports not-ready), never by
deleting receipts, because receipts exist to be replayed after cutoff. A replay
of an already-issued receipt always succeeds, even at capacity. Defaults are
200,000 rows and 512 MiB, overridable with `OATHCAST_RECEIPT_MAX_ROWS` and
`OATHCAST_RECEIPT_MAX_BYTES` (`none` disables a cap).

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

`anchor_receipt_head.py` makes the receipt set tamper-**evident**. Immutability
triggers stop SQL-level mutation, but anyone with the database file can rewrite a
receipt *and* recompute its digest, producing a self-consistent forgery. The
script publishes a hash-chain head over all receipts and re-verifies a published
anchor later, exiting non-zero if the anchored prefix was altered:

    PYTHONPATH=src python3 scripts/anchor_receipt_head.py \
      --database state/receipts.sqlite3 \
      --output artifacts/receipt-anchors/anchor-2026-08-10.json
    PYTHONPATH=src python3 scripts/anchor_receipt_head.py \
      --database state/receipts.sqlite3 \
      --verify artifacts/receipt-anchors/anchor-2026-08-10.json

Because the head is a chain, an anchor published at N receipts stays verifiable
as the store grows. Its value depends entirely on being published somewhere
OathCast cannot rewrite — an anchor kept only in this repository proves nothing
against someone who can also edit this repository. The output contains digests,
counts, and timestamps only; never receipt contents.
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

`read_leaderboard.py` reads the Explorer's **per-Intent** Miner leaderboard and
prints the score to beat in each declared Intent:

    PYTHONPATH=src python3 scripts/read_leaderboard.py
    PYTHONPATH=src python3 scripts/read_leaderboard.py --output snapshot.json

It is read-only and makes no payment or signature. Four behaviours of that API
are enforced in `src/oathcast/leaderboard.py` rather than left to the operator,
because each produces a plausible wrong answer instead of an error. **`?intent=`
is the only filter that works** — `intent_type=` and `epoch=` are accepted and
silently ignored, returning the full board — so a **negative control runs first**
and the read is refused unless a nonsense Intent returns zero entries. **`avg_score`
is per-Intent but `total_requests_served` is not** (it is the Miner's cross-Intent
total, identical in every per-Intent view), so per-Intent reads drop it. **`position`
includes inactive Miners** — a `superseded` Miner can hold position 1 — so the
target is the best *active* Miner and no position is printed without its
activation status. And a rank is always reported with its population, because
the same score read as "6/17" and "4/41" is the denominator moving, not the score.

The target is the **maximum** across declared Intents, not the mean: clearing the
hardest one clears the others, whereas an average sits between the real bars and
is wrong in both directions at once.

Its numbers are other Miners' Telegraph scores and are **not comparable** to the
local proxy in `benchmark_renderer.py`, which is `0.8*overlap +
0.2*length_quality` rather than Telegraph's cosine + BM25 + length composite.
Both land in 0.4–0.7, which is exactly why the comparison is tempting; both
tools now print that warning next to their scores.

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

The current paid boundary is `payment-canary/`. It uses the official x402 fetch
and SVM clients, pins Solana Devnet, Circle's Devnet USDC mint, the 0.01-USDC
cap, recipient, fee payer, Miner route, and endpoint, and independently checks
the settlement transaction through Solana RPC. Preflight never reads a key.
Execution requires both `--execute` and `SOLANA_PRIVATE_KEY`, sends at most one
paid retry, and emits only sanitized evidence. Telegraph's temporary HTTP
dispatcher and prefix-free canonical resource are accepted only by an explicit
flag pinned to the current live authority; redirects and every other HTTP host
remain blocked. `src/oathcast/payment.py` is retained as a legacy Base-Sepolia
policy/journal regression harness and is not the current live signer.

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

Miner registration remains a separate Base-Sepolia portal/contract concern.
The current live consumption challenge uses Solana Devnet USDC; these testnet
tokens have no monetary value. Each Miner YAML must still define a price, and
the minimum allowed price is 0.01 USDC. The explorer is at
https://explorer.telegraphprotocol.com/.

## Official provider references

- Open-Meteo: https://open-meteo.com/en/docs
- WeatherAPI: https://www.weatherapi.com/docs/
- OpenWeather One Call 3.0: https://openweathermap.org/api/one-call-3
- Telegraph Miner YAML: https://docs.telegraphprotocol.com/docs/miners/yaml-config

`docs/engineering-log.md` records the decisions where the obvious answer turned
out to be wrong — a green CI canary whose only real step had never executed, two
filesystem probes that both reported a database was writable when it was not, a
port scan that passed because the binary was missing, and why the receipt chain
hashes a recomputed digest rather than the one the receipt reports about itself.

Strategic and protocol context is kept in local-only working notes that are not
part of this repository.
