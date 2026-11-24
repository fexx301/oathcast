# Gold Rush: investigate Queensland's historical gold mines through Telegraph (tutorial)

Gold Rush is the application side of the team's **Gold Rush challenge** in Telegraph Hackathon Season II. Pick a
historical gold working on a map of Queensland, and the app asks every relevant specialist Miner on Telegraph about
it. The report shows each answer with its source, cost and receipt, and which questions nobody could answer yet.

- Live: <https://oathcastcourt.duckdns.org/gold-rush> (Ethereum Sepolia testnet)
- Code: `payment-canary/src/gold-rush.ts` (server) and `gold-rush-page.ts` (page), reusing the escrow client
  `escrow.ts`
- Disclosure:
  - This is a hackathon entry, which has prizes.
  - The app is run by the OathCast team, which also runs specialist Miner 64176, so calls to 64176 are
    related-party.
  - The app pays from its own testnet escrow.

## Words used here, in plain English

- **Telegraph:** a network that sells answers. An app pays a little per question, and a **miner** (a service that
  answers one kind of question, called an **Intent**) replies.
- **Specialist:** a miner that covers this subject (gold, mining, geology or Queensland).
- **Ranked route:** letting Telegraph pick the miner for you. A **direct ask** names the miner yourself.
- **Escrow:** a prepaid balance the app keeps with Telegraph; each answer is deducted from it, with no blockchain
  transaction per question.
- **Receipt:** the proof Telegraph returns with each paid answer (a hash and the epoch it was recorded in).
- **Test USDC / Sepolia:** play money on Ethereum's test network. Nothing here costs real money.

## 1. Use it (no wallet needed)

1. Open the page. The map shows the **8,132** historical workings with gold in the Geological Survey of Queensland's
   MINOCC record (CC BY 4.0). A larger circle is a larger gold class: very small (<0.5 t), small (0.5–5 t), medium
   (5–50 t), large (50–150 t) or giant (>150 t, Mount Morgan).
2. Click a site. The panel shows its survey site number, map sheet, gold class and coordinates, and what an
   investigation will cost (one $0.01 test-USDC call per step, up to $0.09 for a new site). Answers someone already
   paid for are reused at no charge.
3. Click **Investigate**. The app asks each matching specialist (up to four) the same question (who owned it, how much gold it produced,
   and when and why it closed). A specialist that answers more (today 7310050) is then asked its extra questions:
   the gold price in the site's last recorded year (taken from the answers it just got), the gold price then vs today,
   land and rights (tenure, native title, land status) and **what might remain** (exploration, modern resources,
   access, environment). Then **who controls it today**: the company holding tenure over the site goes to
   Counterproof's identity Miner 731005 (`B2B_IDENTITY_ENRICHMENT_GENERAL`) and its registered name to the sanctions
   Miner 731002 (`SANCTIONS_SCREENING_MATCH_GENERAL`). A private individual on the register is never looked up.
   Telegraph's ranked route is asked the site question once, for comparison. Nine paid steps in all.
   Separately, the app reads **Takar's** signed files for the mine (another Season II team): Heritage Register events
   and people, and Takar's independent checks. They're free, not bought through Telegraph, and labelled as Takar's.
4. Read the report:
   - **the four questions** at the top: why it closed, what has changed since, what the evidence establishes today,
     and what deserves further investigation, answered by fixed rules from the rows below ("not established" when
     nothing qualifies; nothing assumed about remaining gold or economic viability);
   - each answer, its miner and sources, and its receipt (hash and epoch);
   - **Known / Missing / Check:** which questions the specialists answered, which nobody did, and any disagreements
     (between a miner's own sources, or between miners on the same period);
   - a **facts table:** every structured field a specialist returned (production, closure, ownership, gold price,
     "looked, not found"), each with its miner and source. Facts the miner labels as another working (for Brilliant
     Lode: Brilliant Deeps, Brilliant Extended) are listed apart and never counted for this site;
   - **land and rights today:** tenure, native title and land status rows, each with its register, record ID and check
     date. An expiry date or a gap in tenure doesn't mean the ground is available; that needs legal confirmation;
   - **who controls it today:** the registered company (legal name, LEI with a GLEIF link, jurisdiction, status) and
     its sanctions screen (each list's publication date and the screen's scope);
   - **what might remain today:** boreholes and company reports near the site, modern JORC resources as published
     (never "gold left"), the nearest town, road and power line, and environmental records (not a statement of
     contamination);
   - **events and people** (Takar, Queensland Heritage Register): quoted sentences with record IDs and Takar's status,
     and **Takar's independent checks**;
   - any other structured rows a miner returns appear in the facts table with their source, with no change to the app;
   - each answer, its miner and sources, its receipt, and whether the node has published its delivery record yet.

A site that was investigated before reuses the earlier paid answers at no charge. **Download evidence report**
saves the whole investigation as Markdown: the four questions, what's established, disputed, not established and
worth investigating, land and control, what might remain, events, Takar's checks, and every answer's receipt, epoch
and signal record. **Download report (JSON)** saves the raw answers.

**The same data from the command line:**

```sh
B=https://oathcastcourt.duckdns.org/gold-rush/api
curl -s $B/specialists   # the site-question Miners (today: 64176 and 7310050); 731005 and 731002 are asked by ID
curl -s "$B/events?site_no=494270" | head -c 300   # Takar's events and checks for Mount Morgan (free)
curl -s $B/sites | head -c 300
curl -s $B/ledger        # every paid call: question, miner, outcome, receipt
```

## 2. How it uses Telegraph

- **Finding specialists:** `GET /api/miners?intent=RESEARCH_QUERY_GENERAL`. A specialist is an active Miner on that
  intent whose name or description mentions gold, mining, geology, minerals or Queensland. Crypto-mining wording is
  excluded. The app asks at most four.
- **Asking:**
  - **Direct ask** to each specialist: `POST /engine/v1/ask/{minerId}` with `{"intent": "RESEARCH_QUERY_GENERAL",
    "params": {"question": …}}`. The team recommended direct asks for this app, because ranked routing doesn't match
    specialists to subjects inside a broad intent.
  - **One ranked-route ask** on the same question (`POST /engine/v1/ask`), for comparison.
- **Paying:** the **escrow rail**. One EIP-191 signature per call over the node's payment template, with no per-call
  transaction. The client checks that the payee is the current Diamond (`0x72a7…a08b`) and that the price is at most
  $0.01.
- **Reading the miner's own answer:** the node reshapes answers into the intent's fields (`answer`, `sources`,
  `source_count`, `brief`) and may reword them. The miner's raw response, with any extra fields, is kept under the
  answer's signal hash, and the app reads it from there:
  ```sh
  curl -s https://devnode.telegraphprotocol.com/engine/v1/signal/<signal_hash> | jq .payload.response
  ```

## 3. Add your Miner (it gets picked up automatically)

1. Register a Miner on `RESEARCH_QUERY_GENERAL` whose description says what it covers, e.g. "Queensland gold
   mines: reported production…". See the
   [miner tutorial](https://github.com/fexx301/oathcast/blob/main/docs/telegraph-s2-miner-tutorial.md).
2. Put the key facts in `answer`, so anyone reading only the reshaped answer gets them.
3. Optionally, return these structured fields as top-level keys next to `answer` and `sources`. The app shows them
   as rows, and they count toward "who answered what". These were agreed with another Gold Rush builder.
   ```json
   {
     "site_no": 494270,
     "production": [{"commodity": "gold", "amount": 7.6, "unit": "Moz", "period_from": 1886, "period_to": 1990,
                     "basis": "reported", "source_ref": "…", "source_url": "…"}],
     "closure": {"year": 1990, "reason_text": "…", "reason_class": "economic", "source_ref": "…", "page": 12},
     "gold_price": {"month": "1927-06", "usd_per_oz": 20.67, "aud_per_oz": 4.25, "series": "…", "source_url": "…"},
     "not_found": ["closure"]
   }
   ```
   `reason_class` is one of `exhausted | flooding | economic | legal | unknown`. Leave out what you couldn't source
   and list it in `not_found`: never send an estimate as a fact. The values above only illustrate the format.

## 4. Run it yourself

You need Node 22 or later, a Sepolia wallet for the app (its key stays on your server), and some test USDC in its Telegraph
escrow.

```sh
git clone https://github.com/fexx301/oathcast && cd oathcast/payment-canary
npm ci
npx vitest run tests/gold-rush.test.ts        # offline; no network, no money
```

**Fund the app's escrow** once, from any wallet with Sepolia ETH. Test USDC can be minted by anyone:

```sh
R=https://ethereum-sepolia-rpc.publicnode.com
USDC=0xcc194e1a843B658526D0D5C431f670028582Ae17
DIAMOND=0x72a70e949362670Bf8bceAd24eFC5b8f950aa08b
APP=<your app wallet address>
cast send $USDC "mint(address,uint256)" <your funding wallet> 2000000 --rpc-url $R --interactive
cast send $USDC "approve(address,uint256)" $DIAMOND 2000000 --rpc-url $R --interactive
cast send $DIAMOND "depositUSDCFor(address,uint256)" $APP 2000000 --rpc-url $R --interactive
curl -s https://devnode.telegraphprotocol.com/v1/ledger/account/$APP   # "deposited" shows it after the next ledger block
```

**Start it.** The key comes from the environment and is removed from it at startup:

```sh
OATHCAST_S2_ESCROW_PRIVATE_KEY=0x… OATHCAST_S2_ESCROW_ADDRESS=$APP GOLD_RUSH_LEDGER=./gold-rush-ledger.jsonl npm run gold-rush
# open http://127.0.0.1:8792/gold-rush
```

**Spend caps** (environment, with defaults):
- `GOLD_RUSH_MAX_CALLS` 180 in total;
- `GOLD_RUSH_MAX_PER_DAY` 60;
- `GOLD_RUSH_MAX_PER_IP_HOUR` 20.

A full report on a new site is up to 9 paid calls: two site answers; the gold price, the price then vs today, land
and rights (one question for all three layers) and what might remain (one question for all four); the controller's
identity and its sanctions screen (companies only); one ranked-route answer.

Paid calls run one at a time. An attempt row is written before each paid request leaves, so a crash counts as
possibly charged and is never resent. Our deployment runs it as its own container behind Caddy at `/gold-rush`.

## Costs

- **Per answer:** $0.01 test USDC from the app's escrow; no gas per call.
- **Funding:** one deposit transaction (a fraction of a cent of Sepolia ETH).
- **Hosting:** a second small process on our existing server (about 100 MB of memory).
- **Registering a specialist Miner:** a bond in test USDC for its first intent on the current Diamond (read the amount
  with `minerBondParams()`; our 64176 posted 1.2), returned when you deregister.

## Limits

- **Coverage:** four Miners from three teams: 64176 (the survey record, ours), 7310050 (history, prices, land and
  rights, what might remain; NFT KING), 731005 and 731002 (identity and sanctions; Counterproof). The app finds a
  specialist's extra questions from its catalogue text; 7310050's doesn't list them yet, so the app recognises them
  from a short built-in list (`KNOWN_TOPICS`). The controller Miners and the Takar file reader are written in.
- **Not covered by anyone yet:** historical assays, detailed geological reports, tailings volumes, the economics of
  reopening. Gold prices before March 1900 aren't covered, and before 1960 they're the official US price,
  not a market average; the report says which.
- **Known and Missing** come from structured fields where a miner returns them, otherwise from a keyword check of
  sentences that name the mine. Read the answers.
- **The ranked-route comparison** often goes to a general research Miner. On Mount Morgan it returned academic
  abstracts, which is the reason for the direct asks.
- **Map positions** come from the survey record; its location accuracy is shown per site in 64176's answer.
- **Testnet only.** Mainnet would need a funded mainnet escrow and the mainnet Diamond, read from the 402 response's
  `payTo`.
