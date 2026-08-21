# OathCast Weather Scoring Module

This directory contains OathCast's Track 2 scoring module: a standalone,
identity-blind Rust `no_std` evaluator compiled to WebAssembly. `rank_answer`
receives the question, the ground-truth answer, and one Miner's raw answer and
returns a deterministic `f32` score in `[0, 1]`. OathCast's local Brier
benchmark remains a separate weather-quality analysis and is not substituted
for the Telegraph scorer.

## Current ABI

The updated Telegraph scoring-module guide defines these required function
exports:

| Export | Signature | Purpose |
|---|---|---|
| `alloc` | `i32 -> i32` | Allocate an input buffer in the module's exported memory. |
| `dealloc` | `(i32, i32) -> void` | Release an exact pointer/byte-length allocation. |
| `rank_answer` | `(i32, i32, i32, i32, i32, i32) -> f32` | Score `(question, ground_truth, miner_answer)` pointer/length pairs. |

The module also exports linear memory named `memory`. Inputs are UTF-8 bytes and
lengths are byte counts, not character counts. The six `rank_answer` parameters
must remain ordered as question pointer/length, ground-truth pointer/length, and
Miner-answer pointer/length. Blank Miner answers return exactly `0`, and scores
are finite and clamped to `[0, 1]`. The build has no function or memory imports,
no WASM start section, no network/filesystem/WASI dependency, and uses a finite
4 MiB linear-memory maximum. The standalone binary must remain at or below
Telegraph's 32 MiB limit.

### Deprecated export history

An earlier portal/API response appeared to require `breakdown_answer` even
though the public guide did not define it. OathCast temporarily exposed a scalar
alias to investigate that discrepancy. Telegraph's updated guide now explicitly
deprecates and removes `breakdown_answer`; no result struct, result-pointer
ownership, lifetime, deallocation, or five-field layout is part of the current
contract. The `alloc`/`dealloc` rules above still govern input buffers. The old
export metadata and parity-test results are retained only as historical
provenance.

The validator evidence is auditable: registration `1`, WASM Keccak-256
`34220f7244084b2542c34b114189963db5924812e170e54997f9241c9b6807ac`,
surfaced `missing required export "breakdown_answer"` through the portal/API;
Telegraph later reported its node-log root cause as
`module[env] not instantiated` and said the breakdown-related rejection is
fixed. Registration `3`, WASM Keccak-256
`25262ecd9fa03a0c56d35cac63baa461a3cde5f11bb039966df431b530a49336`,
was rejected only after loading and returning identical `0.0000` self and
unrelated scores. These records are historical and must not be used as current
registration requirements.

The historical portal ABI was
`registerWasm(bytes32 wasmHash, string wasmUrl, string[] whitelistedUrls)`, and
the portal passed `[]`. Telegraph confirmed that the empty URL allowlist was
valid and was not used internally for ground truths. That path did not transmit
the selected canonical Intent. Transactions
`0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`
and `0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`
both confirmed through delegated wallet wrappers but nested into the obsolete
`0xac683...` deployment. Their displayed IDs `5` and `7` are retained only as
failed historical registration evidence.

Telegraph subsequently reported the Intent binding and registry mismatch fixed.
Live portal build `D8HL6V9WUTFV9A7Ryk0W0`, page chunk
`_next/static/chunks/app/page-abd375eb1c96558e.js`, targets
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and calls
`registerWasm(bytes32,string,string)` with selector `0xfe1e40f7`. Corrected Base
Sepolia transaction
`0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
preserved that exact nested call through the delegated wallet wrapper: current
registry target, zero value, the frozen raw-byte WASM hash, the existing gateway
URL, and canonical Intent `WEATHER_FORECAST`. The current registry emitted ID
`7`, reports `entityCount(2) == 7`, and returns a non-empty `getWasm(7)` containing
the OathCast wallet, candidate hash, URL, and Intent.

The historical registered rank-only artifact remains pinned at
`ipfs://QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`; an independent re-fetch
confirmed byte identity. Current-registry ID `7` remains valid historical proof
of enrollment and Intent binding for those bytes. A later manual retry registered
the same 16,292-byte artifact from wallet
`0x7dc9C9D535B68C3c6273e3323f0e52E5851C3278` as registration `19` through
transaction
`0xa6bc6f653eec4a5c79acac4a6e747222d48fd257367c325cd0e6c0090d321e73`,
using the Dropbox URL
`https://www.dropbox.com/scl/fi/27orv68frtedmkqq1t9wt/oathcast_weather_scorer.wasm?rlkey=9mrm44geuaejp1629zdntfng3&st=sigr9vji&dl=1`.
According to user-relayed Telegraph team confirmation, corroborated by reaching
champion comparison, Stage 1 passed; no separate Stage 1 API field was retained.
Stage 2 rejected the artifact because it ordered the good answer above the bad
answer on `31/32` comparable cases while the live champion did so on `32/32`.
The returned margins were `0.31248063` for
the candidate and `0.37360683` for the champion, with `historical_rows: 0`.
Those margins are diagnostics, not a direct candidate-versus-champion promotion
test.

The 42,798-byte artifact was later manually hosted on Dropbox and
registered for `WEATHER_FORECAST` as registration `41` in Base Sepolia
transaction
`0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`.
Independent postflight verification reproduced that artifact's exact 42,798-byte
size, SHA-256
`4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174`, and raw-byte
Keccak-256
`0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`. Those are
registration `41`'s frozen bytes, not the current local candidate recorded under
Evidence status below. Registration `41` reached champion comparison
(Stage 1 passed) and Stage 2 rejected it at `31/32` candidate ordering wins
versus the champion's `32/32`. Candidate margin/EvalScore was `0.37852418`,
above the champion aggregate margin `0.37360683`; the higher aggregate did not
override the per-case promotion rule.

See
[`../../docs/telegraph-track2-clarification-request.md`](../../docs/telegraph-track2-clarification-request.md)
for the full history and action boundary. The corrected postflight is
[`oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json`](../../artifacts/registration-drafts/oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json).
The historical 16,318-byte scalar-build metadata and hashes remain frozen
provenance; the old bytes are not present in this workspace.

## Scoring behavior

The evaluator is deterministic and weather-domain aware without assuming a
single precipitation-only question shape. It handles:

- binary polarity and polarity/probability contradictions;
- probability agreement when the ground truth contains a probability;
- semantic weather concepts, numeric facts, and concise paraphrases;
- requested UTC time-window violations;
- JSON envelopes such as `content`, nested chat-completion messages, and
  probability fields;
- malformed JSON, blank answers, overlong input, and keyword stuffing as fatal
  zero-score cases.

Input bounds are 8 KiB for the question, 8 KiB for ground truth, and 4 KiB for a
Miner answer. Invalid pointers, allocation pairs, bounds, or UTF-8 are rejected
without dereference. The checked bump allocator traps on exhaustion/oversized
requests, and `rank_answer` resets allocator state after each call so repeated
calls are deterministic.

## Build and test

The Rust toolchain is pinned in `rust-toolchain.toml` (`1.95.0`, target
`wasm32-unknown-unknown`). From the repository root:

```bash
cd scoring-modules/oathcast-weather
cargo fmt --manifest-path rust-module/Cargo.toml -- --check
cargo clippy --manifest-path rust-module/Cargo.toml \
  --target wasm32-unknown-unknown -- -D warnings
cargo test --manifest-path rust-module/Cargo.toml
cargo build --locked --release --target wasm32-unknown-unknown \
  --manifest-path rust-module/Cargo.toml
(cd go-tester && go test -count=1 -mod=readonly ./...)
```

The release artifact is produced at:

`rust-module/target/wasm32-unknown-unknown/release/oathcast_weather_scorer.wasm`

The full Go harness uses wazero `v1.12.0` and checks the published ABI, deterministic
fixture ordering, repeated-call behavior, malformed UTF-8, invalid pointers,
bounded allocation traps, import/start-section absence, and score range.
Rust tests pass `40/40`, the Go/wazero suite passes, and Python discovery passes
`498/498`.
Telegraph's unmodified official tester also ran successfully against the rank
path (example case `0.8500`). According to user-relayed Telegraph guidance, its
Stage 2 fixtures test
factual paraphrase and lexical discrimination, not numeric, time-window, or JSON
cases. Each near-miss pair has a fixed `0.15` margin floor, there are six
near-miss cases, and promotion follows after all six pass. According to the same
user-relayed guidance, the reported `0.60` metric is Spearman rank correlation against
the live champion's historical scores, not a raw scorer threshold or a direct
comparison of the two returned margins.

The revised local build passes 88 synthetic factual-paraphrase pairs with a
minimum local margin of `0.206250`. Its synthetic ordinal Spearman is `0.956604`
for `exact > good > bad` ordering on handcrafted cases. These are development
proxies constructed from the user-relayed fixture category and floor; the
ordinal metric is not comparable to Telegraph's live candidate-versus-champion
correlation. Predicate-family identity, inverse learned-from and lost-to
phrasing, parenthetical commas, coordinated relation swaps, mixed explicit
reversals, partial multi-relation omissions, mixed directed pairs, shared
predicates, bounded anaphoric and passive ellipsis, suffix-bearing surname
aliases, predicate-free completeness, comma and semicolon gapping, subordinate
parenthetical predicates, and novel extra claims across punctuation now have
both Rust and fixture regressions. These results do not prove a Stage 2 or
`0.60` pass.

The fixture corpus is `fixtures/wasm_scoring_cases.json`; Python tests validate
its three-string shape and fatal-zero/order constraints. The CI job is
`.github/workflows/ci.yml` (`wasm-scorer`).

## Evidence status

The current frozen rank-only artifact is a 45,681-byte local candidate. Its
single change from the registered 42,798-byte registration `41` build is a
probability-scan fix in `percent_probability`, which previously abandoned the
scan on the first `%` that carried no parseable number and so discarded a real
percentage later in the same answer. The fix is behaviour-preserving on the
pre-existing corpus: the weakest synthetic margin and the prior fixture results
are unchanged. These bytes have not been uploaded, hosted, signed, registered, or
evaluated by Telegraph. The artifact was produced by two isolated clean
builds with the pinned Rust `1.95.0` toolchain. Their bytes were identical, and
the size, SHA-256, and raw-byte Keccak-256 were independently checked:

| Measurement | Value |
|---|---|
| Reproducible clean-build comparison | Two clean builds were byte-identical |
| WASM byte size | `45,681` bytes |
| WASM SHA-256 | `d108532c673a3f94010b140333037af93e677ae54148d7f67c42fb2fd3ccef95` |
| Raw-byte Keccak-256 (portal-compatible) | `0x537bf9a7da427e292994ecce7f317e187996345a3a4503901b764ddadd9fbc5f` |
| Fixture SHA-256 | `c96960e6a5e0d0d410686bcf9a2c0dece48ec130e19403322355f19ca4096b0f` |
| Rust native tests | `40/40` passed |
| Go/wazero suite | Full suite passed |
| Python discovery | `498/498` passed |
| Official unmodified Telegraph tester | Passed; example score `0.8500` |

Registration `19` evaluated the earlier 16,292-byte artifact, SHA-256
`97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af`,
raw-byte Keccak-256
`0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
That historical registered artifact is retained separately from both
registration `41`'s 42,798-byte artifact and the current 45,681-byte local
candidate.

Registration `41` evaluated the 42,798-byte artifact. Its confirmed
transaction is
`0x4bfdc7a894ca55edbb18c18cd5ee79b32673c8b3f5b8d04ab6bc5e48a458ccf8`; the
hosted-byte re-fetch matched SHA-256
`4c3e91ac887abf492cbc662a2d02e0b0bae906a176b2ae4b7bf986419a2db174` and
raw-byte Keccak-256
`0xd8b298ded6e50a69fd6cc79350a819536927d879c81250924689edbea98517f8`.
The result is a Stage 2 rejection at `31/32` versus the champion's `32/32`, not
a promotion. The complete postflight is
[`oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json`](../../artifacts/registration-drafts/oathcast-weather-wasm-registration-41-postflight-2026-08-17T193636Z.json).

Metadata for an earlier 16,318-byte compatibility build with a scalar
`breakdown_answer` export remains historical provenance; the old bytes are not
present in this workspace. Its SHA-256 was
`95895681d1e82bf01eab35f53af15cbfba8f459deba2b0dbc49e8dcbdeed9bf4` and its
raw-byte Keccak-256 was
`0xa8cbc78d20b46b0aaba89002fdb585dc4f243dd192faff8e5ad271b4ef088b19`.
The updated requirements received on 2026-08-15 supersede its ABI assumptions.
The current guide was independently verified at
`telegraphprotocol/telegraph-docs@cfe6fbda517f09d3097790778d2b9cbaa4d8f272`,
path `scoring/build-a-scoring-module.md`.

[`release-evidence.json`](release-evidence.json) v7 records the current build,
registration `41` and its hosted-byte postflight, the clean-build hashes and
synthetic proxies, registration `19`'s historical Stage 1 confirmation and
Stage 2 rejection, and the registered and historical artifacts separately. The
wazero suite validates the current ABI and local behavior.

## Registration boundary

The two obsolete-registry confirmations remain historical failures. Corrected
transaction `0x3997...e51e` remains verified current-registry history at ID `7`
for the 16,292-byte artifact. Registration `19` later evaluated those same bytes:
user-relayed team confirmation says reaching champion comparison means Stage 1
passed, and Stage 2 rejected them at `31/32` ordering wins versus the champion's
`32/32`.

Registration `41` is the 42,798-byte artifact's retained validator
result: hosted bytes match that recorded registration `41` build rather than the
current 45,681-byte candidate, Stage 1 passed by reaching
champion comparison, and Stage 2 rejected it at `31/32` versus the champion's
`32/32`. No replacement registration is currently authorized. Before any future
confirmation, decode a fresh complete wallet wrapper and nested call for the new
hash, then obtain fresh explicit user authorization. Local pair margins and
synthetic ordinal Spearman are proxies only and must not be presented as a live
pass.
