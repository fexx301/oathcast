# Track 3 Application payment boundary

This document records the reviewed implementation boundary for a live
Application request. The current Telegraph transport is the Engine API:
`POST https://devnode.telegraphprotocol.com/engine/v1/ask/212`, carrying the
logical upstream `GET /forecast` request in its JSON envelope. The retired
`/miner-dispatcher/v1/{id}/...` route is not used for new Application traffic.
The path remains disabled by default in source builds, while the reviewed
production deployment enables it explicitly through owner-only configuration.
The retained 2026-09-06 payment artifacts reference the pre-Engine dispatcher
route; they are historical evidence and are intentionally not rewritten as
Engine evidence. Two operator-authorized Solana Devnet requests have traversed
the live boundary and settled successfully through Telegraph; the retained
sanitized evidence is explicitly incomplete and does not claim official
demand, adoption, or a resolved score.

## Decision

The Python process remains the Application/control plane. It validates the
user request, creates the strict OathCast forecast question, discovers the
allowlisted external Miner, aggregates its current response, and writes case
and demand evidence.

The TypeScript payment sidecar is the only process allowed to hold
`SOLANA_PRIVATE_KEY`, construct the x402 payment, send the paid request, and
write the payment journal. The two processes communicate through one private
AF_UNIX socket using bounded newline-delimited JSON. There is no TCP payment
port and the public Caddy/UI service does not proxy this socket.

The initial rollout is intentionally narrow:

- one reviewed external Miner: `212` / `forecast` (WeatherAPI schema);
- Solana Devnet USDC only, with the pinned recipient and fee payer from the
  payment canary;
- one paid attempt and a `10000` base-unit one-shot ceiling;
- one total budget unless an operator explicitly lowers or changes the
  reviewed configuration;
- a required application token, principal header, explicit consent, and
  idempotency key.

The live path is disabled unless `OATHCAST_APPLICATION_ENABLE_PAID=true`.
The public `/api/decision` endpoint is enabled only by the explicit production
UI configuration after the private gateway reports ready; source defaults and
incomplete deployments remain fail-closed with HTTP 503.

## State and idempotency

Before signing, the sidecar performs an unpaid preflight and validates the
exact Engine route, POST method, JSON envelope, x402 version, network, asset,
amount, recipient, fee payer, and challenge resource. It then reserves a
journal row. The row binds:

`principal_id + idempotency_key + canonical request hash + policy hash + target hash + challenge hash`

The append-only event table hashes every transition. A process restart moves
`reserved` and `submitted` rows to `unknown`; it never retries them. The
potentially spent states are budgeted, including `unknown`. A duplicate
settled request replays the bounded paid body from the journal without a new
network request. An unknown request requires an explicit operator
`reconcile_unknown` message containing a Solana-RPC verification artifact and
transaction signature; there is no automatic retry, and reconciliation is
never automatic.

The paid body is capped at 2 MiB and stored with its SHA-256, HTTP status,
settlement-header hash, transaction signature, RPC verification JSON, and its
verification-artifact SHA-256 before the sidecar acknowledges success to Python.
Payment authorization headers,
private keys, and raw settlement headers never cross the boundary or appear in
public evidence.

## Threat model

The boundary assumes the public Miner/UI process, an upstream response, and a
user-supplied request can be malicious or compromised. The controls are:

- the gateway binds only to loopback and requires exact bearer, principal, and
  idempotency headers;
- the sidecar binds only to a mode-`0600` Unix socket in a mode-`0700`
  directory and uses a separate shared secret;
- route, Miner, endpoint, parameter, amount, network, asset, recipient, and
  fee-payer allowlists are checked before signer access;
- request and policy hashes prevent an idempotency key from changing meaning;
- all paid outcomes are non-retryable until explicitly classified;
- the application response is allow-listed by `decision_ui.py`, so an upstream
  body cannot become public HTML or expose payment material;
- case and demand evidence are content-hashed, while payment events are
  append-only and immutable.

The sidecar is not a wallet custody service, a public API, or a substitute for
Telegraph's Explorer accounting. A successful local result becomes a
qualifying Track 3 claim only after a real, authorized request is independently
verified in Telegraph/payment evidence.

## Non-live activation checklist

An operator may prepare the environment without spending:

1. Confirm fresh registry discovery, ownership, endpoint, HTTPS Engine route, payee,
   fee payer, and current x402 `accepts[]` data for Miner 212.
2. Review the exact YAML/schema and freeze the request parameter mapping.
3. Provision separate sidecar and gateway tokens; never put the Solana key in
   the Python gateway environment or repository.
4. Run the local build/tests while the paid flag is false. The sidecar refuses
   to start in that state; enable it only after a fresh unpaid preflight and
   written authorization for one genuine devnet request.
5. Start `scripts/run_application_gateway.py` on loopback only.
6. Preserve the sidecar journal, case record, demand event, RPC verification,
   and Telegraph evidence. Do not publish a score or adoption claim until the
   response is resolved and independently reviewed.

The repository tests use injected canary responses and never sign, contact a
live Miner, or spend funds. The sidecar-specific manifest must be captured
alongside the Python release manifest because the payment image has its own
Dockerfile, lockfile, and runtime source tree.

## Evidence packaging

`scripts/build_application_evidence.py` opens the payment, case, demand, and
discovery sources read-only. Strict mode refuses malformed or incomplete
cross-links. `--allow-incomplete` writes a sanitized JSON/Markdown bundle that
lists unresolved gaps without copying the raw paid response, payment headers,
private key, principal, or idempotency key. The retained 2026-09-06 bundle is
`artifacts/application-evidence/track3-2026-09-06-payment.json` with the
operator-readable companion Markdown beside it.

## Process wiring

When the activation gates are approved, run the two processes separately. The
sidecar receives the Solana key; the Python gateway does not:

```sh
# sidecar environment only
export OATHCAST_APPLICATION_ENABLE_PAID=true
export OATHCAST_APPLICATION_SIDECAR_TOKEN='REPLACE_WITH_GENERATED_SIDECAR_TOKEN'
export SOLANA_PRIVATE_KEY='REPLACE_WITH_PROVISIONED_DEVNET_KEY'
cd payment-canary && npm run sidecar

# gateway environment, in a separate shell/service
export OATHCAST_APPLICATION_ENABLE_PAID=true
export OATHCAST_APPLICATION_SIDECAR_TOKEN='REPLACE_WITH_THE_SAME_SIDECAR_TOKEN'
export OATHCAST_APPLICATION_TOKEN='REPLACE_WITH_GENERATED_APPLICATION_TOKEN'
PYTHONPATH=src python scripts/run_application_gateway.py
```

Set the dispatcher, journal, database, and allowlist variables from
`.env.example` in the corresponding process environment. Keep the gateway on
`127.0.0.1`; place an authenticated application-facing adapter in front of it
only after that adapter has its own request, consent, and abuse controls. The
sample commands are an activation runbook, not permission to make a payment;
perform the fresh unpaid preflight and obtain written authorization first.
