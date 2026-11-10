# Field-day review (Telegraph S2 consumer app, Mission 06)

A grower names a field job they planned on a forecast (spraying, harvest,
frost-sensitive work), a place and a past day. The app buys that day's recorded
weather through Telegraph and says whether the weather window held.

- Public page: `https://oathcastcourt.duckdns.org/field-day`
- Public ledger: `https://oathcastcourt.duckdns.org/field-day/api/ledger`
- Code: `payment-canary/src/field-day.ts`, `escrow.ts`, `field-day-page.ts`

## How it consumes Telegraph

- **Ranked route only:** `POST /engine/v1/ask` with
  `{"intent":"WEATHER_FORECAST_VERIFY_GENERAL","params":{"location","date"}}`.
  The router picks the miner. The app never calls `/ask/{minerId}`.
- **Escrow rail on Ethereum Sepolia:** one EIP-191 signature per call over the
  node's template, and no per-call transaction. Payer (app wallet):
  `0x74041A982529b9757a121b48Bc2F98B129dE7842`.
- **Receipts:** each served row stores `miner_id`, `signal_hash`, the
  `X-PAYMENT-RESPONSE` receipt (`receipt_hash`, `epoch_id`, `amount_uusdc`)
  and the result. Once the epoch is recorded (up to about an hour on testnet),
  `GET /v1/blocks/{epoch_id}/deliveries/{receipt_hash}` on the node proves the
  delivery.

## Verdict rules

| Job | Needs | Fails when |
|---|---|---|
| Spraying, harvest | a dry day | 1 mm of precipitation or more (WMO rain-day threshold) |
| Frost-sensitive work | the low above 0 °C | `temp_min_c` ≤ 0 |

## Spend controls

- **Validation before signing.** Jobs come from a fixed list. The place must
  be 2–80 plain characters. The day must be at least two UTC days ago and
  within the last year. Bad input costs nothing.
- **Payment pins.** The network must be `eip155:11155111`. The payee must be
  the Diamond `0x3624…b99b`. The quote may not exceed 10000 µUSDC.
- **Signer pin.** The service refuses to start if the key doesn't match
  `OATHCAST_S2_ESCROW_ADDRESS`.
- **Caps.** 40 calls in total, 12 per UTC day and 4 per IP per hour. Paid
  calls run one at a time.
- **Ledger rows.** An attempt row is written before the paid request leaves,
  so a crash or timeout is counted as possibly charged and is never resent.
  The ledger holds no IP addresses.
- **Reuse.** A served answer for the same place and day is reused for free.

## Fund the escrow (owner wallet `0xe9d1…`, once)

The app wallet needs no ETH: the owner deposits on its behalf.

```bash
R=https://ethereum-sepolia-rpc.publicnode.com
USDC=0xcc194e1a843B658526D0D5C431f670028582Ae17
DIAMOND=0x3624eb012b5e998bb57b6be6c9cfc9c4bc5eb99b
APP=0x74041A982529b9757a121b48Bc2F98B129dE7842
cast send $USDC "approve(address,uint256)" $DIAMOND 2000000 --rpc-url $R --private-key $KEY
cast send $DIAMOND "depositUSDCFor(address,uint256)" $APP 2000000 --rpc-url $R --private-key $KEY
cast call $DIAMOND "escrowBalance(address)(uint256)" $APP --rpc-url $R   # expect 2000000
```

## Deploy (server, one SSH window)

The service runs from the payment-canary image as a separate container, so the
reviewed Solana sidecar is untouched.

```bash
docker build -f payment-canary/Dockerfile -t oathcast-field-day:2026-11-10 .
mkdir -p ~/oathcast-live-state/field-day && sudo chown 1000:1000 ~/oathcast-live-state/field-day
docker run -d --name oathcast-field-day --restart unless-stopped \
  -p 127.0.0.1:8791:8791 -e FIELD_DAY_HOST=0.0.0.0 \
  --env-file ~/oathcast-live-secrets/s2-escrow-wallet.env \
  -v ~/oathcast-live-state/field-day:/data --user 1000:1000 \
  oathcast-field-day:2026-11-10 npm run field-day
curl -s 127.0.0.1:8791/field-day/api/ledger
```

Then update Caddy. Never `mv` over the bind-mounted Caddyfile; see
DEPLOYMENT.md:

```bash
cat Caddyfile.new > Caddyfile
docker exec oathcast-caddy caddy validate --config /etc/caddy/Caddyfile
docker exec oathcast-caddy caddy reload --config /etc/caddy/Caddyfile
```

## Honest accounting

Every routed call is logged, whichever miner served it. For consumption credit
we claim only the calls whose `miner_id` (and receipt `miner`) is our miner
64174. This is our own app, so it is related-party use: at most 7 of the 10
consumption points.
