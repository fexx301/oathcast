# Official Miner Portal Compatibility Check

Originally observed 2026-08-09 against the published Telegraph Miner Registry
source; updated 2026-08-13 against the live Telegraph documentation and launch
email.

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
historical context, not an unresolved registration blocker.

## Remaining non-actions

- Do not treat routing ID `64173` as on-chain registration ID `78`.
- Use the now-published official WASM ABI and tester; do not claim the local
  Python proxy is a compiled module or register a scoring module without
  separate wallet authorization.
- Do not treat local fixtures, direct upstream weather calls, or the capped
  Solana devnet canary as qualifying Track 3 demand.
- Do not claim paid requests, leaderboard performance, or Track 3 demand from
  registration and activation alone.

Authoritative surfaces:

- [Miner Registry portal](https://integrate.telegraphprotocol.com/)
- [Miner Registry source and YAML flow](https://github.com/telegraphprotocol/tg-miner-integration)
- [Hackathon rules](https://hackathon.telegraphprotocol.com/rules)
