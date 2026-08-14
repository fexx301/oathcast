# OathCast Weather Scoring Module

This directory contains OathCast's Track 2 scoring module: a standalone,
identity-blind Rust `no_std` evaluator compiled to WebAssembly. It receives the
question, the ground-truth answer, and one Miner's raw answer and returns a
deterministic `f32` score in `[0, 1]`. It is a protocol-facing semantic scorer;
OathCast's local Brier benchmark remains a separate weather-quality analysis
and is not substituted for the Telegraph scorer.

## Published ABI

The current Telegraph scoring-module guide and official example define exactly
three callable function exports:

| Export | Signature | Purpose |
|---|---|---|
| `alloc` | `i32 -> i32` | Allocate an input buffer in the module's exported memory. |
| `dealloc` | `(i32, i32) -> void` | Release an exact pointer/byte-length allocation. |
| `rank_answer` | `(i32, i32, i32, i32, i32, i32) -> f32` | Score `(question, ground_truth, miner_answer)` pointer/length pairs. |

The module also exports linear memory named `memory`. Inputs are UTF-8 bytes and
lengths are byte counts, not character counts. Blank Miner answers return
exactly `0`. Scores are finite and clamped to `[0, 1]`. The artifact has no
function or memory imports, no WASM start section, no network/filesystem/WASI
dependency, and uses a finite 4 MiB linear-memory maximum. The standalone binary
must remain at or below Telegraph's 32 MiB limit.

### Portal discrepancy

The integration portal's current helper text says a module must also export
`breakdown_answer`, but it provides no signature and the published guide,
official example, and tester specify only the three exports above. OathCast does
not invent an undocumented stub. Treat `breakdown_answer` as a **portal-only,
likely stale but operationally unresolved discrepancy** until Telegraph
publishes an authoritative signature or validator behavior.

The portal also requires selecting Intents in its UI, while the current client
implementation calls `registerWasm(hash, gatewayUrl, [])`. Intent-array
semantics and validator enforcement therefore remain unresolved. This module is
not uploaded or registered as part of this milestone.

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
requests, and the normal `rank_answer` path resets allocator state after each
call so repeated calls are deterministic.

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

The resulting artifact is:

`rust-module/target/wasm32-unknown-unknown/release/oathcast_weather_scorer.wasm`

The Go harness uses wazero `v1.12.0` and checks the ABI, deterministic fixture
ordering, repeated-call behavior, malformed UTF-8, invalid pointers, bounded
allocation traps, import/start-section absence, and score range. Telegraph's
unmodified official tester also ran successfully against the artifact (the
published example case returned `0.8500`).

The fixture corpus is `fixtures/wasm_scoring_cases.json`; Python tests validate
its three-string shape and fatal-zero/order constraints. The CI job is
`.github/workflows/ci.yml` (`wasm-scorer`).

## Evidence status

The final evidence pass used two clean, separate-target release builds with the
pinned Rust `1.95.0` toolchain. Their WASM bytes were identical. SHA-256 and
raw-byte Keccak-256 were independently recomputed against the frozen artifact.

| Measurement | Value |
|---|---|
| Reproducible clean-build comparison | Two clean builds were byte-identical |
| WASM byte size | `16,292` bytes |
| WASM SHA-256 | `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af` |
| Raw-byte Keccak-256 (portal-compatible) | `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8` |
| Fixture SHA-256 | `ceaa3d168b78f7eef1f95b70af940b3d117b181d7f55879c8e9e01c595f7303d` |

The official guide snapshot used for the ABI decision was commit
`ddca0645a80846dd2843e57847cfd1f00800c6b9`; the official example/tester
snapshot was commit `facdb95e4139fb2075e795c648296a23b7df8ba9`.
The same measurements and explicit non-registration state are frozen in
[`release-evidence.json`](release-evidence.json). The wazero contract test
checks the built artifact's byte size and SHA-256 against that record.

## Registration boundary

Compiling and testing this module are authorized local work. IPFS upload,
hosted-byte verification, `registerWasm` calldata, wallet signing, Base Sepolia
submission, and claiming an active validator status are **out of scope until
separately authorized**. A successful transaction alone would not prove
acceptance; validator activation/rejection status must be checked afterward.
