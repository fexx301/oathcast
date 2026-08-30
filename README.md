# OathCast

[![CI](https://github.com/fexx301/oathcast/actions/workflows/ci.yml/badge.svg)](https://github.com/fexx301/oathcast/actions/workflows/ci.yml)

OathCast is a Telegraph hackathon project for time-locked, exact-hour weather
forecasts and later calibration evidence. One authenticated Miner is deployed
publicly on release `2026-08-30-hourly-v18`. The registered `GET /predict`
route and canonical `GET /v1/forecast/point` route share the same authenticated
forecast and receipt path. V18 supports the registered one-hour point contract
plus complete 2-to-168-hour weather windows. For multi-hour aware ISO/RFC3339
bounds it normalizes the start to the nearest whole UTC hour using half-up
rounding; the registered one-hour point behavior remains unchanged. It also
retains the additive, unregistered temperature compatibility route. The YAML
description re-registration on
2026-08-27 is documented separately and changed no runtime behavior. The
separate public UI exposes a truthful read-only status surface and client-only
development fixture. Its live decision endpoint remains degraded and returns
503 because no reviewed paid Application runner is configured.

The repository also contains provider adapters, a cross-Miner Application
scaffold, durable case and receipt stores, a development Script Author proxy,
an isolated TypeScript Solana x402 canary, and leakage-safe evaluation tools.
These components are tested, but they are not yet composed into a live paid
Application.

The project deliberately separates two scoring paths:

1. Telegraph's official scoring-module contract receives the question, ground
   truth, and raw Miner response. The required identity-blind WASM interface is
   `alloc`, `dealloc`, and `rank_answer`; the rank function receives six `i32`
   pointer/byte-length values and returns an `f32` score in `[0, 1]`.
   `breakdown_answer` has been deprecated and removed from the interface. The
   earlier cosine/BM25/length description came from pre-launch team guidance and
   is not assumed to be the current Canonical Script implementation.
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
The registered precipitation contract remains a one-hour UTC window. The
deployed v18 service also exposes an additive, unregistered temperature-only
contract for `/predict?lat=...&lon=...&forecast_hours=24&hourly=2t`:
`forecast_hours`
accepts `1..24`, the window starts at the next complete UTC hour, and the
response contains RFC3339 `time` values plus Kelvin `2t` values. The same
shape is available at `/v1/forecast/point`. The legacy
`/v1/forecast/window` path is not publicly exposed and returns 404. This work is
served additively since v8 and remains enabled in v18; it has not been
substituted into, uploaded as, or re-registered through the protected YAML.

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

The separate provider-evidence freshness workflow checks the live data branch
for collection gaps and overdue unresolved cases without calling the public
Miner. Its exact thresholds and failure semantics are documented in
`docs/provider-evidence-freshness.md`.

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

It exposes `/healthz`, `/readyz`, the registered `/predict` route, and the
canonical `/v1/forecast/point` route. The two forecast paths are exact aliases;
near-miss paths return 404 and both aliases share authentication, rate limits,
responses, and receipt identity. With
`OATHCAST_ENABLE_TEMPERATURE_WINDOW=true` (enabled by the deployed v18 service),
they also accept the additive, unregistered 1-to-24-hour
`forecast_hours`/`hourly=2t` temperature contract. The legacy
`/v1/forecast/window` route is not publicly exposed and returns 404. The service
uses Open-Meteo by default.
WeatherAPI/OpenWeather remain local adapter experiments and are blocked as
production failovers until their event semantics are validated.
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

The deployed v18 release retains v6's deliberately fail-closed replay contract.
New receipts freeze the exact digest-covered `public_response`; replay serves
those stored bytes rather than invoking a newer renderer. If a legacy receipt
lacks `public_response`, the current service returns HTTP 503
`receipt_store_unavailable` instead of synthesizing an answer that was never
part of the original receipt. Restore original response evidence rather than
backfilling it with current rendering code.

The fixtures are development-only. They are not Telegraph traffic and must not
be used to inflate the hackathon request count.

`validate_miner_drafts.py` is a project-owned pre-schema check only; it does
not replace Telegraph's official registration validator. `smoke_miner.py` is a
non-destructive release check covering health, readiness, auth rejection, an
authenticated forecast, receipt provenance, response fingerprints, and
release/request headers. `compare_release_replay.py` compares two smoke reports
and proves a stored event's receipt and public response survived a release
cutover unchanged.

The current public identity is release `2026-08-30-hourly-v18`, source
SHA-256 `5aca88c6890443bc086e0c078d3390eead10461fa734206fcc4937758d5e8b6b`,
and image digest
`sha256:d3c29fa9f274d520635b6c3ca413c383ba1de958840ed1eb3105aedceda7e859`.
The v18 manifest, candidate/public/post-restart smokes, schema replay, exact
wire replay, hourly boundary canary, receipt-backup metadata, v17 rollback
rehearsal, and linked runtime evidence are retained under
`artifacts/release-evidence/2026-08-30-hourly-v18-*`; stopped
`oathcast-v17-rollback-20260830` is the immediate Miner rollback target.
Full Python discovery passes `539/539`, focused canary tests pass `44/44`, and
the v18 evidence identity loader accepts the retained bundle.

`create_registration_draft.py` first requires that local check to pass, then
records an unsigned registration snapshot. It hashes the exact YAML bytes and
keeps price, pinned YAML URI, and fee address as explicit registration inputs;
it never validates through the live portal, connects a wallet, encodes calldata,
or submits `registerMiner`.

`public_canary.py` is the same no-state check intended to run from an
independent scheduler such as GitHub Actions; `.github/workflows/oathcast-canary.yml`
contains the no-cost scheduled path and expects the API key in a repository
secret. The local v18 canary configuration requires the 24-hour temperature
response and alias parity, and resolves release identity from the checked-in v18
evidence bundle. The remote GitHub schedule remains on its last pushed v11
configuration until a separately authorized commit and push.
See `docs/repository-and-canary.md` for the safe repository/secret
setup. `backup_receipts.py` uses SQLite's online backup API, runs integrity
checks on both copies, and verifies that the row count survives restoration.
Because it initializes the current receipt store, use raw read-only `sqlite3.backup`
instead when taking a pre-migration backup of a legacy live database.

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

`read_leaderboard.py` reads the Explorer's epoch-wide Miner leaderboard and
prints the score to beat in each declared Intent:

    PYTHONPATH=src python3 scripts/read_leaderboard.py
    PYTHONPATH=src python3 scripts/read_leaderboard.py --output snapshot.json

It is read-only and makes no payment or signature. The current API returns one
`{"epoch": ..., "intents": {...}}` snapshot using `score` and `rank` fields.
The reader fetches once, selects exact Intent keys locally, caps the response
body, enforces scores in `0..1`, and rejects the retired
`entries/avg_score/position` shape. Inactive Miners may still occupy a rank, so
the target is always the best *active* Miner. Returned entry count is reported
separately because rank gaps mean it is not necessarily a denominator.

The target is the **maximum** across declared Intents, not the mean: clearing the
hardest one clears the others, whereas an average sits between the real bars and
is wrong in both directions at once.

Its numbers are other Miners' Telegraph scores and are **not comparable** to the
local proxy in `benchmark_renderer.py`, which is
`0.8*overlap + 0.2*length_quality` and does not implement the official WASM or
Canonical Script. Both land in a similar numeric range, which is exactly why the
comparison is tempting; both tools print the warning next to their scores.

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

The reference evaluator remains a development proxy, while the Track 2 source
lives in `scoring-modules/oathcast-weather/`. It is a dependency-free Rust
`no_std` module with exported memory and the current required interface:
`alloc(i32) -> i32`, `dealloc(i32, i32)`, and
`rank_answer(6 x i32) -> f32`. The six inputs are UTF-8 pointer/byte-length
pairs for question, ground truth, and Miner answer, in that order. Blank answers
return exactly `0`, results are finite and clamped to `[0, 1]`, and the
standalone artifact has no host imports or start section. The rank path passes
the Rust tests (`40/40`) and OathCast's full Go/wazero ABI and adversarial
suite. Telegraph's unmodified official tester returned example score `0.8500`
for the earlier 42,798-byte registration `41` artifact; it is not vendored here
and has not been re-run against the current candidate. See the
[scoring-module README](scoring-modules/oathcast-weather/README.md) for the
build commands and safeguards. The current frozen rank-only artifact is 45,747
bytes with SHA-256
`80f08229391520dc32acbdeffd7588c9ac83ab8636bc4cccb35e894ecf7ff3e8` and
raw-byte Keccak-256
`0xd75c3f264a3dc553fa3508ca4e4fe23a25330c11aad5a1e0ba8f1f31fcad1c3d`.
Its fixture SHA-256 is
`c96960e6a5e0d0d410686bcf9a2c0dece48ec130e19403322355f19ca4096b0f`.
Two isolated clean builds on the same host are byte-identical, but the artifact
is **not** byte-identical across platforms: `darwin/arm64` and `linux/amd64` both
emit 45,747 bytes with different content under the same pinned `rustc 1.95.0` and
target. The registered bytes are the `darwin/arm64` build, since registration
identifies a specific hosted artifact rather than whatever a given machine
compiles; `release-evidence.json` records both digests under
`build.platform_digests`, and CI asserts the one for the platform it builds on.
Python discovery passes
`498/498`. Its only change from the previously registered build is a
probability-scan fix in `percent_probability`, which abandoned the scan at the
first `%` carrying no parseable number and so discarded a stated probability
later in the same answer; the fix is behaviour-preserving on the pre-existing
corpus, whose weakest synthetic margin is unchanged at `0.206250`. This
candidate is not the registered artifact, and it has not been uploaded, hosted,
signed, or evaluated by Telegraph. Registration `41` froze the earlier
42,798-byte build with SHA-256
`4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174` and
raw-byte Keccak-256
`0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`.
The user manually hosted those bytes on Dropbox and registered
them for `WEATHER_FORECAST` as registration `41`; an independent postflight
fetch reproduced the byte size and both artifact hashes. Registration `41`
reached champion comparison, establishing Stage 1 passage, then failed Stage 2
at `31/32` candidate ordering wins versus the champion's `32/32`.

An earlier frozen build also exported
`breakdown_answer(6 x i32) -> f32` while the portal output and public guide
appeared inconsistent. Telegraph's updated requirements now explicitly
deprecate and remove that export. Telegraph later reported that the underlying
August 14 node-log failure was `module[env] not instantiated`, not proof that
the validator enforced `breakdown_answer`. The old rejection records,
scalar-export test results, and hashes remain provenance for the historical
discrepancy, not current ABI evidence. Two clean Rust `1.95.0`
builds of that historical artifact were byte-identical; its retained metadata
records 16,318 bytes with SHA-256
`95895681d1e82bf01eab35f53af15cbfba8f459deba2b0dbc49e8dcbdeed9bf4` and
raw-byte Keccak-256
`0xa8cbc78d20b46b0aaba89002fdb585dc4f243dd192faff8e5ad271b4ef088b19`.
The historical bytes are not present in this workspace.

The machine-readable v7 record in
[`release-evidence.json`](scoring-modules/oathcast-weather/release-evidence.json)
records the 45,747-byte local candidate artifact, the registered 42,798-byte
registration `41` artifact, their local proxy evidence, the historical
16,292-byte registration `19` artifact, and the scalar-build metadata
separately. According to user-relayed Telegraph guidance,
the Stage 2 fixtures
cover factual paraphrase and lexical discrimination only, use a fixed `0.15`
margin floor for each of six near-miss cases, and do not compare the candidate's
aggregate margin directly with the champion's. The reported `0.60` metric is
Spearman rank correlation against the live champion's historical scores. The
candidate artifact passes 88 synthetic factual pairs with minimum margin
`0.206250`. Its synthetic ordinal Spearman is `0.956559`; that metric measures
`exact > good > bad` ordering on handcrafted cases and is not comparable to the
live candidate-versus-champion correlation. Predicate-family identity,
inverse `learned from` and `lost to` phrasing, parenthetical commas,
coordinated relation swaps, mixed explicit reversals, partial multi-relation
omissions, and mixed directed pairs now have Rust and fixture regressions.
Shared predicates, bounded anaphoric and passive ellipsis, multi-token and
suffix-bearing surname aliases, predicate-free completeness, comma and
semicolon gapping, subordinate parenthetical predicates, and novel extra claims
across punctuation are covered too. The exact artifact has no WASM import
section or host imports and does not require an `env` module.

The old compatibility export provisionally mirrored `rank_answer`. One portal
record surfaced a missing-export message, but Telegraph's node logs attribute
the underlying failure to `module[env] not instantiated`; another
six-`i32`/`f32` module loaded far enough to run the structural self-match check.
These records are superseded by the updated three-function guide and must not
be presented as current requirements. Live portal build
`D8HL6V9WUTFV9A7Ryk0W0`, page chunk
`_next/static/chunks/app/page-abd375eb1c96558e.js`, now targets
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and submits
`registerWasm(bytes32,string,string)` with selector `0xfe1e40f7`. The arguments
are the exact frozen WASM hash, the existing gateway URL, and
`WEATHER_FORECAST`, so the visible UI/direct-simulation path encodes the
canonical Intent. The two earlier delegated packets did not preserve that call;
the later corrected packet did.

Two earlier user-authorized transactions are retained as historical failed
attempts. Transaction
`0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`
emitted old-registry ID `5`, while transaction
`0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`
emitted old-registry ID `7`; both delegated packets targeted the obsolete
`0xac683...` registry and legacy `registerWasm(bytes32,string,string[])` ABI.

After the portal bundle was corrected and the complete wallet wrapper was
verified, transaction
`0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
confirmed on Base Sepolia. Its nested call targets the current registry
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` with selector `0xfe1e40f7`,
the exact frozen hash/CID, and Intent `WEATHER_FORECAST`. The current registry
emitted ID `7`, advanced `entityCount(2)` to `7`, and returns a non-empty
`getWasm(7)` containing the wallet, exact hash, gateway URL, and Intent. This
proves current-registry on-chain registration and Intent binding. It does not
prove that the artifact would pass validation. It remains the historical ID `7`
registration for the 16,292-byte artifact.

A later manual retry registered those same bytes from wallet
`0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278` as registration `19` through
Base Sepolia transaction
`0xa6bc6f653eec4a5c79acac4a6e747222d48fd257367c325cd0e6c0090d321e73`.
The registration used
`https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=sigr9vji&dl=1`
and the registered raw-byte Keccak-256
`0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
According to user-relayed Telegraph team confirmation, corroborated by the
candidate reaching champion comparison, Stage 1 passed; no separate Stage 1 API
field was retained. Stage 2 rejected OathCast at `31/32` comparable ordering
pairs while the champion won `32/32`. The API returned
candidate margin `0.31248063`, champion margin `0.37360683`, and
`historical_rows: 0`; the margins are diagnostic and are not directly compared
for promotion. The evidence and exact Telegraph record are in
[`docs/telegraph-track2-clarification-request.md`](docs/telegraph-track2-clarification-request.md).
That historical artifact is also pinned at
`ipfs://QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`. The portal re-fetched
the gateway bytes and reported the expected raw-byte Keccak-256, with only
`WEATHER_FORECAST` selected. The complete postflight record is
[`oathcast-weather-wasm-registration-postflight-2026-08-15T141838Z.json`](artifacts/registration-drafts/oathcast-weather-wasm-registration-postflight-2026-08-15T141838Z.json).
The corrected, pre-broadcast simulation preflight is
[`oathcast-weather-wasm-reregistration-preflight-2026-08-15T204924Z.json`](artifacts/registration-drafts/oathcast-weather-wasm-reregistration-preflight-2026-08-15T204924Z.json).
The second transaction postflight is
[`oathcast-weather-wasm-reregistration-postflight-2026-08-15T212134Z.json`](artifacts/registration-drafts/oathcast-weather-wasm-reregistration-postflight-2026-08-15T212134Z.json).
The successful current-registry postflight is
[`oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json`](artifacts/registration-drafts/oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json).
The 42,798-byte artifact was later registered manually as registration
`41` in Base Sepolia transaction
`0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`
at block `45613554` (`2026-08-17T19:36:36Z`). The delegated wallet was
`0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278`; the nested call targeted the
current registry with `registerWasm(bytes32,string,string)` and Intent
`WEATHER_FORECAST`, using the independently verified Dropbox URL
`https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=jwhbk80f&dl=1`.
Telegraph recorded Stage 1 passage and a Stage 2 rejection
at `31/32` candidate wins versus the champion's `32/32`. Candidate
margin/EvalScore was `0.37852418`, above the champion aggregate margin
`0.37360683`; the higher aggregate did not override the failed per-case
promotion rule. No further registration is authorized. Any future attempt
requires a fresh decoded wrapper/nested-call preflight and fresh explicit user
authorization.
The complete registration `41` evidence is
[`oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json`](artifacts/registration-drafts/oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json).

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

The retained paid-rehearsal boundary is `payment-canary/`. It uses the official
x402 fetch and SVM clients, caps Solana Devnet USDC at 0.01, and independently
checks settlement through Solana RPC. Preflight never reads a key; execution
requires both `--execute` and `SOLANA_PRIVATE_KEY`, sends at most one paid retry,
and emits only sanitized evidence. Its raw-IP HTTP compatibility mode is a
historical August 9 exception, not the current default. Current official docs
use an HTTPS dev node and offer Base Sepolia or Solana Devnet; for any separately
authorized request, validate the exact received `accepts[]` entry. The legacy
Python Base-Sepolia module remains regression coverage, not a live signer.

When deployed behind a host such as Railway, the service honors the host's
`PORT` value. Set `OATHCAST_MINER_API_KEY` to enable the Bearer protection that
matches the canonical YAML's `auth` block; keep that secret in the host secret
store, never in the repository.

## Miner registration

The three provider-specific YAMLs under miners/ remain adapter experiments. The
single `oathcast-weather` service is active in Telegraph's dispatcher after its
2026-08-27 Base Sepolia re-registration:

- keep the provider adapters behind the one public service and verify their event semantics;
- keep upstream API keys in the host secret store;
- retain the dedicated Telegraph credential validated by the portal until a documented rotation path replaces it;
- use the active pinned URI `https://gateway.pinata.cloud/ipfs/QmRSBA6ig48TVq15UWEwdiYq8HYr6woWRPam961j1q1oMu`, whose retrieved 3,018-byte representation has SHA-256 `0f66aa0679328afb0f53a0d83b846d2e8407ea062189814e455136121ac90d18`;
- on-chain registration ID is `245`; YAML routing ID `64173` is a separate identifier;
- transaction `0x43748dcaac584be466d32d96a45f4293816295579ffa17ee1c20ec4aa288184c` confirmed with receipt status `1`;
- `getMiner(245)`, the portal registration API, and the dispatcher match the approved fee address, `10000` micro-USDC price, and `WEATHER_FORECAST` intent;
- the earlier portal registration ID `78` is deregistered;
- dispatcher slug `oathcast-weather` is active at `GET /predict`.

The sanitized current post-submit record is
`artifacts/registration-drafts/oathcast-weather-cutoff-v2-registration-postflight-2026-08-27.json`.
The committed `miners/candidates/oathcast-weather-cutoff-v2.yaml` is the local
source snapshot used for the registration attempt. Its 5,389-byte raw hash is
`a639611848c484fa035f33f9ef336e0abb943b8ce3a908ae66aa4fa80f160679`, which is
not the portal's 3,018-byte canonicalized pin; the postflight record preserves
both identities.
Current official x402 docs offer Base Sepolia USDC and Solana Devnet USDC; the
received 402 challenge is authoritative for a specific request. These testnet
tokens have no monetary value. Registration price is supplied to the contract,
not embedded in the canonical service YAML. The explorer is at
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
