# Hackathon submission checklist

This is an evidence checklist, not a claim that the corresponding gates are
complete. Mark an item complete only when its artifact is attached and its
source is independently verifiable.

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
- [x] At least three active `WEATHER_FORECAST` Miners observed in the live catalog on 2026-08-13. Recheck at submission; this does not satisfy the separate 100-request condition.
- [ ] Official Miner performance evidence from live evaluation.
- [ ] X update evidence tagged `@Telegraphprotoc`.

## Track 2 — Script Author

- [x] Three-input development proxy: question, ground truth, raw response.
- [x] Adversarial corpus and deterministic local robustness report.
- [x] Explicit separation between semantic proxy and Brier domain benchmark.
- [x] Official scoring-module ABI, Rust example, and wazero tester published.
- [x] OathCast `no_std` scoring module compiled with pinned Rust and tested with
  native Rust tests, a wazero ABI/adversarial harness, 5,000 deterministic
  calls, and Telegraph's unmodified official tester (`0.8500` published example score).
- [x] Final reproducible artifact evidence frozen: two byte-identical clean
  builds; 16,292 bytes; SHA-256
  `97d481b724bd79fa78d32218f20be9c1b85468109a8ff2a0da2d2574c775f3af`;
  raw-byte Keccak-256
  `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`.
  Machine-readable record:
  `scoring-modules/oathcast-weather/release-evidence.json`.
- [ ] Portal discrepancy resolved: current docs/example specify exactly
  `alloc`, `dealloc`, and `rank_answer`, while helper text additionally names
  undocumented `breakdown_answer`.
- [ ] `registerWasm` Intent-array semantics confirmed; current portal UI
  requires Intent selection but submits an empty array.
- [ ] Exact WASM uploaded and hosted bytes verified. Requires separate
  authorization; do not infer this from local build completion.
- [ ] Scoring module registered and active.
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
