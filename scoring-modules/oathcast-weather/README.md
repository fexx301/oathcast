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

The exact rank-only candidate remains pinned at
`ipfs://QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`; an independent re-fetch
confirmed byte identity. Current-registry enrollment and Intent binding are now
proven on-chain, but the portal Dashboard/API still reports `wasm_count: 0` for
the OathCast wallet. Validator Stage 1 processing, Ahmed's reported `0.60`
per-Intent threshold result, and Stage 2 promotion remain unobserved. Telegraph
reported an IPFS gateway timeout as the indexing cause and later suggested
another re-registration after its indexing fix was merged; those are user-relayed
operational statements, not proof of validator processing or authorization for
another transaction. No additional registration has been executed or
authorized.

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

The Go harness uses wazero `v1.12.0` and checks the published ABI, deterministic
fixture ordering, repeated-call behavior, malformed UTF-8, invalid pointers,
bounded allocation traps, import/start-section absence, and score range.
Telegraph's unmodified official tester also ran successfully against the rank
path (example case `0.8500`). Ahmed reported that a registered candidate must
score at least `0.60` on its Intent, but the validator corpus and aggregation
formula are not independently documented. The weakest known valid local
paraphrase scores `0.5875`; that is hidden aggregate risk, not proof of live
threshold failure. No validator acceptance or threshold pass has been observed.

The fixture corpus is `fixtures/wasm_scoring_cases.json`; Python tests validate
its three-string shape and fatal-zero/order constraints. The CI job is
`.github/workflows/ci.yml` (`wasm-scorer`).

## Evidence status

The current rank-only release candidate was produced by two isolated clean
builds with the pinned Rust `1.95.0` toolchain. Their bytes were identical, and
the size, SHA-256, and raw-byte Keccak-256 were independently checked:

| Measurement | Value |
|---|---|
| Reproducible clean-build comparison | Two clean builds were byte-identical |
| WASM byte size | `16,292` bytes |
| WASM SHA-256 | `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af` |
| Raw-byte Keccak-256 (portal-compatible) | `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8` |
| Fixture SHA-256 | `ceaa3d168b78f7eef1f95b70af940b3d117b181d7f55879c8e9e01c595f7303d` |

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

[`release-evidence.json`](release-evidence.json) records the current rank-only
candidate, its clean-build hashes, local Stage 1 checks, and the historical
artifact metadata separately. The wazero suite validates the current ABI and
behavior.

## Registration boundary

The two obsolete-registry confirmations remain historical failures. The later
corrected transaction `0x3997...e51e` is a verified on-chain registration in the
current registry at ID `7`, with the exact rank-only bytes and
`WEATHER_FORECAST` binding. It does not by itself prove Dashboard indexing,
validator Stage 1 acceptance, the reported `0.60` threshold result, or Stage 2
promotion.

The current action is to observe or obtain the authoritative Dashboard/validator
record for ID `7`. No replacement registration is currently authorized. If
Telegraph requires another transaction despite the
non-empty current-registry record, first decode a new complete wallet wrapper and
nested call, then obtain fresh explicit user authorization before confirmation.
