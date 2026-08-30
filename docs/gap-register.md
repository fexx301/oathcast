# OathCast gap register

Last reviewed: 2026-08-30

Current baseline: the public Miner runs `2026-08-30-hourly-v18`, source SHA-256
`5aca88c6890443bc086e0c078d3390eead10461fa734206fcc4937758d5e8b6b`, image
`sha256:d3c29fa9f274d520635b6c3ca413c383ba1de958840ed1eb3105aedceda7e859`.
The separate decision UI provides a read-only status shell and development
fixture while its live API remains fail-closed without a runner. V18 retains
the exact registered `/predict` route, the legacy one-hour precipitation
contract, multi-hour `start`/`end` spans of 1 to 168 hours, and the additive
`hourly=2t` compatibility path with
`OATHCAST_ENABLE_TEMPERATURE_WINDOW=true`.

For multi-hour requests, v18 accepts timezone-aware ISO/RFC3339 bounds,
normalizes `start` internally to the nearest whole UTC hour using half-up
rounding, and derives `end` from the original integral-hour duration. An
omitted cutoff serializes as normalized `start` with `cutoff_policy:
"implicit_grace"` and expires at the end of that first normalized hour. An
explicit cutoff is preserved exactly, must not be after normalized `start`, and
receives no grace. The one-hour point and
`forecast_hours=1..24&hourly=2t` contracts are unchanged.

Candidate, public, and post-restart smokes passed 12/12, schema 1/2/3 replay
preserved event, receipt, and public-response identity, and schema 4 replay
survived a candidate restart. The hourly matrix accepted 2, 24, and 168 hours,
rejected 169 hours with HTTP 400, and preserved the legacy temperature route.
The v17 rollback rehearsal passed for legacy receipts and failed closed for a
v18 schema-4 receipt already bound to a different forecast contract. Stopped
`oathcast-v17-rollback-20260830` is the immediate rollback target, and the
disposable v18 candidate was stopped after evidence capture. OathCast remains
registered on Base Sepolia as current on-chain registration ID `245` and active
in the Telegraph dispatcher as
routing ID `64173`, slug `oathcast-weather`. The prior registration ID `78` is
deregistered. Current registration evidence is retained in
`artifacts/registration-drafts/oathcast-weather-cutoff-v2-registration-postflight-2026-08-27.json`.
Full Python discovery passes `539/539`, focused canary tests pass `44/44`, and
the v18 evidence identity loader accepts the retained bundle.

On 2026-08-27, production logs correlated Telegraph epochs 286 and 287 with
authenticated dispatcher requests from `13.237.89.59`. The requests reached
`/v1/forecast/point` but returned HTTP 400: epoch 286 encoded a seven-day prompt
as a 191-hour interval, and epoch 287 supplied a non-ISO `horizon_start`. The
score timestamps lagged the corresponding HTTP requests by about 62 minutes.
The inactive
`miners/candidates/oathcast-weather-window-v3.yaml` candidate corrects the
dispatch guidance: absolute RFC 3339 timestamps with explicit timezones,
exclusive `end`, an inclusive 1-to-168-whole-hour duration bound, seven days as
exactly 168 hours, and no relative API values. Its local raw SHA-256 is
`f7831c43855b074bbae612698ddfe1e0040df2351e7d83d2c7966f03f5562164`.
Three portal sandbox attempts returned HTTP 401: with the documented bearer
default, with an explicit `value_prefix: "Bearer "`, and with a fully prefixed
value entered in the API-key field. A direct production control returned 401
for a bare key and 200 for `Authorization: Bearer <key>`, while authenticated
dispatcher traffic already succeeds under the active bearer registration.
The candidate therefore preserves that known-working auth block; the sandbox
failure is unresolved and the YAML has not been pinned, signed, deployed, or
registered. Registration ID `245` and the v18 runtime remain unchanged.

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
- Deployed additive compatibility for the team-requested temperature shape
  `forecast_hours=1..24&hourly=2t` on `/predict` and
  `/v1/forecast/point`. It starts at the next complete UTC hour and returns only
  `content`, `reference_time`, `hourly`, and `hourly_units` with RFC3339/Kelvin
  values. The path is live but unregistered; the protected registered YAML and
  its one-hour precipitation contract are unchanged. The legacy
  `/v1/forecast/window` path is not publicly exposed and returns 404. The strict
  v18 smoke requires this response and registered/canonical path parity.
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
  visibly, and the workflow requires the temperature-window check. Its checked-in
  local release-evidence pin now resolves the live v18 identity through sanitized
  manifest, final public-smoke, replay, and runtime-evidence artifacts. The
  workflow integrity test invokes the production evidence loader. The remote
  GitHub schedule remains on stale v11 evidence until an authorized commit and
  push.
- Release `2026-08-23-window-v17` extends the normalized `start`/`end` window
  implementation to 168 hours without changing the registered YAML or the
  separate 24-hour temperature compatibility contract. Candidate, public, and
  post-restart smokes passed 12/12; v16-to-v17 replay preserved event, receipt,
  and public-response identity; a dispatcher-shaped 168-hour request persisted
  168 contiguous hours; and 169 hours returned HTTP 400. The candidate receipt
  survived restart, but only its post-restart state was retained, so the
  repository does not claim an independently reproducible candidate pre/post
  hash comparison.
- The historical registered-path 404 is fixed in release
  `2026-08-16-route-v7`: Caddy routes exact `/predict`, the Miner accepts
  `/predict` and `/v1/forecast/point`, both paths share auth/rate limits and one
  receipt, and the smoke test rejects empty or invalid answers. Public HTTPS,
  exact v6-to-v7 replay, restart persistence, SQLite integrity, and sanitized
  logs all passed. A later epoch-202 observation still scored OathCast `0`, rank
  `6/6`, because the scorer requested 24 hours while the then-live v7 Miner
  served the registered one-hour contract. V8 now serves that additive request
  shape, but no corrected official Telegraph evaluation has been observed.
- Release `2026-08-17-temperature-v8` reproduced the exact 66-file source digest
  `edeeaacf470b2207f6bbd8439e0720eff0459d9ca5fe214bc3a09d48ae0c639c`
  on the host and runs image
  `sha256:ae1fff9db3317cd0f6a9d23772df62d93195bd814359e9a3c8d9b21aa0850672`.
  All nine strict public smoke checks passed, the v7 receipt replayed exactly,
  forecast and temperature receipts survived restart, and the live database
  remained at 19 rows with integrity `ok`. Stopped
  `oathcast-v7-rollback-20260817` preserves the immediate rollback target.
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
- Confirmed Miner registration and activation: the 2026-08-27 transaction
  `0x43748dcaac584be466d32d96a45f4293816295579ffa17ee1c20ec4aa288184c`
  emitted current on-chain registration ID `245`; `getMiner(245)`, the portal
  registration API, and dispatcher activation all match the fee address,
  `10000` micro-USDC price, and `WEATHER_FORECAST` intent. The prior ID `78` is
  deregistered. The active portal pin is
  `https://gateway.pinata.cloud/ipfs/QmRSBA6ig48TVq15UWEwdiYq8HYr6woWRPam961j1q1oMu`
  with SHA-256
  `0f66aa0679328afb0f53a0d83b846d2e8407ea062189814e455136121ac90d18`.
  The sanitized current record is
  `artifacts/registration-drafts/oathcast-weather-cutoff-v2-registration-postflight-2026-08-27.json`;
  the 2026-08-13 ID-78 confirmation remains historical.
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
  windows, JSON envelopes, stuffing, and concision. Rust tests pass `40/40`, the
  full Go/wazero ABI and adversarial suite passes, Python discovery passes
  `498/498`, and
  deterministic repeated `rank_answer` calls pass.
  Telegraph's unmodified tester example also returns `0.8500`. An earlier
  validator-observed scalar `breakdown_answer` export and its tests are now
  historical only: the updated guide deprecates and removes that function. The
  historical registered rank-only artifact is 16,292 bytes with SHA-256
  `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af`
  and raw-byte Keccak-256
  `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
  Two earlier transactions remain historical because their delegated packets
  targeted the obsolete registry. Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  registered the exact hash/CID and `WEATHER_FORECAST` on the current registry
  as ID `7`; entity count `7` and matching non-empty `getWasm(7)` prove on-chain
  registration and Intent binding. Registration `19`, wallet
  `0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278`, transaction
  `0xa6bc6f653eec4a5c79acac4a6e747222d48fd257367c325cd0e6c0090d321e73`,
  later evaluated those same bytes from
  `https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=sigr9vji&dl=1`. According to user-relayed Telegraph team confirmation, corroborated by reaching champion comparison, Stage 1 passed;
  Stage 2 rejected the artifact at `31/32` ordering wins versus the champion's
  `32/32`, with candidate margin `0.31248063`, champion margin `0.37360683`, and
  zero historical rows. According to user-relayed Telegraph guidance, these margins are not directly
  compared for promotion.
- The current 45,747-byte factual-paraphrase artifact is reproducible and is an
  unregistered local candidate: it has not been uploaded, hosted, signed, or
  registered. Its single change over the registration `41` bytes is a
  probability-scan fix in `percent_probability`, which previously abandoned the
  scan on the first `%` carrying no parseable number and so discarded a real
  percentage later in the same answer. Its SHA-256 is
  `80f08229391520dc32acbdeffd7588c9ac83ab8636bc4cccb35e894ecf7ff3e8`
  and raw-byte Keccak-256
  `0xd75c3f264a3dc553fa3508ca4e4fe23a25330c11aad5a1e0ba8f1f31fcad1c3d`.
  The fixture SHA-256 is
  `c96960e6a5e0d0d410686bcf9a2c0dece48ec130e19403322355f19ca4096b0f`.
  Two isolated clean builds are byte-identical. All 88 synthetic factual pairs
  pass the reported `0.15` floor with minimum margin `0.206250`; synthetic
  ordinal Spearman is `0.956559`. Predicate-family identity, inverse
  learned-from and lost-to phrasing, parenthetical commas, coordinated relation
  swaps, mixed explicit reversals, partial multi-relation omissions, and mixed
  directed pairs have Rust and fixture regressions. Shared predicates, bounded
  anaphoric and passive ellipsis, suffix-bearing surname aliases,
  predicate-free completeness, comma and semicolon gapping, subordinate
  parenthetical predicates, and novel claims after punctuation are also
  covered. The `release-evidence.json` schema-v7 record labels these synthetic
  results as local proxies.
- Registration `41` evaluated the earlier 42,798-byte artifact, which the
  current local candidate has since superseded. Its SHA-256 is
  `4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174`
  and raw-byte Keccak-256
  `0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`,
  against fixture SHA-256
  `bf4805e71a95379206f3446b8c185c0278a5702e4005fdd5973f24b99a4629f0`.
  Registration `41` independently re-fetched the hosted bytes and matched the
  size and both artifact hashes. The confirmed Base Sepolia transaction is
  `0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`.
  It reached champion comparison (Stage 1 passed) and failed Stage 2 at `31/32`
  candidate ordering wins versus the champion's `32/32`; candidate
  margin/EvalScore was `0.37852418` and the champion margin was `0.37360683`.
  The higher aggregate margin did not override the per-case promotion rule. No
  further registration or replacement is authorized. The complete postflight is
  `artifacts/registration-drafts/oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json`.
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
- V16 runtime deployment checkpoint: the 68-file host payload matched the local
  manifest; focused Python tests passed `94/94` and full discovery passed
  `515/515`; disposable/public smoke, six boundary positions, v12 compatibility,
  and restart replay passed. Telegraph then persisted a normalized 24-hour
  receipt, the live database reached 76 rows with integrity `ok`, and the
  disposable container was removed. Caddy, the registered YAML, registration,
  wallet state, and Track 2 scorer bytes were unchanged by the runtime release.

## Actionable next, not platform-blocked

- The champion baseline is published at
  `https://github.com/telegraphprotocol/telegraph-wasm-baseline` and is the
  held-out signal this work has lacked. It is not a rule engine: it embeds
  question, ground truth and answer with MiniLM-L6-v2 and blends cosine
  similarity `0.25/0.50` with BM25 `0.15` and a length sigmoid `0.10`. Built with
  `--features real_weights` it runs locally through our existing wazero probe,
  and `breakdown_answer` decomposes a verdict into its four signals, so a
  disagreement can be attributed rather than guessed at.
  Measured complementarity, both directions. It solves predicate substitution,
  which was recorded here as needing synonymy this module cannot judge: on the
  Nile case it scores the correct paraphrase `0.704282` above the wrong "deepest
  river" at `0.654421`, where ours inverts. It is weak where we are strong: on
  "Paris is the capital of France.", the wrong verbose "Lyon is the capital of
  France, and that is the established answer..." scores `0.609420`, above a
  correct bare "Paris." at `0.527878`; ours scores that pair `0.334375` against
  `0.781250`.
  The champion inverts 8 of our 10 pools where ours inverts 1. That is **not**
  evidence our module is the better judge: those pools were authored here around
  the failure modes of a lexical scorer, so they are adversarial toward
  surface-similarity judging generally. Reading that number as a verdict would
  repeat the overfitting error in the opposite direction.
  Indicated direction, not attempted, because it is an architectural decision
  rather than a fix: a semantic signal for paraphrase coverage combined with the
  discrete contradiction rules for entity binding would beat either alone. The
  champion's margins on cases it gets right are thin, typically `0.005` to
  `0.06`; ours are wide where the rules fire and absent where they do not.

- Revert the two zeroing rules introduced after registration `96`. Registration
  `98` won `28/32` where the previous three builds all won `31/32`, so those two
  changes cost three cases. `relation_mismatch` was promoted from the ambiguity
  path, which caps at `0.49`, to `contradicted`, which returns zero, and
  `question_entity_slot_substituted` returns zero as well; a correct answer that
  trips either falls from a capped score that could still win to zero, which
  cannot. Score stddev rose from `0.29768366` to `0.33863735`, consistent with
  more answers being driven to zero.
  Reverting will make the local ranking numbers worse: the `authorship_entity_swap`
  tie and the `shared_token_distractor` inversion both return, and the ratcheted
  floors will need loosening back. That conflict is real and the external measure
  is the one that decides promotion.
- Do not tune the scorer against locally authored corpora again without a held-out
  check. Across four registrations local ranking improved monotonically while the
  scored result went neutral, neutral, neutral, then down. Generated-pair
  inversions fell from 66 of 375 to 9 and every suite stayed green while the win
  count fell from 31 to 28. Prefer a cap over a zero unless a contradiction is
  certain, because a zero can only remove a win a cap might have kept.

- Registration `96` was rejected at Stage 2 on 2026-08-19 with candidate wins
  `31/32` against a champion at `32/32`, the same count as registrations `19` and
  `41`. The aggregate margin moved across the series, `0.31248063` then
  `0.37852418` then `0.37149292`, while the win count never did. Registration 96
  changed scoring behaviour substantially, moving whole answer classes from
  `0.862500` to `0.000000` and taking local generated inversions from 66 of 375 to
  9 of 375, and still did not move it. That is evidence the single failing case is
  not an instance of any inversion class fixed so far.
  Leading hypothesis: it is a tie, not an inversion. Telegraph requires the good
  answer to rank *above* the bad one, so an equal score is not a win, and the
  scorer's hard ceilings `score.min(0.49)` and `score.min(0.30)` collapse every
  answer above them onto a single value. Locally the `authorship_entity_swap` pool
  ties a correct answer against a wrong one at exactly `0.490000`, with four
  candidates sharing that value. Ceilings are structural and were untouched by all
  three builds, which fits an invariant single-case failure.
  Fix direction: make the ceilings order-preserving, mapping scores above a
  triggered ceiling into a narrow monotone band ending at it, so two defective
  answers of different quality stay distinguishable while both stay capped. This
  only helps where the pre-clamp ordering is already correct; where it is not, a
  tie becomes an inversion, which is no worse for Stage 2 but no better. Unverifiable
  against Telegraph's hidden fixtures, whose per-case scores they have said cannot
  be disclosed.
  Worth doing on ranking grounds regardless of promotion: a scorer that gives a
  correct and a wrong answer the same score cannot rank them at all, which is the
  property Telegraph's team said actually matters.

- Make the scorer produce identical results under both wazero engines. Telegraph
  was told about the amd64 compiler divergence on 2026-08-19 and replied that
  they run a single validator today, so it does not affect them much yet, that
  they will move to deterministic execution as they scale to multiple validators,
  and that they have not because deterministic execution is roughly 10 to 50
  times slower. That answers the runtime question by implication: the 10-to-50
  figure is the interpreter-versus-compiler tradeoff, so their validator is
  almost certainly running the configuration where this module diverges.
  Their single validator does close the fairness half. Every miner is scored on
  the same machine, so identical answers get identical scores, and the
  multi-validator inconsistency concern is deferred. It does not close the half
  that is ours: whether the scores their machine computes match the scores
  measured here. Validator count has no bearing on that.
  Measured direction, which corrects an earlier assumption: in all 84 diverging
  generated pairs the compiler raised the correct answer to exactly `0.490000`
  and never moved the wrong answer, so every margin widened. The mechanism pulls
  low scores up to `0.490000` regardless of correctness, and these templates make
  the correct answer the lower-scoring one; in the curated pools the opposite
  occurred once, a wrong answer rising from `0.150000` and narrowing a margin. So
  current exposure is favourable to neutral on the sample measured, and the sign
  is a property of which side scores low rather than a safety property.
  Because Telegraph will not change execution before multi-validator scaling,
  waiting does not close this. The tractable route is to localise the miscompiled
  construct, which is feasible since the divergence reproduces on a single call
  into a fresh instance with one known input, and to write the scorer so both
  engines agree. Filing upstream with wazero remains optional and unfiled.
- Superlative substitution is the last measured ranking defect and is
  deliberately unfixed. 9 of 375 generated pairs replace the ground truth's
  attribute with a different one, so "the longest river in Africa" becomes "the
  deepest river in Africa". That is the same surface operation as a correct
  paraphrase, where "the tallest mountain" becomes "the highest peak", and
  separating them needs a synonym lexicon this module does not have. A heuristic
  would penalise exactly the paraphrases Telegraph's fixture category rewards, so
  the ratcheted ceiling records the defect rather than trading a measured loss for
  an unmeasured one. Reopen only with a way to decide synonymy, not with a
  keyword list.
- The weather-question classifier still over-triggers on a lowercase weather word
  used non-meteorologically, so "Is ice less dense than water?" is scored as a
  forecast. The capitalised case is fixed. Closing the lowercase case means
  dropping the weather-concept-plus-binary clause, which changes how a genuine
  weather question with no temporal cue classifies, so it is asserted as a known
  limitation in the native tests rather than guessed at. Worth revisiting before
  any future registration, because Telegraph's fixture category is factual
  paraphrase rather than weather and every such misroute applies a probability
  ceiling to a correct answer.

- Keep deployed v18 stable and retain stopped `oathcast-v17-rollback-20260830`
  as the immediate rollback target. The local recurring workflow now points to the
  checked-in v18 runtime-evidence file and validates its linked manifest and
  public smoke with the same loader used by the scheduled canary. Keep that
  integrity gate green before treating a scheduled run as current-release
  evidence. Preserve the current production database on rollback; v17 may not
  replay the schema-4 receipt already committed by v18. The remote GitHub
  schedule remains on stale v11 evidence until a separately authorized commit
  and push.
- Remove the temporary inbound SSH security-group rule manually after remote
  evidence work, then verify HTTPS remains healthy and external ports 22 and
  8080 are closed. AWS CLI credentials are unavailable, so this remains the
  sole operational cleanup item.
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
  requirement. Preserve current-registry ID `7`, both obsolete-registry packets,
  and registration `19` as the historical chronology for the 16,292-byte
  artifact. Preserve registration `41` as the 42,798-byte Stage 2
  rejection. Do not infer either hidden failed pair from synthetic cases; keep
  every local pair margin and Spearman result labeled as a proxy. No further
  registration or replacement is authorized. Any future attempt requires a
  fresh decoded wrapper/nested-call preflight for the new hash and fresh
  explicit user authorization.
- Write the payment-boundary ADR and threat model, then implement one private,
  authenticated, allowlisted, transactionally budgeted Solana request with a
  durable payment journal. Do not enable the public decision endpoint first.

## Blocked on authorization, external evidence, or remaining documentation

- Release `2026-08-30-hourly-v18` retains the multi-hour `start`/`end` branch
  introduced by v10 on the registered `/predict` route, so Telegraph's valid
  1-to-168-hour `WEATHER_FORECAST` requests are answered instead of refused. The
  response is structurally
  compliant, carrying both `content` and the `probability` the registered
  `output_schema` requires, and the YAML's `input_schema` constrains `start` and
  `end` only to `format: date-time`. It is not semantically declared: in a
  multi-hour response `probability` is the maximum one-hour precipitation
  probability inside the span, reported alongside an explicit
  `probability_semantics` field, whereas the registered YAML describes a
  one-hour event probability. Treat the multi-hour branch as provisional until
  the window semantics are declared and re-registered, or isolated from the
  registered route. This is the same class of recorded deviation as the additive
  `hourly=2t` shape above, and it was accepted for the same reason: refusing the
  request returned no temperature and scored zero.
- The previous prose descriptions in the pinned registered YAML were stale
  against the deployed service: `start`/`end` were described as "the one-hour
  forecast window", and `cutoff` as "defaults to one hour before start", which
  remains true for the one-hour point contract but not for a window request. The
  cutoff-description re-registration completed on 2026-08-27. V18 still
  serializes an omitted window cutoff as the normalized opening, but marks it
  `cutoff_policy: "implicit_grace"` and uses the end of the first normalized
  hour as the effective issuance deadline. The active portal pin and its
  deliberate byte difference from the local source snapshot are recorded in the
  current postflight artifact.
- **Historical incident, superseded by v16:** Telegraph sent two authenticated
  24-hour `start`/`end` requests from dispatcher
  IP `13.237.89.59` on 2026-08-19 at `11:45:48Z` and `11:50:48Z`. Both omitted
  `cutoff` and failed with HTTP 400 because `horizon_start` was not aligned to a
  whole UTC hour. The pinned YAML says only `format: date-time`; its `start` and
  `end` descriptions do not state that minutes, seconds, and fractional seconds
  must be zero. Telegraph asked for that requirement to be explicit in the YAML.
  V16 now absorbs the dispatcher's timestamp choice by normalizing multi-hour
  bounds internally; only an explicit cutoff remains exact and unrounded. A
  At that historical point a local prose edit could not affect registration `78`,
  because the YAML was content-addressed. The later authorized replacement is
  recorded separately under current registration `245`.
- **Superseding deployed contract (2026-08-19):** the prior strict whole-hour
  requirement remains historical incident evidence. V16 accepts aware
  ISO/RFC3339 timestamps on multi-hour requests, rounds `start` half-up to a
  whole UTC hour (`:30` and later rounds up), and derives `end` from the original
  integral-hour duration. Omitted cutoff uses the first normalized hour as an
  auditable implicit grace window; explicit cutoff remains exact. Focused tests
  passed `94/94`, full Python discovery passed `515/515`, public boundary and
  replay gates passed, and Telegraph subsequently persisted a normalized
  24-hour receipt. The registered YAML was not edited, uploaded, or
  re-registered.
- The deployed `2026-08-30-hourly-v18` service answers the additive
  `forecast_hours`/`hourly=2t` shape on the registered `/predict` route with
  `OATHCAST_ENABLE_TEMPERATURE_WINDOW=true`, but the registered YAML does not
  declare that response. `output_schema.required` is `[content, probability]`
  and `signal_mapping.confidence_field` is `probability`, while the temperature
  response returns `content`, `reference_time`, `hourly`, and `hourly_units`
  only. `probability` is omitted by design: a 2 metre temperature series has no
  event probability, so there is no honest value for that field. This is a
  deliberate, recorded deviation, not an accepted permanent state.
- Reconciling the prose required re-registering the Miner, so it was
  authorization-gated rather than a documentation edit. That action completed
  under current registration ID `245`; the active portal URI/hash, the previous
  ID-78 pin, and the local-source hash comparison are recorded in the current
  postflight artifact. This authorization remains separate from any Track 2
  scoring-module authorization.
- The deviation is latent rather than active. The registered YAML declares
  `start` and `end` as required and never mentions `forecast_hours`, `hourly`,
  or `2t`, so a dispatcher building requests from it does not send the
  triggering shape. Disabling the flag does not remove the exposure: it converts
  an undeclared `200` into a certain `400 temperature compatibility window is
  disabled` for the same request. The flag therefore stays enabled until the
  YAML catches up. Miner scoring is unaffected because `content` is present, and
  schema-v3 and schema-v4 receipts persist and replay normally.
- Telegraph reported that the breakdown-related rejection, Intent binding, and
  registry mismatch are fixed. The August 14 portal/API response remains
  historical surfaced output saying `missing required export
  "breakdown_answer"`, while Telegraph's node logs identify the underlying cause
  as `module[env] not instantiated`. Both rank-only artifacts have no import
  section.
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
  `entityCount(2) == 7`, and returns matching non-empty `getWasm(7)`. Preserve it
  as historical proof for the registered 16,292-byte artifact.
- According to user-relayed Telegraph team confirmation, corroborated by reaching
  champion comparison, registration `19` passed Stage 1 and failed Stage 2 at
  `31/32` versus the champion's `32/32`. According to user-relayed Telegraph
  guidance, there is a fixed `0.15` floor across six factual paraphrase/lexical
  near-miss cases; promotion follows after all six pass, with no direct
  aggregate-margin comparison. The `0.60` metric is Spearman against champion
  historical scores, but the retained validator result has `historical_rows: 0`.
  The hidden pair text, per-pair scores, and champion history remain external
  evidence gaps.
- The six-case guidance and the API's `comparable_cases: 32` were previously
  recorded without reconciliation. Further user-relayed Telegraph guidance on
  2026-08-17 resolves them as one model rather than two: 32 comparable ordering
  cases are tallied and reported for diagnosis, while the six near-miss cases
  carry promotion, so `31/32` is diagnostic output and the six are the gate.
  Both registrations lost exactly one of 32 and neither was promoted, which
  implies the lost case was among the six, though Telegraph has not confirmed
  that directly. One tension survives: the same guidance states there is no
  direct candidate-versus-champion comparison, while the retained rejection text
  does compare `31` against `32`. Read that as win counts being reported but not
  being the promotion rule. Telegraph also confirmed Stage 1 is only a load and
  export check, so passing it is not evidence of scoring quality.
- Telegraph confirmed every Stage 2 case is paraphrase/lexical discrimination in
  the factual Q&A domain, with no numeric, time-window, or JSON cases. Twenty
  local probes in that shape, covering entity swaps inside an identical sentence
  frame, reversed relations, synonym-only rewrites, terse answers against
  verbose ground truth, and on-topic distractors that answer nothing, all clear
  the `0.15` floor by `0.24` to `0.55`. The failing case is therefore not
  reproduced locally, and the per-pair scores Telegraph holds in its node logs
  remain the only way to identify it without guessing.
- Registration `41` is the 42,798-byte artifact result: the hosted bytes
  match the recorded registration `41` build rather than the current local
  candidate, Stage 1 passed by reaching champion comparison,
  and Stage 2 rejected it at `31/32` versus the champion's `32/32`. It was not
  promoted, and the hidden failed pair and champion history remain unavailable.
  All prior authorizations are consumed. Any further attempt requires a fresh
  complete wrapper/nested-call decode and fresh explicit authorization before
  confirmation.
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
