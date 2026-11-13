# Field-day review: consumption evidence (Telegraph S2, Mission 06)

Every paid call the Field-day review app made from launch on 2026-11-11 to
the end of its call list on 2026-11-12. This is Ethereum Sepolia testnet use
by our own app, which makes it related-party use; it is not customer demand.

- **App:** <https://oathcastcourt.duckdns.org/field-day>. Code:
  `payment-canary/src/field-day.ts`. How it works: `docs/field-day.md`.
- **Route:** `POST https://devnode.telegraphprotocol.com/engine/v1/ask` with
  `{"intent":"WEATHER_FORECAST_VERIFY_GENERAL","params":{"location","date"}}`.
  This is the ranked route: the router picks the miner. The app never calls
  `/ask/{minerId}`.
- **Payment:** the escrow method. App wallet (payer):
  `0x74041A982529b9757a121b48Bc2F98B129dE7842`. It was funded with 2.00 test
  USDC on 2026-11-11, and `escrowBalance` read 1,850,000 µUSDC on 2026-11-13,
  which is 15 calls × 10,000 µUSDC.
- **Files:**
  - `ledger-2026-11-13.json`: the app's public ledger, one row per paid call
    with the answer, `signal_hash` and the `X-PAYMENT-RESPONSE` receipt. It
    holds no IP addresses.
  - `proofs/{epoch}-{receipt_hash}.json`: the node's delivery record for each
    call, from `GET /v1/blocks/{epoch}/deliveries/{receipt_hash}`, with its
    Merkle proof.

## Summary

| | Count |
|---|---|
| Paid calls, all served | 15 |
| Served by our miner 64174 (registration 5130) | **13**: 9 on 2026-11-11 and 4 on 2026-11-12 |
| Served by other miners | 2 (registrations 4929 and 5176) |
| Delivery records with `payer` = the app wallet and `class` = `paid_consumer` | 15 of 15 |

## Every call

| When (UTC) | Place, day asked | Miner (answer) | Receipt | Epoch | Miner (delivery record) | Class |
|---|---|---|---|---|---|---|
| 2026-11-11 11:30 | Lleida, Spain, 2026-11-05 | 64174 | `0x70868e1139…` | 2065 | 5130 | paid_consumer |
| 2026-11-11 11:37 | Fresno, California, 2026-11-05 | 64174 | `0xb8fda96ef4…` | 2065 | 5130 | paid_consumer |
| 2026-11-11 11:44 | Yakima, Washington, 2026-11-03 | 64174 | `0x4324b1663e…` | 2065 | 5130 | paid_consumer |
| 2026-11-11 12:47 | Mendoza, Argentina, 2026-10-28 | 64174 | `0xe235a951a4…` | 2066 | 5130 | paid_consumer |
| 2026-11-11 14:47 | Ludhiana, India, 2026-10-30 | 64174 | `0x67acf018ff…` | 2066 | 5130 | paid_consumer |
| 2026-11-11 16:47 | Nakuru, Kenya, 2026-11-02 | 64174 | `0x9e75c0942d…` | 2067 | 5130 | paid_consumer |
| 2026-11-11 18:47 | Ballarat, Australia, 2026-10-25 | 64174 | `0xc68b7b5150…` | 2068 | 5130 | paid_consumer |
| 2026-11-11 20:47 | Bordeaux, France, 2026-11-06 | 64174 | `0xc6f7c130e7…` | 2068 | 5130 | paid_consumer |
| 2026-11-11 22:47 | Rosario, Argentina, 2026-11-01 | 64174 | `0x144938a351…` | 2069 | 5130 | paid_consumer |
| 2026-11-12 00:47 | Almeria, Spain, 2026-11-04 | 64174 | `0x57cb84168a…` | 2070 | 5130 | paid_consumer |
| 2026-11-12 02:47 | Christchurch, New Zealand, 2026-10-29 | 64174 | `0x4da71fed23…` | 2070 | 5130 | paid_consumer |
| 2026-11-12 04:47 | Kano, Nigeria, 2026-10-27 | 64174 | `0xfe0094c286…` | 2071 | 5130 | paid_consumer |
| 2026-11-12 06:48 | Salinas, California, 2026-11-07 | 78131 | `0xf196cd4706…` | 2072 | 4929 | paid_consumer |
| 2026-11-12 08:48 | Wagga Wagga, Australia, 2026-11-08 | 20260828 | `0x81549d0166…` | 2072 | 5176 | paid_consumer |
| 2026-11-12 10:47 | Yakima, Washington, 2026-10-26 | 64174 | `0x4fa97f1783…` | 2073 | 5130 | paid_consumer |

The "Miner (answer)" column is the miner id the node's answer reported. The
delivery record names the same miner by its registration id; 64174 is
registration 5130.

## Check one yourself

```bash
curl -s https://devnode.telegraphprotocol.com/v1/blocks/2065/deliveries/0x70868e113948939d6e54cbac0697cbe11cdd2a9de24f8523c27044de59cdf89e | jq .delivery
cast call 0x3624eb012b5e998bb57b6be6c9cfc9c4bc5eb99b "escrowBalance(address)(uint256)" 0x74041A982529b9757a121b48Bc2F98B129dE7842 --rpc-url https://ethereum-sepolia-rpc.publicnode.com
```
