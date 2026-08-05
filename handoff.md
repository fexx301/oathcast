# OathCast — Handoff and Memory Checkpoint

> Read this file before making project decisions. It is the source of continuity for future sessions.

## 1. Project identity

- **Working name:** OathCast
- **Former descriptive name:** Forecast Court
- **Workspace:** `/Users/femi/Documents/My-Projects/oathcast`
- **Last updated:** 2026-08-05
- **Current state:** Project folder created. Registration form completed. Discord access obtained. Local vertical-slice scaffolding, deployment package, hardened x402 boundary, external response normalization, read-only live Miner discovery with optional evidence snapshots, strict provider-native event validation, cutoff enforcement, immutable SQLite forecast receipts, durable Application case evidence, exact-window ground-truth resolution, request correlation, release provenance, readiness/auth/rate-limit controls, local Miner draft validation, a fixture-excluding append-only demand provenance ledger, a thin case workflow, SQLite backup/restore checks, an independent canary entry point, an owned-Miner fallback ablation, typed registration declarations, protocol result/receipt envelopes, a human-readable Application evidence shell, a canonical registration dry-run artifact, Explorer evidence templates, and repository/canary setup documentation are implemented locally and pass 78 tests. The authorized AWS EC2 staging host still runs the exact v3.2 Docker release behind verified public HTTPS at `https://oathcastcourt.duckdns.org`; the v3.2 release was verified at 64 tests, while the follow-on recovery/canary/ablation/provenance/presentation changes are local and not yet deployed. The old v3.1 container is retained stopped for rollback and the temporary EC2 Instance Connect rule has been removed. No Miner registration, paid external request, or WASM registration exists. The project has not yet been pushed to a hosted Git remote; the local GitHub CLI session is unauthenticated.
- **Primary positioning:** A public calibration court for machine forecasts.
- **One-line pitch:** OathCast time-locks short-horizon machine forecasts, settles them against independent observations, and makes Miner quality visible through transparent scoring.

## 2. Original objective

Build an uncommon, defensible Telegraph Protocol hackathon project that demonstrates the full intelligence flywheel:

1. Multiple Miners provide competing intelligence.
2. A Script Author evaluation layer ranks output quality.
3. An Application creates legitimate demand and paid Telegraph requests.
4. Real usage produces evidence of quality, ranking, routing, and usefulness.

The goal is not another generic chatbot, AI detector, scam filter, trading bot, or dashboard.

## 3. Current strategic decision

The preferred strategy is now a **gated, Application-led vertical slice across all three tracks**:

- **One public OathCast Miner:** a single service exposing the validated Open-Meteo event; WeatherAPI and OpenWeather remain internal experiments and are blocked from production failover until their event equivalence is proven.
- **One identity-blind Script Author:** the same evaluator path for OathCast and independently operated Miners.
- **One Application:** routes equivalent questions to OathCast and external Miners, uses their answers in its decision, and remains functional when OathCast is disabled.

Do not register three team-owned provider wrappers as three competing Miners. They would not establish ecosystem diversity and could look like a self-serving loop.

Commit to the full three-track narrative only after the vertical-slice gates pass: one live OathCast Miner, at least one preferably two independently operated external Miners, a compatible WASM evaluator, and an Application whose decision changes based on external responses. If those gates fail, narrow the submission explicitly to Application-only or Script Author rather than keeping an all-track claim.

## 4. Advisor decision record

An independent advisor was consulted three times using `gpt-5.6-sol` at maximum reasoning effort.

### Advisor verdict

**REVISE** — the new all-track clarification materially changes the architecture; pursue a narrow full-lifecycle slice conditionally, not three independent team-owned Miners.

### Advisor’s core recommendations

- Run a 48-hour validation spike before fully committing.
- Prove that the official schema supports numeric probabilities and comparable Miner responses.
- Confirm three qualifying Miners and a legitimate path to at least 100 paid application requests; target at least 150 for margin.
- Use a simple, defensible scalar score. Brier-style scoring is a candidate, not an official requirement.
- Treat Telegraph's current Script Author contract as raw-text semantic grading, not a structured probability contract.
- Keep the Application thin and focused on real planning use.
- Make the Application the presentation spine, while keeping competitor routing and decision impact real.
- Keep ground truth in the post-resolution evaluation path; live decisions must use current responses and historical reliability, never future outcomes.
- Require an ablation path where the Application still works with the OathCast Miner disabled.
- Do not rely on synthetic, automated, or self-generated traffic to satisfy request requirements.
- Treat weather forecasting as non-binding planning support, not emergency, medical, financial, aviation, or safety-critical advice.

## 5. Verified information from Discord

These points were answered by Ahmed Ali in the official Discord. They should still be checked against the released technical documentation when available.

- A participant does **not** need to own the upstream API.
- Proxying third-party APIs is acceptable.
- The participant is responsible for keeping the service alive and providing valuable intelligence.
- Multiple Miners can be registered by the same participant.
- Each Miner is independent from the hackathon account.
- Each YAML file counts as one Miner. Ahmed reconfirmed this on 2026-08-04; this does not make near-identical internal provider wrappers strategically distinct, so keep one canonical OathCast Miner unless a genuinely different service earns a separate registration.
- Use one base URL per YAML. If services use different base URLs, use separate YAML files.
- Multiple endpoints can live in one YAML when they are supported by the same base URL.
- There is no public Script Author/WASM test harness yet. Telegraph is finalizing official boilerplate WASM modules for supported Intents, plus the testing harness and guides; the team is using the current harness internally and will share it when ready.
- Late, missing, invalid, and abstained responses score `0` and are dropped from routing for the rest of the epoch.
- Telegraph does not mandate Brier score; a Script Author may choose it as long as the final script output is between `0` and `1`.
- The current Weather Intent is schema-agnostic: a Miner may return arbitrary JSON or a chat-completion-like `content` field. The ranking/scoring layer extracts meaning from the response.
- The Script Author currently receives three inputs: the original question, the ground-truth answer, and the Miner's raw response.
- The current scoring model is a `0..1` composite based on cosine similarity, BM25 word overlap, and response-length quality. Brier is not currently part of Telegraph's scoring model.
- Real Application requests must go through Telegraph.
- Application requests do not need to use automatic routing; direct Miner calls are allowed and counted.
- Payment is required for all requests flowing through Telegraph, through x402 or another supported payment method.
- The current hackathon runs on Base Sepolia and uses Base Sepolia USDC rather than a custom testnet token.
- Each Miner YAML must define a request price, and the minimum allowed value is 0.01 USDC (1 cent).
- Ahmed clarified that the node records every request a Miner serves regardless of which supported payment method was used, and the Explorer exposes that activity. This is request accounting, not permission to bypass Telegraph or treat direct upstream calls as Application demand.
- Requests routed through Telegraph at or above the declared Miner price count as valid request activity; the YAML price floor remains 0.01 USDC.
- Ahmed clarified on 2026-08-05 that the Machina bond was removed from the Hackathon 1 contracts. Do not treat a Machina bond as a current H1 registration prerequisite unless a later frozen contract specification supersedes this answer.
- Ahmed clarified on 2026-08-05 that the integration-interface YAML always overrides the whitepaper for Hackathon 1. The YAML is still being audited/finalized; hashes, schema URI, and all other released requirements apply, and the YAML is validated before on-chain submission.
- Ahmed clarified on 2026-08-05 that served requests/signals are public on the Telegraph Explorer, payment is attached to each request, consumption-side records are kept on-chain, Miner totals are visible on the Explorer, and Application consumption can be tracked through payment. Explorer/on-chain evidence is the official demand source; OathCast's local ledger remains corroborating provenance only.
- Ahmed clarified on 2026-08-05 that the Explorer is the current way to check request/payment records and that Telegraph will release API documentation later. Until those APIs are documented, use manual Explorer evidence for a capped live test and keep the protocol envelope/API adapter generic; do not build against an invented Explorer API.
- Telegraph's explorer exposes miner, validator, and signal/request activity: https://explorer.telegraphprotocol.com/
- The three tracks are judged independently, but one project may participate across all three.
- A genuine all-track project must build the full lifecycle: service behind an API, Miner integration and registration, WASM evaluation logic, and agents that consume the requests and make decisions.
- The Application must route to other Miners as well as any Miners owned by the participant. An agent that only consumes its owner's Miner is a self-serving loop and is explicitly discouraged.

### Payment boundary and timing decision

- No funds or live payment are needed for the current preparation phase: local fixtures, service tests, YAML drafting, Script Author work, offline Application logic, or ordinary Miner development.
- **Implement now:** put the declared Miner price (`0.01 USDC`) into the draft/canonical YAML and keep it covered by local validation. This is configuration only; it does not move funds or require a wallet.
- **Implement during Track 1/2 (from 2026-08-17):** register and validate the Miner and Script Author when the official YAML/WASM harness is released. Continue using local fixtures for engineering; do not generate Application demand yet.
- **Implement during Track 3 (from 2026-08-31):** enable the Application's Telegraph payment adapter, use a dedicated faucet-funded Base Sepolia wallet, send manually approved paid requests through Telegraph, and verify the resulting Miner activity in the Explorer. Track 3's official window is Aug 31–Sep 7, 2026.
- Live payment becomes necessary only if we enter Track 3 with a real Application request flowing through Telegraph. Direct upstream calls, local mocks, and development fixtures are useful for engineering but do not count as qualifying Telegraph demand.
- Do not fund a wallet or sign a payment now. Activate the payment path only after Track 3 opens on 2026-08-31 and all of these are verified: official HTTPS dispatcher/payment route, compatible signer/SDK, Base Sepolia USDC wallet, recipient/price allowlist, the official Telegraph settlement/Explorer reconciliation path, and restart-safe reconciliation.
- The first live test should be one manually approved request at the declared minimum price (`0.01 USDC`), followed by a small hard-capped batch of legitimate user requests. There must be no automatic faucet use, recurring spend, or production private key on the Miner host.
- Base Sepolia USDC is testnet currency with no financial value and is not backed by real US dollars; any Base Sepolia ETH needed for gas should likewise come from a faucet. A Telegraph payment on this network is a real testnet transaction for protocol/accounting purposes, not real-money spending. Use only a dedicated test wallet and never use mainnet USDC.
- The official global cash-prize guardrail is at least 3 active Miners in the same Intent and at least 100 real Track 3 requests. This is not a prerequisite for participating in Track 1 or Track 2, nor for building a local Application prototype.
- If the payment prerequisites or legitimate-demand path do not materialize, narrow the claim to Miner/Script Author (or an explicitly non-qualifying Application prototype) rather than pretending that unpaid traffic is Track 3 evidence.

### Official whitepaper review — 2026-08-04

The official [Telegraph Protocol whitepaper](https://telegraphprotocol.com/Whitepapers%20-%20Telegraph%20Protocol.pdf), Version 1.0 dated 2026-05-04, was reviewed after the Discord research. It is useful for protocol architecture, but the hackathon portal and current team answers remain authoritative for Hackathon 1 overrides.

- Miner registration is explicitly permissionless: the whitepaper describes an on-chain registration transaction, supported Intent IDs, a Miner fee address, the declared `min_price_usdc` floor, a credential hash, a YAML schema hash/URI, and an `on_chain_output` mapping. Ahmed's Hackathon 1 clarification supersedes the whitepaper's Machina-bond assumption: the bond was removed from the H1 contracts.
- The whitepaper's x402 flow is: Agent request → HTTP 402 with amount, recipient, network, and deadline → wallet signs/broadcasts USDC → Telegraph verifies settlement → routes to a Miner → returns the response and cryptographic receipt. It also describes a pre-funded escrow and a Web2/MPC-wallet onboarding path. OathCast now records the challenge, artifact, and verification state explicitly; a non-empty settlement header is not treated as verified.
- The whitepaper makes the protocol/client boundary explicit: a protocol-native Flow handles a paid Intent, routing, validation, and receipt delivery; multi-step stateful Workflows belong in the Agent/Application. This supports OathCast's Application-side case store and cross-Miner decision logic.
- The whitepaper describes a Base Sepolia testnet bootstrap phase with no real capital at risk, while the hackathon/Discord specifically governs our current Base Sepolia USDC path. Do not infer mainnet contracts, MPC onboarding, or the `on_chain_output` schema are already required for Hackathon 1; the integration-interface YAML is authoritative and still being frozen.
- Script registration is described in the whitepaper as permissionless with a 10,000 Machina anti-spam bond and strict WASM sandbox rules, but Ahmed says the Machina bond was removed from Hackathon 1 contracts. Treat the whitepaper bond as non-applicable to H1 unless a later frozen contract specification says otherwise; it is not a substitute for the unreleased H1 boilerplate/harness.

Decision: keep our current service and payment boundary, but add a registration-compatibility checkpoint before submitting. The Machina-bond blocker is removed. We still must wait for the frozen H1 YAML, exact hash/schema-URI rules, contract addresses, and testnet registration flow. Do not submit a registration transaction based on the whitepaper alone.

## 6. Important correction to the original idea

Earlier drafts treated “honest abstention” as a scoring advantage. That is no longer valid based on the Discord clarification.

OathCast should **not** reward abstention. Miners should return valid, comparable numeric forecasts. The user interface may show low confidence when a forecast is near `0.5`, but the protocol-facing response should not rely on an `abstain` state.

Do not describe OathCast as an abstention-aware scoring system unless the official Script Author documentation explicitly supports a different treatment.

The local Brier harness remains useful for objective weather-quality analysis and provider comparison. It must not be presented as the current Telegraph Script Author metric. Script preparation must additionally include a deterministic text-rendering/normalization path so a structured weather response can be compared by Telegraph's current semantic scorer.

## 7. Product concept

OathCast asks short-horizon, objectively resolvable questions such as:

> Will rainfall exceed 0.1mm at a named observation station between 15:00 and 16:00 UTC?

Possible users:

- Outdoor event organizers
- Sports groups
- Market vendors
- Delivery riders
- Small logistics operators
- People planning outdoor activities

The Application should:

1. Collect a real planning question.
2. Specify location/station, metric, threshold, horizon, and cutoff.
3. Discover and call OathCast plus independently operated Telegraph Miners directly or through an approved route.
4. Capture each raw response, normalized forecast, Miner identity, and Telegraph request/payment metadata.
5. Use current responses and historical reliability to make the live planning decision; do not use future ground truth.
6. Freeze the forecast before the cutoff.
7. Resolve the event later using an independent observation source.
8. Apply the same Script Author evaluation path to every Miner after resolution.
9. Display Miner-by-Miner scores, cost, latency, and historical performance.
10. Produce a shareable forecast-versus-outcome receipt.
11. Remain functional and useful when the OathCast Miner is disabled.

The product must remain non-binding planning support.

## 8. Internal working response shape

This is an internal draft only. Do not treat it as the official Telegraph schema until the harness and Intent documentation are released.

```json
{
  "event_id": "unique-event-id",
  "station_id": "station-001",
  "metric": "precipitation_mm",
  "operator": ">",
  "threshold": 0.1,
  "horizon_start": "2026-08-17T15:00:00Z",
  "horizon_end": "2026-08-17T16:00:00Z",
  "probability": 0.73,
  "issued_at": "2026-08-17T12:00:00Z",
  "cutoff_at": "2026-08-17T14:59:00Z"
}
```

The official implementation may require a different field layout. Adapt to Telegraph’s schema once released.

## 9. Planned architecture

```text
                         +----------------------+
                         |  OathCast Web App     |
                         |  Next.js / TypeScript |
                         +----------+-----------+
                                    |
                         paid Telegraph requests
                                    |
                 +------------------+------------------+
                 |                                     |
       +---------v----------+              +-----------v-----------+
       | OathCast Miner     |              | External Telegraph     |
       | one public YAML    |              | Miners (not ours)      |
       | 3 adapters behind  |              +-----------+-----------+
       +---------+----------+                          |
                 +------------------+------------------+
                                    |
                         raw responses + provenance
                                    |
                       +------------v-------------+
                       | OathCast Application      |
                       | compare, route, decide   |
                       +------------+-------------+
                                    |
                       +------------v-------------+
                       | Identity-blind WASM      |
                       | Script Author evaluator  |
                       +------------+-------------+
                                    |
                       +------------v-------------+
                       | Independent observation  |
                       | resolves cases later     |
                       +--------------------------+
```

### Planned components

- **OathCast Miner:** one public service backed by Open-Meteo for the validated v1 event. WeatherAPI and OpenWeather adapters remain internal experiments and are blocked from production failover until their event equivalence is proven; only this service is a candidate for our Miner registration.
- **External Miners:** independently operated Telegraph Miners discovered from the registry; direct calls to upstream weather APIs do not count as external Miners.
- **Application:** owns discovery, capability filtering, dispatch, payment, raw-response retention, normalization, historical reliability, and the final planning decision.
- **Evidence loop:** `SqliteCaseStore` freezes the canonical question and records raw Miner replies, decision snapshots, observations, and append-only resolution revisions. `ground_truth.py` rejects wrong stations/coordinates/windows, future-leaking timestamps, and ambiguous data; missing observations remain unresolved rather than becoming “no rain.”
- **Script Author:** identity-blind deterministic evaluator applied uniformly to every raw Miner response after a case resolves.
- A shared renderer emits a minimal `content` plus `probability` response; internal provenance records include request ID, Miner identity, raw response, normalized forecast, price, timestamps, and parser version.
- All three adapters remain local parsing/build-url modules only at this stage; no public endpoints or live Miner registrations exist yet.
- **Script preparation:** deterministic text normalizer plus semantic-score fixture tests for the current contract; retain Brier as a separate local domain benchmark.
- **Application:** a thin, user-facing workflow that generates legitimate paid demand.
- **Ground-truth service:** independent timestamped observations with an explicit missing-data and timezone policy.
- **Telemetry:** request ID, Miner ID, timestamp, payment status, response validity, latency, forecast, outcome, and score.

## 10. Candidate scoring approach

Current protocol-facing metric (per Ahmed Ali's Discord answer):

- The Script Author receives the original question, ground truth, and raw Miner response.
- The current scorer is a `0..1` composite of cosine similarity, BM25 word overlap, and length quality.
- Structured JSON may be converted to readable text before scoring; the scorer itself works with plain text.
- Brier is not currently part of the protocol scorer.

Local domain benchmark:

- Keep Brier score and aggregate Brier skill against a frozen climatology baseline for objective provider comparison.
- Report the local Brier result separately from the protocol-compatible semantic score.
- Invalid, late, missing, and abstained results must score `0` and be excluded from later routing for the relevant epoch according to the Discord clarification.
- Do not let a local benchmark silently become an unsupported claim about Telegraph's current evaluator.

The evaluator must be deterministic, testable, resistant to time leakage, and explicit about:

- Outcome timestamp
- Forecast cutoff
- Station identity
- Timezone
- Missing observations
- Observation revisions
- Duplicate requests
- Invalid JSON or out-of-range probabilities

### Preparation implementation snapshot (2026-08-03)

Implemented under `/Users/femi/Documents/My-Projects/oathcast`:

- `src/oathcast/forecast.py`: strict one-hour UTC question and canonical forecast contracts.
- `src/oathcast/adapters/`: Open-Meteo, WeatherAPI, and OpenWeather parsers plus exact-hour selection; no silent aggregation.
- `src/oathcast/service.py`: one HTTP Miner service with strict event/cutoff checks, production fail-closed auth, safe provider selection, request parsing, raw-payload provenance hashing, and receipt replay.
- `src/oathcast/receipts.py`: append-only SQLite receipts keyed by `event_id`, with canonical-question conflict checks, integrity hashes, and immutable database triggers.
- `src/oathcast/discovery.py`: weather-Intent capability parsing and owned-Miner filtering for registry snapshots.
- `src/oathcast/application.py`: cross-Miner router, raw-response retention, external influence detection, and owned-Miner failover.
- `src/oathcast/payment.py`: read-only integrations discovery and a safe Base Sepolia x402 challenge/retry boundary; it requires an injected real signer and never fabricates payment proofs.
- `src/oathcast/payment.py`: strict preflight and payment policy: Base Sepolia USDC exact scheme, explicit recipient/resource/amount/deadline validation, immutable validated signer authorization, HTTPS-only signing, target allowlists, durable SQLite `RESERVED -> SUBMITTED -> SETTLED/UNKNOWN` journaling, spend caps, duplicate protection, independently verified settlement requirement, and unknown-outcome halt.
- `src/oathcast/protocol.py`: protocol result envelopes that keep x402 artifacts, verification state, registry snapshot provenance, optional Signal Receipt identity, and response hashes distinct from OathCast forecast receipts.
- `src/oathcast/registration.py`: immutable per-generation Miner registration declarations with explicit integer micro-USDC pricing, raw-YAML SHA-256, output-mapping fingerprint, chain profile, source authority, and confirmation status; it does not encode or submit transactions.
- `src/oathcast/demand.py`: append-only local demand provenance with application correlation, Telegraph/payment/fixture predicates, explicit settlement verification and artifact hashes, immutable SQLite triggers, event-hash verification, and an explicit refusal to claim Telegraph's official count.
- `src/oathcast/ground_truth.py`: exact one-hour observation contract, integer-micrometre precipitation normalization, strict `> 0.1 mm` resolution, and explicit missing/invalid statuses.
- `src/oathcast/cases.py`: SQLite Application evidence store with immutable question hashes, Miner reply/decision snapshots, observation metadata, resolution hashes, restart-safe idempotency, and append-only revisions.
- `src/oathcast/workflow.py`: thin Application façade that seals a current cross-Miner decision and resolves it later without exposing outcomes to the live router.
- `src/oathcast/render.py`: deterministic text presentation with a small `content` + `probability` envelope.
- `src/oathcast/reference_evaluator.py`: development-only proxy for the future three-input Script Author contract; not the official scorer.
- `src/oathcast/scoring.py`: Brier loss, Brier quality, unclipped Brier skill, coverage, and zero-scored non-valid attempts.
- `fixtures/`: development provider payloads, registry snapshot, evaluator cases, one common question, and ten synthetic Brier cases.
- `miners/`: one canonical OathCast Miner registration draft plus three provider-adapter YAML experiments; only the canonical draft is a registration candidate.
- `scripts/discover_live_miners.py`: read-only live registry discovery; it does not pay, sign, or write a snapshot.
- `scripts/preflight_miner.py`: one unpaid challenge inspection for a selected Miner; it never attaches a signer.
- `Dockerfile`, `.dockerignore`, `.env.example`, `Caddyfile`, and `DEPLOYMENT.md`: public-service packaging and operational handoff.
- `tests/`: 77 standard-library contract tests; all pass.

Read-only live registry observation on 2026-08-03 (volatile; query again before routing):

| Miner | ID | Selected endpoint | Minimum price |
|---|---:|---|---:|
| OpenWeatherMap | 211 | `forecast` | 10,000 micro-USDC ($0.01) |
| WeatherAPI | 212 | `forecast` | 10,000 micro-USDC ($0.01) |
| Zeus Weather Forecasting / Bittensor SN18 | 18 | `predict` | 10,000 micro-USDC ($0.01) |

This proves discoverability only. No paid call was made, no wallet was used,
and these records are not copied into the local fixture or counted as traffic.

Non-signing preflight of Miner 18 on 2026-08-03 returned HTTP 402 with x402
version 2, `exact`, Base Sepolia USDC, amount `10000` micro-USDC, and recipient
`0x43Eb1B49a079a4587E0D7e8dA81035dc791c91F8`. The current challenge omitted a
resource URL. The dispatcher URL returned by the official guide and live
preflight is HTTP, not HTTPS; no payment authorization will be sent until an
approved HTTPS route exists or an explicitly approved disposable-wallet
exception is chosen.

Development fixture output is not evidence of live provider performance and cannot count as hackathon traffic. Current synthetic benchmark output:

| Provider | Valid coverage | Brier | Brier skill | End-to-end score |
|---|---:|---:|---:|---:|
| Open-Meteo | 0.90 | 0.084478 | 0.707858 | 0.823970 |
| OpenWeather One Call | 0.90 | 0.096100 | 0.624365 | 0.813510 |
| WeatherAPI | 0.80 | 0.114575 | 0.579541 | 0.708340 |

These numbers are fixture sanity checks only. They do not establish provider ranking or event comparability.

## 11. Remaining unknowns

Do not bombard the Discord with more questions now. The following are the only current blockers:

1. Public release of the official Script Author/WASM boilerplates, test harness, guides, and exact function signature. Telegraph currently uses the harness internally.
2. Released harness extraction behavior for raw JSON, `content`, and any supported structured fields.
3. Official ground-truth source and how it is supplied to the evaluator.
4. Which observation source is accepted as sufficiently independent, its latency/finality deadline, and its revision policy.
5. A funded Base Sepolia wallet, real signer/PayAI integration, the official Telegraph settlement/Explorer reconciliation path, and a safe Application payment flow for paid Miner requests.
6. Exact submission artifacts and pre-build rules.
7. The frozen Hackathon 1 integration YAML, exact hash/schema-URI rules,
   credential/output fields, contract addresses, and testnet registration flow.
   Ahmed has explicitly removed the Machina bond from H1 contracts.
8. The eventual Explorer/API payment artifact and reconciliation format an
   Application should retain as proof of a served request. The Explorer is the
   current manual source of truth; Telegraph's API documentation is promised
   but not yet released.

The public-host blocker is resolved for staging: an AWS EC2 `t3.micro` was
explicitly authorized and launched on 2026-08-03. Domain/TLS, DDNS, durable
receipts, release provenance, and the public authentication boundary are now
verified. Application payment, ground-truth, Telegraph demand, and spend
monitoring still remain; staging API-key rotation was completed on 2026-08-04.

These have already been asked or are awaiting the promised documentation. Do not repeat them until new documentation is released.

The x402 protocol shape is now documented in the official inference guide:
`GET /miner-dispatcher/integrations` is unpriced; paid requests use a 402
`Payment-Required` challenge, a signer-produced `PAYMENT-SIGNATURE`, and a
settlement response header. The implementation boundary now retains the
header as an artifact but requires an independent verifier before marking the
payment settled; the signer, wallet funding, and live paid proof remain
intentionally outstanding.

### Hosting timing decision

- On 2026-08-03, the user reported approximately 27 days remaining on the Railway trial.
- On 2026-08-03, the user confirmed via the AWS EC2 Free Tier screen that the new six-month plan applies, with $100 signup credit, optional additional credits, and eligible `t3.micro`, `t3.small`, `t4g.micro`, `t4g.small`, `c7i-flex.large`, and `m7i-flex.large` types. The AWS staging host is now provisioned.
- Railway's current official policy says the trial is a one-time $5 grant that expires after 30 days, after which the account reverts to a Free plan with $1 of monthly credit: https://docs.railway.com/pricing/free-trial
- The official Hackathon 1 window is Aug 17–Sep 7, 2026: https://hackathon.telegraphprotocol.com/
- Do not provision Railway now. Target deployment around Aug 16–17, after the service, authentication, upstream keys, and payment preflight are ready. This keeps the trial aligned with the judging window.
- The AWS staging host is the authorized public staging target; do not add Railway or another host without a new cost/operations decision.

### AWS hosting decision and current staging host

- AWS can substitute for Railway if the account is eligible. AWS's current Free Tier gives new customers a free plan for up to six months and up to $200 in credits; eligibility depends on account history and a payment method is still required: https://aws.amazon.com/free/free-tier-faqs/
- Preferred AWS shape for this stateless service: one free-tier-eligible EC2 instance running the existing Docker image, with HTTPS termination and a tightly scoped security group. Avoid managed databases, NAT gateways, load balancers, and other unnecessary billable components.
- Default instance choice: `t3.micro` rather than ARM `t4g.micro`, to minimize Docker image and tooling compatibility risk; use `t4g.micro` only if we deliberately validate the ARM64 path.
- On 2026-08-03, AWS was selected for staging and the first host was launched in `eu-north-1` (Stockholm): instance `i-0c4948734b7a6326c`, `t3.micro`, Amazon Linux 2023 x86, security group `oathcast-web`, key pair `oathcast-ec2`.
- The instance is running Amazon Linux 2023 and passed the deployment smoke checks. Re-check EC2 status checks before any later stop/start or production-facing change.
- Current public address is `13.49.229.253` (`ec2-13-49-229-253.eu-north-1.compute.amazonaws.com`). This is ephemeral and may change after stop/start because no Elastic IP was allocated.
- The final inbound rules are public HTTP `80` and HTTPS `443` only. The stale SSH CIDR rule and the temporary EC2 Instance Connect prefix-list rule used for updater setup were removed. Port `8080` is not exposed by the security group.
- AWS created and downloaded the private key locally during launch. Never inspect, commit, paste, or upload its contents; use the downloaded file only for the eventual SSH session.
- Docker is installed and enabled. Container `oathcast` runs image `oathcast:v3-2-correct` with `--restart unless-stopped`, bound to `127.0.0.1:8080` (private to the instance) and durable host storage `/home/ec2-user/oathcast/data:/data/oathcast`. The previous v3.1 container is retained stopped as `oathcast-old-20260804-v3-1` for rollback; the older v2 rollback remains stopped as well. Container `oathcast-caddy` runs with host networking, persistent `/home/ec2-user/oathcast/caddy-data` and `caddy-config` mounts, and proxies public ports 80/443 to the Miner. The remote owner-only env file is `/home/ec2-user/oathcast/.env`; it now holds the active rotated key. The owner-only local backup at `/Users/femi/Downloads/oathcast-miner.env` retains the retired key for the stopped v3.1 rollback only and is not a current smoke credential. Never print, commit, or paste either file's contents.
- Verified on 2026-08-03 externally: HTTPS `/healthz` returned HTTP 200 with a trusted certificate; HTTP redirected with 308; unauthenticated forecast returned HTTP 401; an authenticated near-term Lagos forecast through HTTPS returned HTTP 200; and public port 8080 timed out. The external response included `Via: 1.0 Caddy`.
- DuckDNS is now the approved zero-cost hostname path: `oathcastcourt.duckdns.org` was created on 2026-08-03 and corrected to the current EC2 public IPv4 `13.49.229.253`. DuckDNS initially auto-detected the operator's VPN/hotspot egress IP (`102.89.23.245`), so never rely on auto-detection from the local browser for this server.
- HTTPS termination is live: Caddy now serves `oathcastcourt.duckdns.org`, automatically obtained the certificate, and redirects HTTP to HTTPS. The canonical Miner YAML points to `https://oathcastcourt.duckdns.org`. Do not register the Miner until the DDNS updater, official YAML/harness validation, and paid-request gates are also complete.
- The 2026-08-03 infrastructure advisor review recommends keeping `8080` loopback-only and using the reverse proxy. DuckDNS is free but depends on the EC2 IP remaining synchronized; the narrowly scoped updater is installed, and an Elastic IP is still unnecessary. The temporary EIC rule has now been removed; do not reopen SSH except for a specific, time-bounded maintenance operation. Do not allocate an Elastic IP without approval.
- DuckDNS synchronization was installed on 2026-08-03 on the EC2 host as `duckdns-update.service` plus `duckdns-update.timer`, using root-only `/etc/duckdns.env`; a scheduled run at 22:43 UTC returned `DuckDNS update OK`. The temporary EC2 Instance Connect SSH rule used for setup was then removed and the final security group restored to HTTP/HTTPS only.
- On 2026-08-04, v3.2 was deployed from the independently hashed source bundle with source SHA256 `df62b88dbb4a4e6661e944a910269058f910d9f1201c27b5ac1833f19496d976`; the source bundle SHA256 is `66c8b2b455db48d0a11e9ca5c820ba3e8723b9e4a96cb346b9fd966ef322438b`. The host image ID is `sha256:34183579a239fdfd3048519c8c5551a428a7ed2665b2df6b312b86e36bd1d71`; public `/healthz` and `/readyz` report release `oathcast-2026-08-04-hardened-v3-2`, that source digest, and that image identity. Public health/readiness/auth checks passed, the authenticated forecast was replayed after restart with the same receipt hash, and the security group was verified at two inbound rules: HTTP 80 and HTTPS 443 only. See `artifacts/release-evidence/oathcast-2026-08-04-hardened-v3-2-runtime-evidence.json`.
- On 2026-08-04, the staging Bearer credential was rotated with a temporary old/new overlap. Both credentials returned `200` through public HTTPS during overlap; after the container was recreated from the changed env file, the retired credential returned `401`, the active credential returned `200`, and `/healthz` plus `/readyz` returned `200`. The security group was then restored to HTTP/HTTPS only. See `artifacts/release-evidence/oathcast-2026-08-04-key-rotation.json`. The repository does not contain either secret.

AWS eligibility checklist: the account must qualify as a new customer for the
new Free plan/credits; existing or previously used AWS accounts are ineligible
for those new-customer benefits. Accounts created before 2025-07-15 follow the
legacy EC2 rules, while newer accounts use the six-month-or-credit-exhaustion
limit. The account needs a valid payment method for identity verification, the
selected EC2 instance/AMI/EBS combination must be marked Free Tier eligible,
and joining an AWS Organization or Control Tower can end the Free plan and
expire credits. Confirm status, expiration, and balance in the AWS Cost and
Usage widget before adding billable components or leaving the host running
outside the intended staging window.

## 12. 48-hour go/no-go spike

### Gate A — Script feasibility

- The official public WASM boilerplate/harness is **not yet available**; the local evaluator is only a development proxy.
- A minimal script compiles/runs.
- Script receives response, ground truth, and request metadata as needed.
- Script returns a valid score in `[0, 1]`.

### Gate B — Forecast schema

- Canonical forecasts render into semantically comparable text under the released extraction path.
- Three Miners return semantically comparable responses.
- Probability, event wording, and timestamp rules are clear enough to compare without leakage.

### Gate C — Ground truth

- Observation source is independent and timestamped.
- Threshold and horizon resolve deterministically.
- Missing/revised data policy is documented.

### Gate D — Eligibility and demand

- The OathCast Miner is eligible and live.
- At least three active Miners exist in the same Intent, including at least one independently operated external Miner; the official rules require three active Miners and at least 100 real Track 3 requests for global cash-prize eligibility.
- Application requests are paid and routed through Telegraph.
- Direct Miner calls are traceable and counted.
- External responses materially affect the Application decision.
- The Application still completes when the OathCast Miner is disabled.
- There is a credible plan for at least 100 legitimate, paid Track 3 requests; target 150+ without artificial inflation.

### Decision

- **All gates pass:** proceed with the deliberate Miner + Script Author + Application vertical slice.
- **Any external-routing or WASM gate fails:** narrow the submission explicitly to Application-only or Script Author; do not keep an all-track claim.
- **Do not:** create duplicate Miners, farm requests, rely on daemon traffic without confirmation, or claim unsupported probability/abstention behavior.

## 13. Build lifecycle

### Phase 0 — Discovery and preparation

Current phase, before August 17, 2026.

- Obtain official harness and schema.
- Confirm ground truth and payment flow.
- Draft three separate YAML configurations.
- Build local adapters and development fixtures.
- Build a local semantic-score contract fixture and a separate Brier scorer without using fixtures as qualifying hackathon traffic.
- Build and test the local HTTP Miner service with the validated Open-Meteo path; keep unverified provider failover opt-in only.
- Build and test the Application evidence loop: freeze questions/decisions, retain raw replies and hashes, resolve only exact observation windows, keep missing data unresolved, and append revisions instead of overwriting evidence.
- Build and test Application-side capability discovery, cross-Miner routing, external influence, and owned-Miner failover.
- Record Application request provenance in the append-only demand ledger; treat only independently verified, successful Telegraph responses as conservative local candidates and never substitute the ledger for Telegraph's official node/Explorer accounting.
- Query the live integrations endpoint read-only before each routing experiment; prefer independently operated Miners 18, 211, and 212 when they remain active.
- Package the public HTTP Miner with Docker and `.env.example`; built the staging image on the authorized AWS host on 2026-08-03.
- Installed Docker and deployed the authenticated staging service on the AWS host on 2026-08-03; DuckDNS synchronization and public HTTPS are complete, while the Application, payment, Script Author, ground-truth, and demand gates remain.
- Keep paid transport behind `TelegraphX402Client`; run its non-signing preflight first, never add a fake proof, and never claim live traffic until a real signer and funded Base Sepolia wallet are configured.
- Configure `OATHCAST_MINER_API_KEY` in the host secret store so the public Miner enforces the Bearer token declared in its YAML; keep the payment wallet off-host.
- Build and test the development reference evaluator against structured, chat, empty, and overlong responses; replace its proxy scoring once the official harness arrives.
- Base Sepolia USDC and the minimum Miner price of 0.01 USDC are confirmed; exact wallet/faucet flow remains open.
- Prepare a minimal Application shell.

### Phase 1 — Miners and Scripts

Expected opening: August 17, 2026 at 12:00 UTC.

- Register one genuinely useful OathCast Miner only; the provider adapters remain internal sources/failover.
- Identify and successfully query at least one, preferably two, independently operated external Miners.
- Keep every upstream service live.
- Submit and test the Script Author evaluator.
- Demonstrate the Application's decision changes when an external response changes, and survives with OathCast disabled.
- Post transparent progress updates on X.
- Confirm performance and response validity.

### Phase 2 — Application

Expected opening: August 31, 2026.

- Launch the thin OathCast workflow.
- Use paid Telegraph requests only.
- Recruit real users and collect legitimate planning queries.
- Publish resolved forecast receipts and scorecards.
- Track actual request attribution and costs.

### Phase 3 — Submission and evidence

- Freeze the public repository.
- Document architecture and request flow.
- Include live demo, screenshots, transaction/payment evidence, score history, and limitations.
- Show the full loop: Miner → Script → Application → demand → ranking.

## 14. Judge strategy

Telegraph emphasizes the complete cycle from Miner to Application to demand. The project should visibly demonstrate:

- A real public service behind the OathCast Miner and real independently operated external Miners.
- Meaningful differences between Miner responses.
- A deterministic quality function.
- Real ground truth.
- Paid Telegraph requests.
- Transparent rankings.
- Real users and real usage.
- Public progress updates.
- Evidence that the Application consumes and compares external Miners, not only participant-owned services.

### Current judging-weight interpretation

The Hackathon judging page currently shows engagement and X updates as:

- **Track 1 — Miner:** 25% engagement/X updates; the other 75% is performance
  within the Intent.
- **Track 2 — Script Author:** 10% engagement/X updates; the priority remains
  automated evaluation quality, ranking accuracy, and resistance to gaming.
- **Track 3 — Application:** 25% engagement/X updates; the remaining weight is
  the application's real usage, usefulness, creativity, and Telegraph-Miner
  consumption requirements.

This means our public updates should be evidence-led rather than high-volume:

- Miner updates should show availability, forecast quality, response validity,
  and meaningful progress toward live demand.
- Script Author updates should explain evaluator design, anti-gaming tests,
  harness compatibility, and ranking behavior.
- Application updates should show real user-facing utility, live Miner usage,
  Explorer/payment evidence when available, and resolved outcomes.

Do not use fixture runs or artificial traffic as engagement proof. Keep all
development screenshots and local demos explicitly labeled as preparation.

Avoid building:

- Generic weather chatbot
- Generic AI agent
- Polymarket-style trading bot
- Another scam/deepfake detector
- Generic on-chain escrow agent
- Unverifiable humanitarian or medical decision system

## 15. Safety and operational rules

- Never put API keys in YAML or source control.
- Respect third-party API terms and rate limits.
- Do not store unnecessary personal data.
- Do not make emergency, medical, financial, aviation, or safety-critical claims.
- Do not automate artificial request volume.
- Keep the OathCast Miner available throughout the judging period and show visible failure handling for external outages.
- Treat all official Discord answers as provisional until reflected in the released docs.
- Do not make irreversible architecture decisions before the harness and schema are known.

## 16. Official references

- [Hackathon landing page](https://hackathon.telegraphprotocol.com/)
- [Hackathon rules](https://hackathon.telegraphprotocol.com/rules)
- [Telegraph protocol overview](https://telegraphprotocol.com/)
- [Protocol mechanics](https://docs.telegraphprotocol.com/docs/protocol/how-it-works)
- [Engine inference](https://docs.telegraphprotocol.com/docs/using/engine-ask)
- [Daemon signal feed](https://docs.telegraphprotocol.com/docs/using/daemon-signals)
- [Miner YAML configuration](https://docs.telegraphprotocol.com/docs/miners/yaml-config)
- [Miner registration](https://integrate.telegraphprotocol.com/)
- [Telegraph authority matrix](docs/telegraph-authority-matrix.md)
- [x402 inference and payment flow](https://docs.telegraphprotocol.com/docs/using/x402-inference)
- [Telegraph Protocol whitepaper](https://telegraphprotocol.com/Whitepapers%20-%20Telegraph%20Protocol.pdf)
- [Current Telegraph applications](https://alexandria.telegraphprotocol.com/apps)
- [Telegraph Explorer](https://explorer.telegraphprotocol.com/)
- [Open-Meteo API docs](https://open-meteo.com/en/docs)
- [WeatherAPI docs](https://www.weatherapi.com/docs/)
- [OpenWeather One Call 3.0 docs](https://openweathermap.org/api/one-call-3)

## 17. Recovery instructions for a future session

1. Read this file completely.
2. Inspect the current workspace and Git status.
3. Check whether the official Script Author harness and Weather Intent schema have been released.
4. Do not re-open already-resolved Discord questions.
5. Update the “Remaining unknowns” section with authoritative answers.
6. Run the 48-hour go/no-go gates before committing to the full stack.
7. Preserve OathCast’s core differentiator: time-locked, objectively settled, publicly scored forecasts.
8. If the gates fail, use the documented Application-only fallback rather than inventing unsupported protocol behavior.

## 18. Change log

- **2026-08-02:** Researched the official hackathon pages, rules, Telegraph protocol, docs, Alexandria apps, and existing use cases. Selected OathCast/Forecast Court as the leading concept. Consulted an independent advisor.
- **2026-08-02:** Completed the hackathon registration form and gained Discord access.
- **2026-08-03:** Discord clarified multi-Miner registration, third-party API proxying, YAML-per-Miner behavior, invalid/late/abstained scoring, direct paid request counting, and the forthcoming Script Author harness.
- **2026-08-03:** Ahmed Ali clarified that Weather Intent responses are schema-agnostic, the Script Author receives question + ground truth + raw Miner response, and the current scorer uses cosine similarity, BM25 overlap, and length quality; Brier is a local benchmark rather than the current protocol metric.
- **2026-08-03:** Ahmed Ali clarified that the hackathon uses Base Sepolia USDC and exposes request/miner activity through the Telegraph Explorer.
- **2026-08-03:** Ahmed Ali corrected the pricing detail: every Miner YAML must define a price, with 0.01 USDC as the minimum allowed price.
- **2026-08-03:** Ahmed Ali clarified that tracks are judged independently, but an all-track project must show the full Miner → WASM Script → Application lifecycle; the Application should route to other Miners too, not only the participant's own service.
- **2026-08-03:** Advisor re-reviewed the new semantic-scoring contract and recommended canonical JSON internally, one shared minimal text renderer externally, a separate leakage-safe Brier benchmark, exact one-hour provider points, and no unproven probability aggregation.
- **2026-08-03:** Implemented the local provider adapters, canonical contract, deterministic public renderer, development fixtures, Brier harness, tests, and three draft Miner YAMLs. All 11 local tests pass; no live services are deployed.
- **2026-08-03:** Advisor re-reviewed Ahmed's all-track clarification and revised the architecture: one public OathCast Miner backed by the three adapters, an identity-blind Script Author, and an Application that must use independent external Miners and survive with OathCast disabled.
- **2026-08-03:** Added `miners/oathcast-weather.yaml` as the sole canonical registration candidate; provider-specific YAMLs remain internal adapter experiments.
- **2026-08-03:** Ahmed clarified that no public Script Author harness is released yet; official Intent boilerplates, harness, and guides are still being finalized internally.
- **2026-08-03:** Implemented the first local vertical slice: HTTP Miner service, provider failover, capability discovery, cross-Miner Application router, development evaluator proxy, fixtures, and 22 passing tests.
- **2026-08-03:** Created the `/Users/femi/Documents/My-Projects/oathcast` project folder and this handoff checkpoint.
- **2026-08-03:** Added the deployment package, `.env.example`, x402 challenge/retry boundary, Telegraph dispatcher client, live-shaped registry parsing, and read-only live discovery. The live endpoint returned weather-capable Miners 211 (OpenWeatherMap), 212 (WeatherAPI), and 18 (Zeus/Bittensor SN18), each with a 0.01 USDC floor. No paid request was made; all 30 local tests pass.
- **2026-08-03:** Advisor reviewed the rollout and returned REVISE: harden challenge validation, dynamic-price caps, HTTPS, duplicate/unknown-settlement handling, external response normalization, and Miner-origin authentication before deployment or payment. Implemented those safeguards, `PORT` support, non-signing payment preflight, and 38 passing tests. No host was provisioned and no funds were used.
- **2026-08-03:** User authorized AWS setup after confirming the enhanced Free Tier. Launched the `OathCast-weather-router` EC2 staging host (`i-0c4948734b7a6326c`) in `eu-north-1` as `t3.micro`, using security group `oathcast-web` and key pair `oathcast-ec2`; at launch time no application image, wallet, paid request, or Miner registration had been added.
- **2026-08-03:** Installed Docker on the AWS host, built `oathcast:staging`, configured an owner-only API-key env file, and launched the `oathcast` container with restart policy. Health, auth rejection, and authenticated Lagos forecast smoke tests passed; the container had restart count `0`.
- **2026-08-03:** Removed temporary EC2 Instance Connect and stale SSH inbound rules after diagnostics and HTTPS rollout; briefly re-added only the AWS-managed EIC prefix-list rule for DuckDNS updater setup, then removed it again. The security group now exposes only ports 80 and 443; port 8080 remains private. No wallet, paid request, or Miner registration was added.
- **2026-08-03:** Added the local `Caddyfile`, rebound the Miner to loopback port 8080, deployed the persistent Caddy reverse proxy, and verified health/authenticated forecast behavior through the public HTTPS hostname.
- **2026-08-03:** Signed into DuckDNS, created `oathcastcourt.duckdns.org`, and corrected its A record from the VPN/hotspot auto-detected address to EC2 `13.49.229.253`.
- **2026-08-03:** Advisor HTTPS rollout review was applied: request-body configuration was removed pending a pinned Caddy version, persistent Caddy mounts were preserved, TLS/redirect/auth/loopback checks passed, and the temporary EIC prefix-list rule `pl-0bd77a95ba8e317a6` was removed after deployment. DDNS synchronization is now installed; API-key rotation remained pending at this checkpoint and was completed on 2026-08-04. The existing key was used only through a local secret file for a near-term smoke test and was never printed.
- **2026-08-03:** Installed the root-only DuckDNS updater and five-minute systemd timer on EC2; the first scheduled run returned `DuckDNS update OK`. Removed temporary EIC access afterward; final security group is HTTP/HTTPS only.
- **2026-08-04:** Ahmed reconfirmed that multiple YAMLs count as independent Miners and that proxied third-party APIs are acceptable. Architecture unchanged: one canonical public OathCast Miner; provider YAMLs remain non-registration experiments.
- **2026-08-04:** Ahmed clarified the payment/counting distinction: Telegraph uses Base Sepolia USDC rather than a custom hackathon token; its node records Miner-served requests regardless of supported payment method; every Miner YAML must still declare at least `0.01 USDC`. Direct upstream calls remain outside Telegraph demand evidence.
- **2026-08-04:** Verified the stage-1 pricing configuration: all four Miner drafts, including the canonical `miners/oathcast-weather.yaml`, declare `min_price_usdc: 0.01`. Full local regression remains green at 54 tests; fixture scripts pass; no wallet, live payment, or network request was used.
- **2026-08-04:** Added project-owned Miner draft validation, release manifest generation, release identity headers, `/readyz`, request IDs, dual-key rotation, rate limiting, public smoke checks, Application request correlation through HTTP/x402 headers, decimal-price normalization, timestamped hashed discovery snapshots, and a fixture-only Application decision/resolution demo. Full local regression now passes 57 tests.
- **2026-08-04:** Read-only Telegraph integrations discovery was rerun and archived at `docs/telegraph-integrations-2026-08-04.json`: weather-capable Miners 211, 212, and 18 were visible at 0.01 USDC each. No payment or qualifying traffic occurred; registry state remains volatile.
- **2026-08-04:** Ahmed clarified the qualifying request path: real Application calls must flow through Telegraph; Engine auto-routing is optional; agents may call Miners directly through Telegraph and those requests count; every such request must use x402 or another supported payment method. This closes the Engine-routing unknown. Remaining gates are genuine Track 3 demand, payment settlement, Miner registration, and official WASM/ground-truth artifacts.
- **2026-08-04:** Built and locally smoke-verified release `oathcast-2026-08-04-hardened-v3` with source digest `2caa7e23152e54de6a1e231312c04a28ae1c5b557395a70474c6dd6ab4d8e9da` and image digest `sha256:f94a69426fe98acd7f664a8a7851f0abeb941e544eab7cff3cfb661562275fe0`. The public EC2 host remains on the older image because port 22 is closed; no security-group change was made.
- **2026-08-04:** Focused release-readiness advisor review returned **REVISE**. P0 gaps are stale public deployment, no registered/discoverable Miner, no verified external Miners or Track 3 Application traffic, unvalidated signer/settlement flow, unproven request-qualification evidence, and the unreleased official WASM/ground-truth artifacts. Next gate is to redeploy the exact tested artifact, verify public lifecycle behavior, and obtain written qualification semantics before generating threshold traffic.
- **2026-08-03:** Final gap audit with advisor returned **REVISE**: infrastructure is sound, but the qualifying loop is unproven. P0 work is official harness/YAML validation, payment feasibility and one paid end-to-end request, threshold/operator correctness, cutoff enforcement with immutable receipts, fail-closed authentication, per-Miner adapters, a real Application plus ground-truth resolver, and 100 legitimate requests with at least three active Miners in the same Intent. UI polish, extra providers, and regional hardening are lower priority.
- **2026-08-04:** Focused advisor review confirmed the exact v1 predicate must be finite `threshold_mm == 0.1` with `operator == ">"`; unverified adapters cannot satisfy the public contract; completion must be checked against cutoff; and event receipts should be insert-only, replayable, conflict-safe, and hashed. Implemented the contract, fail-closed production auth, SQLite receipts, cutoff checks before and after upstream fetch, receipt hash header, and 45 passing tests. The deployed EC2 image is intentionally not updated yet.
- **2026-08-04:** Payment checkpoint advisor returned **BLOCKED** for live spending: the configured dispatcher is HTTP and no signer/SDK, funded wallet, settlement proof, or reconciliation path is established. Implemented the offline-safe portion only: `ValidatedPaymentAuthorization` binds the signer to one exact challenge option, `SqlitePaymentJournal` persists spend and outcome state across restarts, unresolved outcomes block retries, and preflight remains reservation-free. The payment suite and full suite pass with 49 tests; no funds or paid request were used.
- **2026-08-04:** Evidence-loop advisor returned **PROCEED** for a local persistence/resolver slice independent of payment, networking, UI, and the unreleased WASM ABI. Implemented `SqliteCaseStore` with canonical question/reply/decision/observation/resolution hashes, fixed-point precipitation, exact-window validation, explicit unresolved data, append-only resolution revisions, and `ApplicationWorkflow` orchestration. Full regression suite now passes 54 tests. Observation-source independence, deadline/finality policy, and official ground-truth integration remain open.
- **2026-08-04:** Added `DemandLedger` and `scripts/inspect_demand.py`. Telegraph-routed Application responses can now be retained with correlation, payment-settlement, source/fixture, HTTP-status, and integrity-hash provenance. Only a conservative settled-successful local candidate is counted, while the ledger explicitly leaves the official Telegraph count `null`; fixtures, direct calls, unpaid preflights, and unverified payment responses remain excluded. Full regression suite now passes 61 tests.
- **2026-08-04:** Hardened the request limiter to use trusted remote-address keys with idle expiry and bounded LRU state, added case-insensitive receipt-header handling to the smoke script, and passed 64 local tests plus compile and draft-validation checks.
- **2026-08-04:** Built and deployed release `oathcast-2026-08-04-hardened-v3-2` from source digest `df62b88dbb4a4e6661e944a910269058f910d9f1201c27b5ac1833f19496d976`. Verified disposable and public authenticated forecasts, readiness/auth boundaries, durable receipt replay after restart, the public smoke artifact, and the final AWS security group with only ports 80 and 443. The old v3.1 container remains stopped for rollback; no paid Telegraph demand or registration was created.
- **2026-08-04:** Added the non-secret recovery slice: SQLite online backup plus integrity/row-count restore checks, a scheduled external-canary workflow entry point, and an Application ablation mode proving that valid external Miner replies still influence a decision when the owned OathCast Miner is disabled. The local regression suite now passes 66 tests. These changes are not included in the already-deployed v3.2 image; a future release rebuild remains optional and pending.
- **2026-08-04:** Completed the staging API-key rotation. Public HTTPS observed old/new `200/200` during overlap, then retired-old `401` and active-new `200` after container recreation; health/readiness remained `200` and the temporary EIC rule was removed. The local backup intentionally retains the retired credential only for the stopped rollback image; no secret was added to repository evidence.
- **2026-08-04:** Reviewed the official Telegraph Protocol whitepaper. It confirms permissionless Miner registration and the protocol-native x402/receipt flow, but introduces Machina bonds, credential hashes, and an `on_chain_output` schema whose Hackathon 1 applicability is not yet confirmed. Added this as a registration-compatibility checkpoint; no Machina was purchased and no registration transaction was submitted.
- **2026-08-04:** Whitepaper integration advisor returned **REVISE**. It confirmed the Flow/Workflow split and cross-Miner Application design, but found that a settlement header was being treated as verified and that protocol receipt provenance was discarded by the router. It also flagged whitepaper-versus-current-YAML registration terminology and ambiguous price units. Added `registration.py`, `protocol.py`, explicit micro-USDC handling, deadline checks, independently verified settlement state, immutable demand-event triggers/hash verification, and the authority matrix. Machina bonds, registration transaction encoding, WASM registration, escrow, MPC, mainnet, and automated paid batches remain gated.
- **2026-08-04:** The whitepaper integration slice passed the full 77-test suite, compile checks, all four Miner-draft validations, and the owned-Miner-disabled Application demo. The new code is local only; the deployed v3.2 staging image has not been rebuilt or replaced.
- **2026-08-05:** Ahmed clarified that Hackathon 1 removes the Machina bond, the integration-interface YAML overrides the whitepaper and is still being frozen, released hashes/schema URI requirements apply with pre-submission validation, and served request/payment records are public on-chain/Explorer evidence. He confirmed the Explorer is the current checking path and API docs will follow. Updated the authority matrix and remaining gates; no code or AWS deployment change was needed.
- **2026-08-05:** Implemented the immediate local preparation slice: `scripts/demo_application.py --format markdown` now presents the cross-Miner decision, external influence, durable case hashes, raw responses, later resolution, and owned-Miner-disabled ablation; `scripts/create_registration_draft.py` generated `artifacts/registration-drafts/oathcast-weather-registration-draft.json` with explicit draft/non-submission claims; added human and JSON Explorer evidence templates; added repository/canary setup guidance, secret/database ignore rules, and least-privilege/concurrency protections to the GitHub workflow. Full local regression now passes 78 tests and compile/draft validation passes. The project is not yet pushed to a hosted Git remote because the available GitHub CLI session is unauthenticated; AWS remains on v3.2 and was not redeployed.
- **2026-08-05:** User reviewed the Hackathon judging page and recorded the current engagement/X weights: 25% for Track 1, 10% for Track 2, and 25% for Track 3. Updated judge strategy: evidence-led public updates are a major workstream for Miner and Application, while Script Author effort remains concentrated on evaluator quality, ranking accuracy, anti-gaming, and harness compatibility.

## 19. Handoff maintenance protocol

Update this file whenever any of the following occurs:

- An official Discord or documentation answer resolves an unknown.
- The project concept, track strategy, architecture, schema, or data source changes.
- A build, test, integration, payment, deployment, or live-request result is obtained.
- A blocker, risk, eligibility issue, or fallback decision appears.
- A milestone is completed or the immediate next action changes.

Every meaningful update should include the date, the evidence/source, the decision made, and any remaining uncertainty. Do not silently rewrite an old decision; mark it as superseded when new evidence changes it. Future sessions should read this file first and update it before handing work back.
