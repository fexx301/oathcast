# Track 3 application evidence

- Verification complete: no
- Official Telegraph demand claimed: no
- Local candidate observed: yes

## Request

- Miner: `212` / `forecast`
- HTTP status: `200`
- Response body SHA-256: `f662b3ef2ee8f9bb3c4f3d8a5ab805adbb6da5c146acd86a02c52009265bf5b6`
- Application request: `app-90c60f8ba329abdd99bee0df356555a0b75ca6a350524e2ac88cae6a3f33`
- Resolution status: `pending`

## Payment

- Network: `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1`
- Asset: `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`
- Amount: `10000` micro-USDC
- Settlement: `verified`
- Transaction: `4SSxNZf2z8URpccdPK66EqkF5FW9fUpdiMTkGcQuC3D9U7fVboHRtjpxC4vaux3FXUA4xsAL6KcfG4y57MzVM9VK`
- Verification artifact persisted in journal: `no`
- Recomputed verification artifact SHA-256: `32b5261318fd38f2432c5a91dfdea57b8e97a8cf9d45939e2c6208a6c5907efc`

## Discovery

- Observed: `2026-09-05T23:25:22.457183Z`
- Snapshot SHA-256: `259f0cb352db59365676567d6ae39b1d90c89cb2568f31a0aabe62a42dd7133a`
- Registry hash linked in protocol receipt: `259f0cb352db59365676567d6ae39b1d90c89cb2568f31a0aabe62a42dd7133`
- Signal receipt hash: `not present`

## Verification checks

- [x] payment_journal_integrity
- [x] payment_events_hashed
- [x] solana_settlement_verified
- [x] demand_ledger_integrity
- [x] demand_event_is_local_candidate
- [x] application_external_influence
- [x] application_receipt_bound_to_payment
- [x] fresh_discovery_contains_reviewed_miner
- [ ] verification_artifact_persisted
- [ ] registry_snapshot_linked_to_discovery
- [ ] signal_receipt_present
- [ ] application_resolved

## Open evidence gaps

- payment journal does not persist verification_artifact_sha256 for this settled attempt
- application protocol receipt registry_snapshot_sha256 is malformed
- application protocol receipt has no signal receipt hash
- application case has no independent observation and resolution
- application protocol receipt registry snapshot does not match the fresh discovery snapshot

This sanitized artifact excludes the raw paid response, payment authorization headers, private keys, and principal/idempotency secrets. It is local payment evidence, not an official Telegraph demand or adoption count.
