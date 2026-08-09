# OathCast Solana-devnet x402 payment canary

This is a one-shot, explicitly gated canary for the Telegraph Miner x402
flow. It is self-contained under `payment-canary/` and uses the official v2.11
fetch/SVM client pattern with `@solana/kit` and `@scure/base`.

The policy is fixed to the current devnet challenge:

- network: `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1`
- asset: `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`
- default maximum: `10000` base units (0.01 USDC)
- recipient: `G53EbeTZSNsAn7bj6iMFUQnq3zpDdEbHhKkPRywo8bix`
- fee payer: `2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4`

## Install and check

```bash
cd /Users/femi/Documents/My-Projects/oathcast/payment-canary
npm ci
npm run build
npm test
```

`package-lock.json` pins the dependency graph. Tests mock both HTTP and RPC;
they never send a payment or read a key from disk.

## Preflight only

Preflight is the default. It makes one unpaid request, decodes the
`PAYMENT-REQUIRED` header, and validates x402 v2, the exact Solana network and
USDC mint, the amount cap, recipient, fee payer, and exact target Miner/path.
It does not read `SOLANA_PRIVATE_KEY` or initialize a signer.

```bash
export TELEGRAPH_DISPATCHER_URL='https://your-telegraph-dispatcher.example/miner-dispatcher'
npm run canary -- \
  --dispatcher-url "$TELEGRAPH_DISPATCHER_URL" \
  --miner-id 18 \
  --path predict \
  --operation-id preflight-2026-08-08-001 \
  --param lat=6.5244 \
  --param lon=3.3792
```

Use `--target-url https://host/v1/18/predict` instead of
`--dispatcher-url ...` for a direct registered route. The challenge resource
URL must exactly equal the resulting URL, including sorted query parameters.

Telegraph's current live hackathon dispatcher is temporarily exposed over HTTP
on Solana devnet and advertises its canonical resource without the gateway's
`/miner-dispatcher` prefix. That narrowly pinned compatibility mode must be
enabled explicitly:

```bash
npm run canary -- \
  --dispatcher-url http://13.237.89.59:7044/miner-dispatcher \
  --allow-insecure-http-devnet \
  --miner-id 18 \
  --path predict \
  --operation-id preflight-live-001 \
  --param lat=6.5244 \
  --param lon=3.3792 \
  --param hourly=2t \
  --param forecast_hours=24
```

The flag permits no other HTTP authority. Redirects are disabled, and the
prefix-free challenge URL must retain the same origin, Miner route, endpoint,
and complete query string. Remove this exception when Telegraph publishes an
HTTPS dispatcher.

## One-shot execute

Execution requires both `--execute` and a 64-byte base58 Solana secret key in
the process environment. The key is read only from `SOLANA_PRIVATE_KEY`; this
directory contains no `.env` or secret-file loader.

Create a dedicated devnet-only wallet once with:

```bash
npm run wallet:create
```

The command writes an owner-only `.secrets/solana-canary.env`, prints only the
public address, and refuses to overwrite an existing wallet. `.secrets/` is
gitignored. Never reuse this wallet or key on mainnet.

```bash
export SOLANA_PRIVATE_KEY='BASE58_64_BYTE_SECRET_KEY'
npm run canary -- \
  --dispatcher-url "$TELEGRAPH_DISPATCHER_URL" \
  --miner-id 18 \
  --path predict \
  --operation-id execute-2026-08-08-001 \
  --max-amount 10000 \
  --rpc-url https://api.devnet.solana.com \
  --param lat=6.5244 \
  --param lon=3.3792 \
  --execute
```

The process performs one unpaid preflight and, only after all policy checks
pass and the signer is initialized, one paid retry. It sends the operation ID
as `Idempotency-Key` and `X-Payment-Canary-Operation-ID`. Use a fresh operation
ID for a new attempt; do not automate retries around this command.

After the retry, the canary records only a hash of the settlement header and
the public transaction signature. It queries Solana devnet with
`getSignatureStatuses` and `getTransaction` (`confirmed`, `jsonParsed`) and
requires a confirmed, error-free transaction. When token-balance metadata is
available, it independently checks the exact USDC decrease for the signer and
increase for the approved recipient. Missing metadata or a mismatch produces
sanitized evidence and a non-zero exit status; the canary never treats a
settlement header alone as proof.

## Evidence and safety

stdout is one sanitized JSON evidence document. It contains hashes, public
addresses, status fields, and the settlement transaction signature. It never
contains `SOLANA_PRIVATE_KEY`, `PAYMENT-SIGNATURE`, a payment payload, or a
raw settlement proof. A failed paid retry is treated as an unknown outcome and
is never retried by the CLI.
