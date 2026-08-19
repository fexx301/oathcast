# Official Miner Portal Compatibility Check

Originally observed 2026-08-09 against the published Telegraph Miner Registry
source; updated 2026-08-13 against the live Telegraph documentation and launch
email, 2026-08-15 against the revised scoring-module guide and corrected WASM
portal deployment, and 2026-08-16 against the confirmed current-registry
registration and postflight reads, then 2026-08-17 against registration `19`'s
historical validator result and registration `41`'s current artifact postflight.

This began as a compatibility audit. On 2026-08-13 the exact canonical YAML was
validated by the official portal with a dedicated Telegraph credential, pinned
through the portal to IPFS, registered on Base Sepolia, and activated in the
Telegraph dispatcher.

## Result

The frozen registered YAML at
[`miners/oathcast-weather.yaml`](../miners/oathcast-weather.yaml) matches the
required shape used by the published portal wizard:

| Portal area | Draft status | Notes |
| --- | --- | --- |
| Basics | Passed | `kind`, `slug`, and `name` are present. Routing ID `64173` is active and distinct from on-chain registration ID `78`. |
| Connection | Passed | Public HTTPS `base_url` is present and passed the portal endpoint validation. |
| Endpoints | Passed | One forecast endpoint declares `WEATHER_FORECAST` and explicit required/optional query parameters. |
| Semantics | Passed | Exactly `WEATHER_FORECAST` is declared; label, confidence, and reason mappings are explicit. |
| Registration inputs | Separate | URI, raw-byte SHA-256, fee address, price, and wallet action are transaction inputs. The optional YAML `on_chain` block concerns ERC-8183 request/response mapping and is not required by the documented minimal Miner example. |
| Live portal validation | Passed | `/api/validate` returned HTTP 200, `valid: true`, and `api_key_stored: true` for the exact 4,960-byte YAML. The response did not expose a durable per-endpoint case list. |
| Pinning | Passed | Portal upload returned `ipfs://QmRTd9ojKSdMvokKj4tUa4MndQhQWHomy1NTLU6Jz4Un7F`; Pinata reproduced the exact frozen bytes/hash. |
| Registration | Passed | Transaction `0x937d45d8…97b5d2` confirmed, emitted on-chain registration ID `78`, and `getMiner(78)` matches the exact approved payload. |
| Dispatcher activation | Passed | Routing ID `64173`, slug `oathcast-weather`, endpoint `GET /predict`, and `WEATHER_FORECAST` are active. |

Immediately before validation, the 41-record live dispatcher response contained
no exact match for candidate ID `64173` or slug `oathcast-weather`. A separate
40-record pre-submit snapshot also had no exact match. Those observations were
pre-registration collision checks, not reservations or proof of global
uniqueness; the current dispatcher now contains the active OathCast record.
Portal YAML validation then passed. The portal response is retained only as a
sanitized aggregate result and does not independently enumerate endpoint test
cases; this limits the retained evidence but is not a separate registration
parameter. The later wallet transaction was deliberately authorized and
confirmed. The post-submit evidence is retained separately so this earlier
validation history remains intact.

## Registration result

The confirmed transaction emitted sequential on-chain registration ID `78`.
The YAML's numeric routing ID remains `64173`; the two IDs serve different
purposes and must not be interchanged. The transaction used an EIP-7702 smart-
wallet wrapper, while the nested call targeted the current Telegraph Diamond
with zero native value. The `MinerRegistered` event and `getMiner(78)` both
attribute the record to `0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE`.

The portal registration API and dispatcher now report the record active. The
full sanitized confirmation is
`../artifacts/registration-drafts/oathcast-weather-registration-confirmation-2026-08-13T1940Z.json`.

The current registration guide identifies Base Sepolia (`84532`), Diamond
contract `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`, and
`registerMiner(string,bytes32,address,uint256,string[])`. The older published
integration-source address `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3` is
historical context for the completed Miner registration and the first failed-to-
index Track 2 attempt. Telegraph reports that mismatch fixed. Live portal build
`D8HL6V9WUTFV9A7Ryk0W0`, chunk
`_next/static/chunks/app/page-abd375eb1c96558e.js`, now targets the current
Diamond and encodes `registerWasm(bytes32,string,string)`, selector
`0xfe1e40f7`, with the exact hash, existing gateway URL, and
`WEATHER_FORECAST`.

## Remaining non-actions

- Do not treat routing ID `64173` as on-chain registration ID `78`.
- Use the current scoring-module contract: `alloc`, `dealloc`, and
  `rank_answer(6 x i32) -> f32`. `breakdown_answer` is deprecated and removed.
  The retained metadata and hashes for the historical 16,318-byte scalar-export
  build document an earlier validator discrepancy only; the old bytes are not
  present in this workspace. The portal/API surfaced a missing-export error, but
  Telegraph later identified the node-log root cause as
  `module[env] not instantiated` and reported the breakdown-related rejection
  fixed. Do not present the historical extra export or parity tests as current
  requirements. The historical registered rank-only artifact is 16,292 bytes and has no
  import section. Its exact bytes are pinned at
  `ipfs://QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`, portal-verified, and
  independently re-fetched byte-identically, with only `WEATHER_FORECAST`
  selected. Two old-registry transactions remain historical: one emitted ID
  `5`, and one emitted ID `7`, but neither created a current-registry record.
  Corrected transaction
  `0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
  used target `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`, selector
  `0xfe1e40f7`, the exact hash/CID, and `WEATHER_FORECAST`. Current-registry
  event ID `7`, `entityCount(2) == 7`, and matching non-empty `getWasm(7)` prove
  registration and Intent binding. Registration `19` later registered the same
  16,292-byte artifact from a fresh wallet and reached the live validator.
  According to user-relayed Telegraph team confirmation, corroborated by
  reaching champion comparison, Stage 1 passed; Stage 2 rejected it at `31/32` comparable ordering wins
  versus the champion's `32/32`. According to user-relayed Telegraph guidance, the hidden cases are
  factual paraphrase/lexical discrimination; each of six near-miss pairs uses a
  fixed `0.15` margin floor, while `0.60` is a separate Spearman correlation
  requirement against champion historical scores. Registration `19` evaluated
  zero historical rows, so it did not observe that Spearman gate.

  The current artifact is instead 46,809 bytes, SHA-256
  `ef687d45cd3cf86fa4e0c56dd01459238370e36b443c7021d58ea152a3049d95`,
  and raw-byte Keccak-256
  `0x71d5f30d96c2bcd15e02f52af933857a51d76e0a381d6779dab414d952179065`.
  Its fixture SHA-256 is
  `c96960e6a5e0d0d410686bcf9a2c0dece48ec130e19403322355f19ca4096b0f`.
  Two isolated clean builds are byte-identical. Rust tests pass `40/40`, the
  full Go/wazero suite passes, and Python discovery passes `498/498`. It passes
  88 synthetic factual pairs with minimum
  margin `0.206250` and a synthetic ordinal Spearman proxy of `0.958926`;
  predicate-family identity, inverse
  learned-from and lost-to phrasing, parenthetical commas, coordinated relation
  swaps, mixed explicit reversals, partial multi-relation omissions, mixed
  directed pairs, shared predicates, bounded anaphoric and passive ellipsis,
  suffix-bearing surname aliases, predicate-free completeness, comma and
  semicolon gapping, subordinate parenthetical predicates, and extra claims
  across punctuation have Rust and fixture regressions. These local
  cases are not the hidden Telegraph fixtures
  or champion history. The official unmodified Telegraph tester returns
  `0.8500`. The registered artifact is the earlier 42,798-byte build, SHA-256
  `4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174` and
  raw-byte Keccak-256
  `0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`; the
  46,809-byte artifact above has not been uploaded, hosted, or registered. The
  user manually hosted that exact 42,798-byte registration `41` build and registered
  it for `WEATHER_FORECAST` as registration `41` in Base Sepolia transaction
  `0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`.
  The hosted URL was
  `https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=jwhbk80f&dl=1`.
  An independent postflight fetch matched the byte size, SHA-256, and raw-byte
  Keccak-256. The validator reached champion comparison, establishing Stage 1
  passage, then rejected Stage 2 at `31/32` candidate wins versus the champion's
  `32/32`. Candidate margin/EvalScore was `0.37852418`, above the champion
  aggregate margin `0.37360683`; the higher aggregate did not override the
  per-case promotion rule. No further transaction is authorized; any future
  attempt requires a fresh complete wrapper/nested-call preflight followed by
  fresh explicit authorization.
- Do not treat local fixtures, direct upstream weather calls, or the capped
  Solana devnet canary as qualifying Track 3 demand.
- Do not claim paid requests, leaderboard performance, or Track 3 demand from
  registration and activation alone.

Authoritative surfaces:

- [Miner Registry portal](https://integrate.telegraphprotocol.com/)
- [Miner Registry source and YAML flow](https://github.com/telegraphprotocol/tg-miner-integration)
- [Hackathon rules](https://hackathon.telegraphprotocol.com/rules)
