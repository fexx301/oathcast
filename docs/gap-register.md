# OathCast gap register

Last reviewed: 2026-08-14

Current baseline: the public Miner runs `2026-08-12-hardened-v6`. The separate
decision UI provides a read-only status shell and development fixture while its
live API remains fail-closed without a runner. The v6 release passed the full
Python suite, nine payment-canary tests, a disposable-container smoke, public
identity/write-readiness checks, and restart/replay persistence on the live
receipt volume. OathCast is registered on Base Sepolia as on-chain registration
ID `78` and active in the Telegraph dispatcher as routing ID `64173`, slug
`oathcast-weather`.

This register separates work we can complete now that Miner registration is
live from work that still depends on external participants, independent
observations, or explicit payment/publication authorization. Telegraph's
official scoring-module ABI and tester are now public, so Track 2 implementation
is no longer a platform blocker.

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
  visibly, and the workflow is pinned to the deployed v6 identity.
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
  windows, JSON envelopes, stuffing, and concision. Rust tests, the local
  wazero ABI/adversarial suite, 5,000 deterministic repeated calls, and
  Telegraph's unmodified tester pass; the official tester example scores
  `0.8500`. Two clean Rust `1.95.0` release builds were byte-identical; the
  frozen artifact is 16,292 bytes with SHA-256
  `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af`
  and raw-byte Keccak-256
  `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
  No upload or registration has occurred.
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

- Keep the deployed v6 Miner and its recurring canary pinned to the release,
  source, and image identities; retain the stopped v5 rollback container until
  v6 has accumulated an adequate stable operating window.
- Run and verify the new provider-evidence freshness workflow. It alerts
  separately on stale collection and stale resolution; workflow code is local
  until the branch is pushed and manually dispatched once.
- Establish an independently sourced observation pipeline and resolve the
  accumulated provider cases before using them for reliability weights or
  WeatherAPI failover.
- Monitor `oathcast-weather` health, authenticated dispatcher traffic, request
  counts, and the `WEATHER_FORECAST` leaderboard. Registration and catalog
  activation are complete, but no paid request or leaderboard score has yet
  been claimed.
- Preserve the byte-identical scorer artifact and its recorded size, SHA-256,
  and independently checked raw-byte Keccak-256. Do not upload or register it
  until the portal-only
  `breakdown_answer` discrepancy and `registerWasm` Intent-array semantics have
  been resolved and the user separately authorizes IPFS upload and the Base
  Sepolia wallet transaction.
- Write the payment-boundary ADR and threat model, then implement one private,
  authenticated, allowlisted, transactionally budgeted Solana request with a
  durable payment journal. Do not enable the public decision endpoint first.

## Blocked on Telegraph or external participants

- Current scoring docs and the official example define exactly three exports
  (`alloc`, `dealloc`, `rank_answer`), but portal helper text additionally names
  `breakdown_answer` without a signature. The portal also requires visible
  Intent selection while its current transaction call passes an empty Intent
  array. Treat both as operationally unresolved registration/validator
  questions; do not invent an ABI or infer activation from transaction success.
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
