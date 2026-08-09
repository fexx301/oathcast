# Telegraph Explorer evidence template

Use one copy of this template for each future **paid request that actually
flows through Telegraph**. This is a capture form, not proof by itself. Leave
unknown fields blank and do not infer an Explorer API response.

## Evidence status

- Evidence status: `unverified | manually_verified | rejected`
- Official demand claim made: `no` until the Explorer record is checked
- Capture date/time (UTC):
- Captured by:
- Application release ID/source digest:
- Local evidence file:

## Application request

- Application request ID:
- OathCast event ID:
- Intent:
- Question/location:
- Forecast window (UTC):
- Forecast cutoff (UTC):
- Selected Miner ID/slug:
- Registry snapshot SHA-256:
- Telegraph route mode: `telegraph | auto | direct-through-telegraph`

## Payment and response

- Payment method: `x402 | other-supported-method`
- Asset/network: `Solana Devnet USDC | Base Sepolia USDC | other supported`
- Declared Miner price (USDC):
- Paid amount (USDC):
- Payment attempt ID:
- Challenge SHA-256:
- Settlement artifact SHA-256:
- Settlement verification state: `unverified | verified | invalid`
- HTTP response status:
- Response SHA-256:
- Signal/receipt hash, if returned:

## Explorer record

- Explorer URL:
- Explorer request/signal ID:
- Explorer Miner ID:
- Explorer application/consumer identity:
- Explorer payment/settlement identifier:
- Explorer status:
- Explorer checked at (UTC):
- Screenshot or exported record path:

## Reconciliation decision

- Served request visible in Explorer: `yes | no | unknown`
- Payment attached to request: `yes | no | unknown`
- Request matched local Application correlation: `yes | no | unknown`
- Count as local candidate: `yes | no`
- Count as official Telegraph demand: `yes | no | pending`

Reason for the decision:

```text

```

## Integrity and limitations

- Local receipt hash:
- Evidence artifact hash:
- Any mismatch or retry:
- Notes:

Do not count fixtures, direct upstream calls, unpaid preflights, failed
payments, or responses whose settlement is not independently verified. The
Explorer is the current manual checking path; replace this process with an
official Telegraph API only after those API semantics are released.
