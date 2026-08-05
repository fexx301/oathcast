# OathCast gap register

Last reviewed: 2026-08-05

This register separates work we can complete without Telegraph releasing the
official WASM/harness and registration materials from work that must remain a
platform gate.

## Fixed in this preparation slice

- Local Miner draft validation for required fields, canonical HTTPS, unique
  slugs, and the `0.01 USDC` price floor.
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
  workflow; the workflow is inactive until this project is hosted in a
  repository with the API key configured as a secret.
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
  SHA-256, supported Intent strings, explicit integer micro-USDC price, output
  mapping fingerprint, chain profile, source authority, and confirmation state.
- Demand-ledger SQLite triggers and a full event-hash verification check.
- A human-readable Markdown Application evidence shell that presents the
  question, Miner comparison, external influence, later resolution, durable
  hashes, raw responses, and owned-Miner-disabled ablation without claiming
  live protocol evidence.
- A registration dry-run generator and snapshot for the canonical YAML. It
  records the raw-YAML digest, supported Intents, integer micro-USDC price,
  output mapping fingerprint, and explicit non-submission claims.
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

- The public repository is hosted at `https://github.com/fexx301/oathcast` and
  the reviewed `main` branch is pushed. Configure the external canary secret
  `OATHCAST_MINER_API_KEY` when the active staging credential is intentionally
  shared with GitHub Actions; until then, the workflow skips authenticated
  checks safely. No paid AWS monitoring component is required for the
  preparation baseline.

## Blocked on Telegraph or external participants

- Official WASM boilerplate, ABI, resource limits, schema, and public harness.
- Official ground-truth source, deadline/finality, revision, and extraction
  rules.
- Hackathon 1 registration compatibility and a successful registration/
  discovery proof for the canonical Miner. Ahmed clarified that the Machina
  bond was removed from Hackathon 1 contracts and that the integration-interface
  YAML overrides the whitepaper. The YAML is still being finalized; released
  hash/schema-URI/output requirements and contract addresses must be frozen and
  validated before any transaction is encoded or submitted.
- Ahmed clarified that Application agents may call Miners directly through
  Telegraph; Engine auto-routing is optional, and those Telegraph-routed calls
  count. Payment is required for every request flowing through Telegraph via
  x402 or another supported method. The remaining demand requirement is to
  generate genuine Track 3 Application usage and avoid artificial inflation;
  do not reopen the already-answered Engine-routing question.
- Independently operated active Miners in the same Intent and their continued
  availability. The cash-prize guardrail states three active Miners and 100
  real Track 3 requests, but the full qualification semantics still need
  written confirmation.
- A protocol-compatible signer/SDK, official Telegraph settlement/Explorer
  reconciliation path, and verified HTTPS dispatcher path for one capped Base
  Sepolia test request.
  The whitepaper specifies the generic 402 → wallet broadcast → Telegraph
  verification → Miner response → cryptographic receipt sequence, but the
  hackathon's concrete endpoint, credentials, and client-side evidence format
  still govern implementation. Ahmed confirmed that served requests and their
  attached payments are public/on-chain and visible in the Explorer; a
  non-empty settlement header alone is still explicitly treated as unverified.
  The Explorer is the current manual checking path; Telegraph's API docs are
  promised but not yet released, so no client-side Explorer API is assumed.

## Non-goals

- No custom token, mainnet wallet, real-money payment, request farming, or
  fabricated Explorer evidence.
- No registration of the provider-adapter drafts as separate ecosystem Miners.
- No claim that local fixtures, read-only discovery, or direct upstream calls
  are qualifying Track 3 demand.
