# OathCast gap register

Last reviewed: 2026-08-16

Current baseline: the public Miner runs `2026-08-16-route-v7`. The separate
decision UI provides a read-only status shell and development fixture while its
live API remains fail-closed without a runner. The v7 release adds the exact
registered `/predict` route at both Caddy and application boundaries. It passed
333 Python tests, the Go WASM suite, a disposable-container smoke, public
identity/write-readiness checks, exact `/predict`/canonical receipt parity, and
restart/replay persistence on the live receipt volume. OathCast is registered
on Base Sepolia as on-chain registration ID `78` and active in the Telegraph
dispatcher as routing ID `64173`, slug `oathcast-weather`.

This register separates work we can complete now that Miner registration is
live from work that still depends on external participants, independent
observations, or explicit payment/publication authorization. Telegraph's
updated guide requires `alloc`, `dealloc`, and `rank_answer` only.
`breakdown_answer` is deprecated and removed, so Track 2 no longer has a
breakdown-layout blocker before a registration candidate can be frozen.

## Fixed in this preparation slice

- Local Miner draft validation for a positive numeric routing candidate,
  canonical HTTPS, exact `WEATHER_FORECAST` semantics, endpoint query contracts,
  and signal mappings. Slug availability remains a live portal check; routing-ID
  collision inspection is conservative and is not the on-chain registration ID.
- Release provenance through a non-secret release ID, source-tree digest, and
  image digest fields exposed by `/healthz` and response headers.
- `/readyz` readiness endpoint and a reproducible public Miner smoke script
  covering health, readiness, authentication rejection, authenticated
  forecasting, receipt hashes, and request correlation.
- Request correlation from Application decision to Miner replies and, where
  supported, through HTTP/x402 request headers.
- Overlapping Bearer-key rotation with `OATHCAST_MINER_API_KEY` plus the
  temporary `OATHCAST_MINER_API_KEYS` set; raw secrets are never exposed by
  health or release output.
- In-process rate limiting with `429` and `Retry-After` behavior.
- Deployment build arguments for release identity and source provenance.
- Exact v3.2 deployment to the authorized EC2 staging host, with the public
  HTTPS path verified against the release ID, source digest, image digest,
  readiness, and authentication boundary. The authenticated forecast was
  replayed after a container restart with the same receipt hash. The old v3.1
  container remains stopped under a rollback name; the temporary EC2 Instance
  Connect rule was removed and the security group is back to ports 80/443.
- A written gap register and release evidence workflow, without claiming local
  fixtures or discovery traffic as hackathon demand.
- A receipt-store backup/integrity utility using SQLite's online backup API,
  including a restore-count check and tests that refuse accidental overwrite.
- An external-canary entry point plus a no-cost scheduled GitHub Actions
  workflow. The repository secret is configured, missing-secret runs fail
  visibly, and the workflow is pinned to the deployed v7 identity.
- The live registered-path failure is fixed in release
  `2026-08-16-route-v7`: Caddy routes exact `/predict`, the Miner accepts only
  `/predict` and `/v1/forecast/point`, both paths share auth/rate limits and one
  receipt, and the smoke test rejects empty or invalid answers. Public HTTPS,
  exact v6-to-v7 replay, restart persistence, SQLite integrity, and sanitized
  logs all passed. The previous leaderboard zero remains historical; only a
  fresh Telegraph scoring epoch can establish the corrected live score.
- A demo ablation mode that runs the Application with the owned Miner disabled
  and asserts that valid external responses still drive the decision.
- Staging API-key rotation: the old/new overlap was exercised through public
  HTTPS, the container was recreated to load the changed env file, the retired
  key returned `401`, the active key returned `200`, and health/readiness stayed
  `200`. The secret itself is absent from repository evidence.
- Append-only local demand provenance with request correlation, transport,
  payment-evidence, fixture/source, response-status, and integrity-hash fields.
  The conservative local-candidate predicate excludes fixtures, direct HTTP,
  unpaid/preflight traffic, and responses whose settlement header is not
  independently verified; it never presents the result as Telegraph's official
  count.
- Typed protocol result envelopes that preserve response hashes, x402 artifact
  hashes, challenge/deadline metadata, registry snapshot provenance, optional
  Signal Receipt identity, and explicit settlement verification state.
- Immutable per-generation Miner registration declarations with raw-YAML
  SHA-256, supported Intent strings, explicit integer micro-USDC price, chain
  profile, source authority, and confirmation state.
- Demand-ledger SQLite triggers and a full event-hash verification check.
- A human-readable Markdown Application evidence shell that presents the
  question, Miner comparison, external influence, later resolution, durable
  hashes, raw responses, and owned-Miner-disabled ablation without claiming
  live protocol evidence.
- A registration dry-run generator and snapshot for the canonical YAML. It
  records the candidate ID, raw-YAML digest/bytes32, exact Intent, integer
  micro-USDC price, live Base Sepolia contract signature, missing operator
  inputs, and explicit non-submission claims.
- Confirmed Miner registration and activation: transaction
  `0x937d45d8108b905a551608707755e47899a41046436038a315a859d2f497b5d2`
  emitted on-chain registration ID `78`; `getMiner(78)`, the portal registration
  API, and dispatcher activation all match the frozen YAML, fee address,
  `10000` micro-USDC price, and `WEATHER_FORECAST` intent. The sanitized record
  is `artifacts/registration-drafts/oathcast-weather-registration-confirmation-2026-08-13T1940Z.json`.
- Human and JSON Explorer evidence templates that separate local receipts,
  payment artifacts, settlement verification, and future manual Explorer
  confirmation.
- Repository/canary setup documentation, secret/database ignore rules, and
  least-privilege/concurrency protections on the scheduled GitHub workflow.
- A deterministic local Script Author adversarial benchmark with ten fixed
  cases, baseline-versus-candidate behavior metrics, fixture hashing, and a
  report that rejects wrong outcomes, malformed/overlong responses,
  wrong-window answers, contradictions, and keyword stuffing. It is explicitly
  labeled development-only and does not claim Telegraph's Canonical Script.
- A standalone OathCast Track 2 scorer under
  `scoring-modules/oathcast-weather/`: pinned Rust `1.95.0`, dependency-free
  `no_std` WASM, exported bounded memory, checked allocation/input validation,
  and the published `alloc`/`dealloc`/`rank_answer` ABI. It handles generic
  weather semantics, probability/polarity consistency, numeric facts, UTC time
  windows, JSON envelopes, stuffing, and concision. Rust tests, the local wazero
  ABI/adversarial suite, and deterministic repeated `rank_answer` calls pass.
  Telegraph's unmodified tester example also returns `0.8500`. An earlier
  validator-observed scalar `breakdown_answer` export and its tests are now
  historical only: the updated guide deprecates and removes that function. The
  current rank-only build is 16,292 bytes with SHA-256
  `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af`
  and raw-byte Keccak-256
  `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
  Two isolated clean builds were byte-identical, and the v4 machine-readable
  release record separates this candidate from the historical scalar-build
  metadata. The exact candidate is uploaded and portal-verified. Two earlier
  transactions remain historical because their delegated packets targeted the
  obsolete registry. Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  registered the exact hash/CID and `WEATHER_FORECAST` on the current registry
  as ID `7`; entity count `7` and matching non-empty `getWasm(7)` prove on-chain
  registration and Intent binding. Dashboard indexing, validator Stage 1, the
  reported `0.60` result, and Stage 2 remain unobserved.
- A leakage-safe chronological provider backtest with timestamped synthetic
  cases, a frozen warmup/holdout split, resolution-aware prior-only selection,
  simultaneous-timestamp batching, common-case Brier, coverage, and explicit
  end-to-end utility semantics. It is methodology evidence only and does not
  establish live provider performance.
- A local Planning Desk intake surface with privacy-minimal fields, stable
  hashed request IDs, an idempotent SQLite queue, and explicit no-Telegraph/no-
  payment status. It prepares legitimate pilot demand without claiming usage.
- A file-backed observation ingestion boundary with raw-export hashing,
  duplicate-event rejection, exact observation contracts, and an explicit
  operator-verification requirement for source independence.
- A pilot runbook, evidence-led X drafts, and a track/submission checklist
  that distinguish completed local preparation from live protocol gates.

## Actionable next, not platform-blocked

- Keep the deployed v7 Miner and its recurring canary pinned to the release,
  source, and image identities; retain stopped
  `oathcast-v6-rollback-20260816` until v7 has accumulated an adequate stable
  operating window.
- Run and verify the new provider-evidence freshness workflow. It alerts
  separately on stale collection and stale resolution; workflow code is local
  until the branch is pushed and manually dispatched once.
- Establish an independently sourced observation pipeline and resolve the
  accumulated provider cases before using them for reliability weights or
  WeatherAPI failover.
- Monitor `oathcast-weather` health, authenticated dispatcher traffic, request
  counts, and the `WEATHER_FORECAST` leaderboard. Telegraph confirmed the old
  zero came from its scorer receiving an empty answer after `/predict` returned
  404. Registration, activation, and the route correction are complete; a
  fresh non-empty scorer result is still pending.
- Preserve the superseded scalar artifact metadata and hashes as historical
  provenance only; the old bytes are not present in this workspace. Do not treat
  the historical scalar export or old `whitelistedUrls` ABI as a current
  requirement. The 16,292-byte rank-only artifact and release record are frozen
  at `ipfs://QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`; the existing CID
  was re-fetched byte-identically. Live portal build
  `D8HL6V9WUTFV9A7Ryk0W0`, chunk
  `_next/static/chunks/app/page-abd375eb1c96558e.js`, targets
  `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and encodes
  `registerWasm(bytes32,string,string)` with selector `0xfe1e40f7` and arguments
  exact hash, existing gateway URL, and `WEATHER_FORECAST`. Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  used that exact nested call and created current-registry ID `7`.
  `entityCount(2) == 7`, and `getWasm(7)` contains the expected wallet, hash,
  URL, and Intent. The immediate gate is Dashboard/validator indexing and an
  observable Stage 1 result. Telegraph later asked the user to try
  re-registering after its indexing PR merged but the dashboard remained empty;
  no new transaction, decoded preflight, or authorization exists. Any retry
  requires a fully decoded packet and fresh explicit authorization.
- Write the payment-boundary ADR and threat model, then implement one private,
  authenticated, allowlisted, transactionally budgeted Solana request with a
  durable payment journal. Do not enable the public decision endpoint first.

## Blocked on authorization, external evidence, or remaining documentation

- Telegraph reported that the breakdown-related rejection, Intent binding, and
  registry mismatch are fixed. The August 14 portal/API response remains
  historical surfaced output saying `missing required export
  "breakdown_answer"`, while Telegraph's node logs identify the underlying cause
  as `module[env] not instantiated`. The current candidate has no import section.
  Current-registry ID `7` is proven on-chain, but Stage 1 acceptance remains
  unobserved.
- Transactions `0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`
  and `0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`
  remain historical old-registry packets. The first emitted ID `5`; the second
  emitted ID `7` but targeted the obsolete registry and legacy ABI. The old
  registry has empty `getWasm(5)`/`getWasm(7)` and `entityCount(2) == 0`.
- Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  has receipt status `1`; its decoded nested call targets current registry
  `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`, selector `0xfe1e40f7`, exact
  hash/CID, and `WEATHER_FORECAST`. The registry emitted ID `7`, now reports
  `entityCount(2) == 7`, and returns matching non-empty `getWasm(7)`. The
  Dashboard still reports `wasm_count: 0`, so indexing, Stage 1, Stage 2, and
  the reported `0.60` result remain unobserved. Telegraph's subsequent request
  to re-register is user-relayed guidance after its indexing PR merged, not a
  new transaction authorization.
- Ahmed confirmed that re-registration is intended and reported that the
  candidate must score at least `0.60` on the Intent. The official example is
  `0.8500`, while the weakest known valid local paraphrase is `0.5875`. Because
  the validator replay corpus and aggregation formula are not independently
  documented, this is hidden aggregate risk and does not prove threshold failure
  or success. Do not infer validator activation from the simulation or local
  scores. All prior authorizations are consumed. Any further attempt requires a
  fresh complete wrapper/nested-call decode and fresh explicit authorization
  before confirmation.
- Official ground-truth source, deadline/finality, revision, and extraction
  rules.
- Ahmed clarified that Application agents may call Miners directly through
  Telegraph; Engine auto-routing is optional, and those Telegraph-routed calls
  count. Payment is required for every request flowing through Telegraph via
  x402 or another supported method. The remaining demand requirement is to
  generate genuine Track 3 Application usage and avoid artificial inflation;
  do not reopen the already-answered Engine-routing question.
- Continued availability of independently operated Miners in the same Intent.
  Five active `WEATHER_FORECAST` Miners, including OathCast, were observed on
  2026-08-13, so the three-Miner condition was met at that snapshot. It must be
  rechecked near submission, and the separate 100-real-request condition remains
  unmet.
- The Solana x402 canary has completed one independently RPC-verified devnet
  payment, but it is an isolated CLI. A production Application boundary still
  needs authenticated principals, transactional budgets, idempotency binding,
  durable ambiguous-outcome reconciliation, and capture of the paid Miner body.

## Non-goals

- No custom token, mainnet wallet, real-money payment, request farming, or
  fabricated Explorer evidence.
- No registration of the provider-adapter drafts as separate ecosystem Miners.
- No claim that local fixtures, read-only discovery, or direct upstream calls
  are qualifying Track 3 demand.
