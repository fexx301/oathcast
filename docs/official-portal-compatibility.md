# Official Miner Portal Compatibility Check

Observed 2026-08-09 against the published Telegraph Miner Registry source at
[`telegraphprotocol/tg-miner-integration`](https://github.com/telegraphprotocol/tg-miner-integration),
local snapshot commit `3985e67`.

This is a compatibility audit, not an official registration or validation
result. No YAML was uploaded, no Pinata/API key was used, no wallet was
connected, and no on-chain transaction was signed.

## Result

The current draft at [`miners/oathcast-weather.yaml`](../miners/oathcast-weather.yaml)
matches the required shape used by the published portal wizard:

| Portal area | Draft status | Notes |
| --- | --- | --- |
| Basics | Ready except identity | `kind`, `slug`, and `name` are present; `id` is intentionally a placeholder until a unique Integration ID can be claimed. |
| Connection | Ready | Public HTTPS `base_url` is present. |
| Endpoints | Ready | At least one endpoint path is present. |
| Semantics | Ready | `WEATHER_FORECAST` and `WEATHER_CHECK` are declared. |
| On-chain section | Shape-ready | The draft includes an on-chain field and request mapping. |
| Live portal validation | Pending | Requires the portal's current runtime, a non-placeholder identity, and its final upstream checks. |
| Pinning/registration | Pending | Requires a public pinned YAML URL, raw-byte SHA-256 hash, fee address, minimum price, supported intents, and a deliberate wallet signature. |

The local validator reports the draft as structurally valid. Its warning about
the placeholder Integration ID is intentional and must not be “fixed” with an
arbitrary number. The portal's published registration flow requires a unique
identity and validates before the on-chain submission.

## Registration gates when the window opens

Use the integration portal's current interface as the authority. Before any
wallet action:

1. Replace the placeholder with an ID that the portal accepts as unique.
2. Re-run local YAML validation and the portal's parse/validation step.
3. Pin the exact final bytes to the permitted public URL/IPFS flow.
4. Compute the SHA-256 hash from those exact bytes and confirm the displayed
   hash matches.
5. Confirm the fee address, canonical Weather intents, and minimum price of
   `0.01 USDC`.
6. Review the contract payload, then sign only the intended Base Sepolia
   registration transaction.

The published integration source identifies Base Sepolia (`84532`) and the
Miner Registry contract as `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`; these
details remain subject to the final Hackathon 1 interface presented by the
portal.

## Explicit non-actions

- Do not upload the draft while its identity is a placeholder.
- Do not invent the official WASM evaluator or register a Script Author before
  the official harness and ABI are released.
- Do not treat local fixtures, direct upstream weather calls, or the capped
  Solana devnet canary as qualifying Track 3 demand.

Authoritative surfaces:

- [Miner Registry portal](https://integrate.telegraphprotocol.com/)
- [Miner Registry source and YAML flow](https://github.com/telegraphprotocol/tg-miner-integration)
- [Hackathon rules](https://hackathon.telegraphprotocol.com/rules)
