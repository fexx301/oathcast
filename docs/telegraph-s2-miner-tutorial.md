# Serve a Telegraph Intent: from the published contract to a live, routed miner

This is how I took one Telegraph Intent, `WEATHER_FORECAST_VERIFY_GENERAL`,
from its published contract to a registered miner that the network routes paid
requests to. It covers the steps, the costs, the things that went wrong, and a
way to test the result without a wallet.

Disclosure: I built this for the Telegraph Hackathon Season II, which has
prizes. Everything here was run on the Ethereum Sepolia testnet in November 2026.

> **Updated 20 Nov 2026: Telegraph moved to a new Diamond on 17 Nov.**
> - **Address:** `0x72a70e949362670Bf8bceAd24eFC5b8f950aa08b`, which replaces `0x3624…b99b`. No node reads the old
>   one, so a registration sent there is never seen.
> - **Bonds:** paid in test USDC, not MACHINA.
> - **Balances:** escrow balances didn't carry over; deposit again.
> - The commands below use the new address. The costs I paid on the old Diamond are kept as history and marked.
> - Source: [Addresses & Parameters](https://anjoman.ai/docs/protocol/addresses-and-params).

## What Telegraph does here

Telegraph is a network that sells answers. An app sends a request that names an
**Intent** (a published question type with fixed inputs and outputs) and pays
$0.01. The network's router picks a **miner** (a service that answers that
Intent), calls it, reads the answer into the Intent's fixed format and returns
it with a receipt.

So a miner's job is narrow: answer one Intent's request in a shape the node can
read. You do not find customers or handle payments. The Intent's contract is
the spec.

## What you need

- A public HTTPS endpoint that answers the Intent's question.
- [Foundry](https://getfoundry.sh) for `cast`.
- A throwaway wallet with a little Sepolia ETH for gas.
- About $1 of test USDC for the bond (anyone can mint test USDC). It is returned after you deregister.
- A place to host one YAML file at a URL that never changes. I used a GitHub
  raw link pinned to a commit.

## Step 1: Read the contract

Every Intent publishes its request and response format:

```bash
curl -s https://devnode.telegraphprotocol.com/engine/v1/intents/WEATHER_FORECAST_VERIFY_GENERAL \
  | jq '{request: .request.params, response: .response.fields, miner_count}'
```

For this Intent the request is two params:

| Param | Type | Meaning |
|---|---|---|
| `location` | string | City or place name |
| `date` | date | Past date to verify |

The response is six fields. `temp_max_c` is marked `scored: true`, so it is the
one the network scores. `temp_min_c` and `precipitation_mm` are numbers, and
`location`, `date` and `condition` are text.

Read this before you write any code. This Intent's contract changed during the
season: it first took a forecast claim to check, then became "place and past
day in, recorded weather out". My first YAML refused the new request as
unreadable, and I only found out by reading the contract again.

## Step 2: Answer exactly that shape

My endpoint is `GET /v1/forecast/verify?location=…&date=…`. For a place and a
past day it returns the six fields by name, plus one sentence of text. This is
a real answer as Telegraph returned it to a paying app:

```json
{
  "location": "Lleida, Spain",
  "date": "2026-11-05",
  "temp_max_c": 16.2,
  "temp_min_c": 6.5,
  "precipitation_mm": 5.1,
  "condition": "moderate rain"
}
```

Two rules made the miner easier to score:

- **Name your fields after the contract.** Then the mapping in Step 3 is one
  line per field.
- **Refuse with an error code, never with a guess.** A day that has not
  finished gets HTTP 422 with `code: window_not_finished` and a `Retry-After`
  header. A place that cannot be found gets 422 with `place_not_found`. A
  request that fails is not billed, and a refusal cannot be scored as a wrong
  answer.

## Step 3: Write the YAML

The YAML tells the node how to call you. Mine is
[oathcast-forecast-verify-s2.yaml](https://github.com/fexx301/oathcast/blob/c051cdb6f25405297904cd080fb3a46c20ba9b00/miners/candidates/oathcast-forecast-verify-s2.yaml).
The part that matters most is `intent_mapping`, which says where each contract
field goes in your request and where it sits in your answer:

```yaml
intent_mapping:
  WEATHER_FORECAST_VERIFY_GENERAL:
    endpoint: /verify
    input:
      location:
        from: location
        how: The requested place, verbatim.
      date:
        from: date
        how: The requested past day as YYYY-MM-DD, verbatim.
    output:
      temp_max_c:
        path: temp_max_c
        unit: degrees Celsius
      temp_min_c:
        path: temp_min_c
        unit: degrees Celsius
      precipitation_mm:
        path: precipitation_mm
        unit: mm
      condition:
        path: condition
```

With a mapping, the node finds each number by its path and converts units in
code. Without one, a model reads your whole answer and guesses.

Other things the node checks, from the
[YAML reference](https://anjoman.ai/docs/miners/yaml-config):

- Every endpoint needs a `description` and an `intents:` list.
- Every Intent on an endpoint must also be in `semantics.supported_intents`.
- Give each endpoint its `params`. An endpoint without them is legal, but the
  node then has to guess your field names.
- Leave out the old `on_chain.request` block. Jobs no longer read it.

## Step 4: Check before you pay

These checks are free. Each one catches a mistake that would otherwise cost a
transaction.

```bash
DIAMOND=0x72a70e949362670Bf8bceAd24eFC5b8f950aa08b   # the 17 Nov 2026 Diamond
RPC=https://ethereum-sepolia-rpc.publicnode.com

# The Intent name must be the full one. A family name reverts the registration.
cast call $DIAMOND "isCanonicalIntent(string)(bool)" WEATHER_FORECAST_VERIFY_GENERAL --rpc-url $RPC   # true
cast call $DIAMOND "isCanonicalIntent(string)(bool)" WEATHER_FORECAST_VERIFY --rpc-url $RPC           # false

# Your id and slug must be unused.
curl -s https://devnode.telegraphprotocol.com/api/miners \
  | jq '.[] | select(.id=="64174" or .slug=="oathcast-forecast-verify") | .slug'
```

Then paste the YAML into the registration page at
[anjoman.ai/dashboard/earn](https://anjoman.ai/dashboard/earn). It validates the
YAML and calls your real endpoint before anything is signed.

One wrinkle: the page's test call sent my endpoint no place and no date, so my
miner answered 400. That was my refusal rule working, and the page still
accepted the API key.

## Step 5: Register

The registration page does the whole flow from your wallet: it validates, pins
the YAML, sends `registerMiner` and stores your API key. I used it. The same
thing by hand with `cast` is in
[Registering as a Miner](https://anjoman.ai/docs/miners/miner-registration).

What it costs on the current Diamond (read on 2026-11-20):

| Item | Cost |
|---|---|
| Bond for the first Intent | 1 test USDC (`minerBondParams()`, first value, 6 decimals) |
| Gas | a fraction of a cent of Sepolia ETH |
| Price floor I set | $0.01 per request, the minimum |

Read the bond from the Diamond rather than trusting a page, then mint the test
USDC you need (the token lets anyone mint):

```bash
cast call $DIAMOND "minerBondParams()(uint256,uint256,uint256,uint256,uint256)" --rpc-url $RPC   # 1000000 = $1 for the first Intent
USDC=0xcc194e1a843B658526D0D5C431f670028582Ae17
cast send $USDC "mint(address,uint256)" <your address> 2000000 --rpc-url $RPC --interactive
```

History: on the old Diamond (2026-11-07) the bond was paid in MACHINA. There was no
faucet, so I swapped test USDC on the Sepolia Uniswap pool; 14 test USDC bought
about 2.5 MACHINA, and the $1 bond took about 0.167. None of that is needed now.

## Step 6: Install the API key

If your endpoint needs a key, the node must hold it. The key is tied to the
wallet that registered the miner, so it can only be stored after registration.
The registration page stores it for you. To do it by hand, request a challenge,
sign it with that wallet and post the key
([steps](https://anjoman.ai/docs/miners/miner-registration)).

This is the step that cost me the most. My older miner, on a different weather
Intent, sat at **score 0, rank 19 of 25** after the network moved chains. I
reinstalled its key on 2026-11-07, and the same day it scored **0.96, rank 9**.
Nothing else about that miner changed.

So if a miner is registered, active and scoring nothing, check the key first.

## Step 7: Verify it is live

The node reads the chain about 10 to 15 minutes behind, so wait before you
check. Then look the miner up by the registration id from your receipt:

```bash
curl -s https://devnode.telegraphprotocol.com/api/miners/5130 \
  | jq '.miner | {activation_status, rejection_reason}'
# { "activation_status": "active", "rejection_reason": null }
```

Look it up by registration id, not by slug. If you were rejected for a slug
clash, the slug lookup shows someone else's miner.

## Step 8: Test it with a real paid request

A miner is only proven when a paying app gets its answer through the router.
I built a small app for that: **Field-day review**. A grower names a field job,
a place and a past day. The app buys that day's recorded weather through
Telegraph and says whether the weather window held.

You can try it without a wallet:

- App: <https://oathcastcourt.duckdns.org/field-day>
- Ledger of every call it paid for: <https://oathcastcourt.duckdns.org/field-day/api/ledger>

The app never calls my miner directly. It sends `POST /engine/v1/ask` with the
Intent and params, and the router picks the miner. Each ledger row names the
miner that answered and carries a receipt. About an hour after a call, the
receipt can be checked against the network's block for that epoch:

```bash
# Use the epoch_id and receipt_hash from the receipt your paid call returned.
curl -s https://devnode.telegraphprotocol.com/v1/blocks/<epoch_id>/deliveries/<receipt_hash> | jq .delivery
# fields: the payer (your wallet), the miner_id that served, class paid_consumer, usdc 10000
```

The network was reset around 16–17 Nov, and delivery records from before then
now return 404, including the ones this app collected on 11–12 Nov. Keep your own
copy of each record if you need it later.

As of 2026-11-12 00:49 UTC the app had made 10 paid calls over two days, and the
router sent all 10 to this miner.

To pay for requests from your own code, the escrow method is the simplest: one
deposit, then one signature per call and no per-call transaction. A working
client in about 160 lines is
[escrow.ts](https://github.com/fexx301/oathcast/blob/2495caa/payment-canary/src/escrow.ts)
(pinned to the new Diamond; it refuses to pay any other address).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Registered and active, but score 0 | No API key stored for the miner | Install the key (Step 6) |
| Not in `/api/miners` | The node has not seen the event yet, or it rejected the YAML | Wait 15 minutes, then read `/api/miners/{registrationId}` |
| `activation_status: rejected` | The YAML failed a check | Read `rejection_reason`, fix the YAML, then call `updateMiner`. Do not register again |
| `unsupported intent` or `a prefix is not an intent` revert | Wrong or shortened Intent name | Check with `isCanonicalIntent` (Step 4) |
| Hash mismatch | The hosted bytes differ from the bytes you hashed, often by a trailing newline | Hash the output of `curl -s <url>` |
| The node rejects a Python client with 403 | It refuses Python's default User-Agent | Send your own User-Agent header |
| Paid call refused: `signature is from …` | The wallet address was not lowercased before signing | Lowercase it. Sign the node's message template exactly as given |

## Costs and limits

- **Bond:** 1 test USDC for the first Intent on the current Diamond, returned after you deregister
  (`withdrawMinerBond()` after a 2-day delay).
- **Data:** my source, Open-Meteo's historical weather API, is free and needs
  no key. Its numbers are model estimates for a grid cell, not station
  readings. Its reanalysis data arrives about five days late, so recent days
  can be revised.
- **Accuracy:** against NOAA station records on 100 city-days chosen before the
  run, the daily high was within ±2 °C on 81.5% of the 92 covered days. The
  average error was 1.35 °C. The weakest region was Asia-Pacific at 65%.
  [Method and numbers](https://github.com/fexx301/oathcast-telegraph-s2/tree/main/wfv-miner-quality-v1).
- **Ranking:** this Intent has no dedicated scorer registered yet. The node
  gives it a generic fallback score, and Telegraph's explorer shows the Intent
  as unranked. So I can show routed, paid requests, but not an official rank.
- **Routing share:** all 10 of my app's calls reached this miner. I do not know
  why the share was that high, and it may not stay that way.

## Try it, and tell me what breaks

If you serve an Intent with these steps, or the Field-day app gives you a wrong
answer for a place and day you know, open an issue on
[fexx301/oathcast](https://github.com/fexx301/oathcast/issues). A wrong answer
with the place and date is the most useful report.
