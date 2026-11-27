# Field-day Settle: weather-verified settlement on Solana

A buyer hires a contractor for field work that depends on the weather: spraying or harvest (needs a dry day) or
frost-sensitive work (needs the low above 0 °C). Today the buyer either pays regardless or argues about the weather
afterwards. Field-day Settle holds the fee in escrow and lets the recorded weather decide, using two independent
Telegraph verifiers, each paid on Solana with x402.

Code: `payment-canary/src/field-day-solana.ts` (app, rules, audit), `field-day-solana-page.ts` (page),
`tests/field-day-solana.test.ts` (22 tests). Real runs: [`artifacts/field-day-solana/`](../artifacts/field-day-solana/).

## The flow

1. **Book.** The fee (0.01–0.05 devnet USDC) moves from the buyer to the escrow agent wallet. The memo records the job.
2. **Verify twice through Telegraph,** intent `WEATHER_FORECAST_VERIFY_GENERAL`, each call paid with a Solana devnet
   x402 payment ($0.01, `exact`, checked against Telegraph's published recipient and asset before signing):
   - the **ranked route** (`POST /engine/v1/ask`): Telegraph picks the verifier;
   - a **cross-check** (`POST /engine/v1/ask/{miner}`) from a different miner, tried in order until one answers:
     77031 (SecondSky, NASA POWER: satellite and MERRA-2), 731004 (Counterproof, ERA5), 64174 (ours, ERA5).
     Each answer must be for the place and day asked, in the intent's shape, with a signal hash.
3. **Settle by rule** (code, not a model). A rain day is 1 mm or more (WMO); frost means a low at or below 0 °C.

   | Ranked route | Cross-check | Decision | Money |
   |---|---|---|---|
   | held | held | RELEASE | escrow → contractor |
   | failed | failed | REFUND | escrow → buyer |
   | disagree, or either has no reading | | HOLD | stays in escrow |

   The settlement transaction's memo is compact JSON: the job, the decision, and for each verifier its miner id,
   the reading the rule used, and its Telegraph signal hash.
4. **Resolve a HOLD by hand** (operator command, not a web button):
   `npm run field-day-solana -- resolve <job id> contractor|buyer "<reason, 5–140 characters>"`. The memo carries the
   reason and the HOLD transaction; a job resolves once.

A job is idempotent: the same job (work, place, day, contractor, fee) has the same id. Asking again shows it; a job
that failed midway resumes without a second deposit or a second payment for a reading it already has. A deposit that
was sent but never confirmed is refused until someone checks the chain.

## Audit from the transaction alone

`GET /field-day-solana/api/audit?tx=<signature>` (or the page's Audit box) reads the transaction from devnet and
checks: it succeeded; the decision follows from the memo's own readings under the rule above; the USDC moved as the
decision says (a HOLD moves nothing; a resolution must point at a real HOLD for the same job); and each reading
matches its Telegraph signal record (`GET /engine/v1/signal/{hash}`), including the miner that answered. If the node
doesn't answer, that check says so rather than passing.

## Run it

```sh
cd payment-canary && npm ci
# SOLANA_PRIVATE_KEY: operator (pays Telegraph and fees; the demo buyer; needs devnet USDC and a little SOL)
# FIELD_DAY_AGENT_KEY: the escrow agent, a different wallet
node --env-file=operator.env --env-file=agent.env node_modules/.bin/tsx src/field-day-solana.ts
# http://127.0.0.1:8793/field-day-solana/
```

Limits: `FIELD_DAY_SOLANA_MAX_JOBS` (60), `_MAX_PER_DAY` (15), `_MAX_PER_IP_HOUR` (4). Ledger:
`FIELD_DAY_SOLANA_LEDGER` (append-only JSONL; an attempt row is written before any money moves).

## What it isn't

- **Devnet only.** Test USDC (`4zMMC9…DncDU`); no mainnet path.
- **A custodial escrow,** an agent wallet the app holds, not an on-chain program; the demo buyer is the operator wallet.
  On mainnet the buyer would sign the deposit and the escrow would be a program enforcing the same rule.
- **Two data sources, not two stations.** 64174 and 731004 both read the ERA5 archive (they agreed to the decimal on
  every test day); 77031 reads NASA POWER, which is why it is the first cross-check. Neither is a rain gauge at the
  field. On 18 Nov in Lleida they disagreed (3.7 mm vs 0.62 mm), and the fee was held.
- **Disclosure:** OathCast runs 64174, which the ranked route currently picks; the cross-check is always another
  operator's miner first.
