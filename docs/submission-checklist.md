# Hackathon submission checklist

This is an evidence checklist, not a claim that the corresponding gates are
complete. Mark an item complete only when its artifact is attached and its
source is independently verifiable. `[~]` marks partial or legacy-scoped
evidence that is not sufficient for submission readiness; `[!]` marks an
external blocker that must remain open.

**X account: [@fexx_off](https://x.com/fexx_off).** All three X items below draw
on the same log, kept locally with a URL per publication — record the link when
you post, not later. First publication 2026-08-10. Each track asks separately,
so one post can satisfy more than one line only if its content is actually
relevant to that track.

## Track 1 — Miner

- [x] Public OathCast service package and HTTPS staging path.
- [x] Frozen canonical YAML plus explicit `10000` micro-USDC registration price.
- [x] Provider-native event contract and exact-hour validation.
- [x] Local chronological benchmark with coverage and end-to-end utility.
- [x] Exact 4,960-byte YAML passed the official portal's aggregate validation
  (`valid: true`); durable per-endpoint sandbox cases were not retained.
- [x] Canonical Miner registered and discoverable: on-chain ID `78`, routing ID `64173`.
- [x] Registered `GET /predict` route deployed in `2026-08-16-route-v7` and
  verified through public HTTPS. It shares authentication, rate limits,
  semantic JSON, and receipt identity with `/v1/forecast/point`; near-miss
  paths remain 404.
- [~] Local, undeployed compatibility for the team-requested
  `forecast_hours=1..24&hourly=2t` temperature request is implemented on
  `/predict` and `/v1/forecast/point`, with next-complete-hour UTC alignment and
  RFC3339/Kelvin output. The unregistered candidate schema is checked against a
  real 24-hour service response. This is not live Track 1 evidence and does not
  change the registered YAML; `/v1/forecast/window` retains its legacy
  start/end contract.
- [x] At least three active `WEATHER_FORECAST` Miners observed in the live catalog on 2026-08-13. Recheck at submission; this does not satisfy the separate 100-request condition.
- [~] Telegraph confirmed the earlier leaderboard zero came from `/predict`
  returning 404, which produced `miner_answer=""`. Release v7 fixed that route,
  but an epoch-202 observation still scored OathCast `0`, rank `6/6`. The later
  team diagnosis is that the scorer requests 24 hours while the live Miner
  serves the registered one-hour contract. The local 24-hour fix is undeployed,
  so no corrected live result exists.
- [ ] Official Miner performance evidence from live evaluation.
- [ ] X update evidence tagged `@Telegraphprotoc`.

## Track 2 — Script Author

- [x] Three-input development proxy: question, ground truth, raw response.
- [x] Adversarial corpus and deterministic local robustness report.
- [x] Explicit separation between semantic proxy and Brier domain benchmark.
- [x] Current scoring-module ABI, Rust example, and wazero tester published. The
  required functions are `alloc`, `dealloc`, and
  `rank_answer(6 x i32) -> f32`; `breakdown_answer` is deprecated and removed.
- [x] OathCast `no_std` rank path compiled with pinned Rust and tested with
  `39/39` native Rust tests, the full Go/wazero ABI and adversarial harness,
  deterministic repeated calls, `401/401` Python discovery tests, and Telegraph's
  unmodified official tester (`0.8500` published example score).
- [x] Historical validator evidence retained: the August 14 portal/API response
  surfaced a missing `breakdown_answer` message, while Telegraph later reported
  the node-log root cause as `module[env] not instantiated`; a six-`i32`/`f32`
  probe loaded far enough for the self-match check. These observations are
  historical, and neither the export nor any five-field result is current.
- [x] Current 42,798-byte rank-only revision reproduced and locally verified:
  two byte-identical clean builds;
  SHA-256
  `4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174`;
  raw-byte Keccak-256
  `0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`;
  fixture SHA-256
  `bf4805e71a95379206f3446b8c185c0278a5702e4005fdd5973f24b99a4629f0`.
  `scoring-modules/oathcast-weather/release-evidence.json` v7 records the build,
  registration `41`, local proxy evidence, the historical registered
  16,292-byte artifact, and the 16,318-byte scalar-export metadata separately; the
  scalar-export bytes are not present in this workspace.
- [x] Local factual-paraphrase proxy passes 87 synthetic pairs with minimum
  margin `0.206250` and synthetic ordinal Spearman `0.959623`.
  Predicate-family identity, inverse learned-from and lost-to phrasing,
  parenthetical commas, coordinated relation swaps, mixed explicit reversals,
  partial multi-relation omissions, mixed directed pairs, shared predicates,
  bounded anaphoric and passive ellipsis, suffix-bearing surname aliases,
  predicate-free completeness, comma and semicolon gapping, subordinate
  parenthetical predicates, and novel claims after punctuation have Rust and
  fixture regressions. These cases model Telegraph's clarified
  category and floor only; they are not hidden fixtures, champion historical
  scores, or proof of a live pass.
- [x] Telegraph reported that the breakdown-related validator rejection, Intent
  binding, and registry mismatch are fixed. According to user-relayed team
  confirmation, corroborated by reaching champion comparison, registration `19`
  passed Stage 1 and then reached the Stage 2 rejection for the 16,292-byte
  registered artifact; no separate Stage 1 API field was retained.
- [x] Breakdown clarification closed by the revised authoritative guide. No
  pointer ownership, lifetime, struct layout, return encoding, or parity rule is
  required for the removed export.
- [x] Historical `registerWasm(bytes32,string,string[])` path retained: argument
  three was `whitelistedUrls`, the old portal submitted `[]`, and Telegraph
  confirmed that the empty list was valid and not used internally for ground
  truths.
- [~] Non-empty URL-allowlist semantics remain undocumented for the historical
  ABI, but the corrected portal path uses a canonical Intent string instead and
  OathCast does not depend on that legacy field.
- [x] Canonical Intent association encoded by the refreshed portal path and
  established on-chain by current-registry registration ID `7`.
  Live build `D8HL6V9WUTFV9A7Ryk0W0`, chunk
  `_next/static/chunks/app/page-abd375eb1c96558e.js`, targets
  `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and submits
  `registerWasm(bytes32,string,string)`, selector `0xfe1e40f7`, with the exact
  hash, existing gateway URL, and `WEATHER_FORECAST`.
- [x] Telegraph clarification record updated with the rank-only resolution plus
  deployed-contract, portal-source, registration-ID, artifact-hash, and
  historical validator-error evidence:
  `docs/telegraph-track2-clarification-request.md`.
- [x] Registered 16,292-byte three-function WASM uploaded to the portal's IPFS
  flow and re-fetched by the portal and independently with matching bytes/hash.
  Gateway:
  `https://gateway.pinata.cloud/ipfs/QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`.
  Only `WEATHER_FORECAST` is selected.
- [~] Portal transaction
  `0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`
  confirmed and emitted old-registry ID `5` with the exact hash, URL, zero value,
  and empty allowlist. It remains historical and unmigrated. The Dashboard/API
  reports `wasm_count: 0`; the old `0xac683...` registry has an empty
  `getWasm(5)` and `entityCount(2) == 0`. At that historical pre-registration
  snapshot, the current registry had `entityCount(2) == 6`, with IDs `5` and
  `6` belonging to other authors.
- [x] Transaction, receipt, event, live portal bundle address, and both registry
  read results retained in
  `artifacts/registration-drafts/oathcast-weather-wasm-registration-postflight-2026-08-15T141838Z.json`.
- [x] Corrected inner call simulated read-only from the connected wallet. It
  succeeded and returned prospective registration ID `7`; no transaction was
  generated or broadcast. Preflight:
  `artifacts/registration-drafts/oathcast-weather-wasm-reregistration-preflight-2026-08-15T204924Z.json`.
- [~] Second authorized transaction
  `0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`
  confirmed with receipt status `1`; the portal showed ID `7` and
  `WEATHER_FORECAST`. Decoding found outer target
  `0xdb9b1e94b5b69df7e401ddbede43491141047db3`, selector `0xcef6d209`
  (`redeemDelegations(bytes[],bytes32[],bytes[])`), and inner target old
  `0xac683...`, selector `0x19238d1c`, legacy
  `registerWasm(bytes32,string,string[])`, exact hash/CID, and `[]`.
- [x] Historical second-attempt reads retained: correct registry `getWasm(7)`
  was empty with `entityCount(2) == 6`; old registry `getWasm(7)` empty and
  `entityCount(2) == 0`; Dashboard `wasm_count: 0`. This is a portal
  UI/simulation versus delegated wallet packet split, not the successful
  current-registry registration.
- [x] Complete second transaction postflight retained in
  `artifacts/registration-drafts/oathcast-weather-wasm-reregistration-postflight-2026-08-15T212134Z.json`.
- [x] Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  decoded and confirmed: current target `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`, selector `0xfe1e40f7`, exact
  hash/CID, and `WEATHER_FORECAST`; current-registry event ID `7`,
  `entityCount(2) == 7`, and matching non-empty `getWasm(7)`.
- [x] Corrected postflight retained in
  `artifacts/registration-drafts/oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json`.
- [x] Registration `19` retained for wallet
  `0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278`, transaction
  `0xa6bc6f653eec4a5c79acac4a6e747222d48fd257367c325cd0e6c0090d321e73`,
  the registered 16,292-byte hash, and Dropbox URL
  `https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=sigr9vji&dl=1`.
- [x] Validator result retained: user-relayed team confirmation, corroborated by
  reaching champion comparison, says Stage 1 passed; Stage 2 rejected
  the registered artifact at `31/32` good-over-bad ordering wins versus the live
  champion's `32/32`. Returned diagnostics are candidate margin `0.31248063`,
  champion margin `0.37360683`, and `historical_rows: 0`.
- [x] Telegraph fixture clarification retained: factual paraphrase and lexical
  discrimination only, fixed `0.15` pair margin floor, six near-miss cases,
  automatic promotion after all six pass, and no direct comparison between the
  candidate and champion aggregate margins. The `0.60` metric is Spearman
  correlation against the champion's historical scores.
- [~] The live Spearman threshold is still unobserved because the retained result
  reports zero historical rows. Synthetic ordinal Spearman `0.959623` is a proxy only.
- [x] All registration authorizations used so far, including registrations `19`
  and `41`, are consumed.
- [x] Current 42,798-byte artifact manually hosted and registered as registration
  `41` for `WEATHER_FORECAST` in Base Sepolia transaction
  `0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`.
  Independent hosted-byte verification matched the size, SHA-256, and raw-byte
  Keccak-256 above. Registration `41` reached champion comparison (Stage 1
  passed) and failed Stage 2 at `31/32` candidate wins versus the champion's
  `32/32`; candidate margin/EvalScore was `0.37852418` and champion margin was
  `0.37360683`. The higher aggregate did not override the per-case rule.
  Postflight:
  `artifacts/registration-drafts/oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json`.
- [~] Registration `41` reached the validator but was not promoted. The hidden
  failed pair, champion history, and live Spearman result remain unobserved;
  local synthetic margins and ordinal Spearman remain proxies only. No further
  registration is authorized without a fresh decoded wrapper/nested-call
  preflight and fresh explicit user authorization.
- [ ] Improvement measured against the official baseline.
- [ ] Community/adoption evidence from independent users.
- [ ] X updates tagged `@Telegraphprotoc`.

## Track 3 — Application

- [x] Cross-Miner router with external influence detection.
- [x] Owned-Miner-disabled ablation.
- [x] Durable question, reply, decision, observation, and resolution evidence.
- [x] Local Planning Desk intake pilot.
- [x] One isolated, manually authorized x402 devnet settlement rehearsal independently verified; not Application demand.
- [ ] Reviewed Application payment boundary integrated and verified end to end.
- [ ] Real Application calls routed through Telegraph.
- [ ] Independent Explorer/payment evidence retained per request. *(Telegraph
  confirmed 2026-08-11 that requests served through Telegraph are tracked and
  counted server-side even if they do not surface on the Explorer, so Explorer
  visibility is corroboration rather than a gate. Payment evidence via Solana RPC
  is retained per request and is the primary source for our own claims.)*
- [ ] Real users and legitimate request volume.
- [ ] Resolved receipts and scorecards published.
- [~] X update evidence tagged `@Telegraphprotoc` — **two posts published**:
  2026-08-10, the Application spine,
  https://x.com/fexx_off/status/2086925135554982049; and 2026-08-11, the
  receipt-chain anchor thread,
  https://x.com/fexx_off/status/2087190115395183088 (publishes `head_sha256
  8a63dba5…40e230` over 6 receipts). Both tags confirmed present.
  Left unticked deliberately: X is 25% of this track's score, and two posts are
  cadence starting, not a body of evidence. The next scheduled publications are
  in the local drafting log.

## Submission integrity

- [x] Hosted repository is public or otherwise accessible to judges.
- [x] Secrets, private keys, wallet files, local databases, and credentials are absent.
- [x] README explains what is live, synthetic, pending, and platform-dependent.
- [ ] Demo video shows the Application consuming independent Miners.
- [ ] Every traffic/usage number links to Explorer or payment evidence.
- [ ] No fixture or automated traffic is presented as adoption.
- [x] Limitations and non-binding planning scope are visible.
