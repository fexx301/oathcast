# OathCast improvement register

Review date: 2026-08-07 (work log updated 2026-08-10)
Context: Full-project review (security, purpose, hackathon-winning potential) ahead of the
Hackathon 1 window (Track 1 opens 2026-08-17; Track 3 opens 2026-08-31).
Baseline at review time: 94/94 tests passing, no secrets in git history, staging live at
`https://oathcastcourt.duckdns.org` (v3.2, auth enforced, `/readyz` healthy).

**Status 2026-08-10 (evening): 198/198 tests passing, and the work in this file is now
DEPLOYED.** The renderer rework (P0) and the entire security batch (S1–S6b) are merged,
covered, and **live** — the host runs `2026-08-10-hardened-v5`
(`source_sha256 8b1788ca…a10d46`, `image_digest sha256:22a7c789…f96b3f`) as
`uid=1000(oathcast)`, verified through public HTTPS with auth. Renderer v2 is therefore the
**scored surface** now, which is the point of the whole exercise: Telegraph scores the
response text, so P0 only counted once it was serving traffic.

This supersedes the previous status note, which said "nothing in this file's completed items
has been deployed yet" and named v4 as the running release. The P4 collection harness now
runs on **two** legs (GitHub Actions + an EC2 systemd timer), and their outputs were
cross-checked and found identical.

Baseline line above says "v3.2" as of the 2026-08-07 review; that was correct then. A claim
in this file and in `DEPLOYMENT.md` that the host *still* ran v3.2 on 2026-08-10 was wrong —
it came from reading `/readyz`'s `release_id` at the top level when the field is nested under
`release`. Read it as `release.release_id`.

This register is an actionable list, not a claim that any item is complete. Mark items only
when the change is merged and covered by tests.

---

## A. Security gaps

### A1. Strengths already in place (do not regress)

- Timing-safe Bearer comparison (`hmac.compare_digest`), fail-closed auth when
  `OATHCAST_REQUIRE_AUTH=true` and no token is provisioned (`src/oathcast/service.py`).
- Immutable receipts via SQLite triggers plus digest integrity check on read
  (`src/oathcast/receipts.py`).
- Payment boundary: no fabricated proofs, HTTPS-only signing, recipient/amount/deadline/
  resource validation, `RESERVED -> SUBMITTED -> SETTLED/UNKNOWN` journal, spend caps,
  duplicate blocking (`src/oathcast/payment.py`).
- Secrets hygiene: no keys in git history, `.gitignore` coverage, secret scanning enabled,
  loopback-only container port, security group 80/443 only.

### A2. Gaps, ranked

- [x] **S1 — Shared rate-limit bucket behind Caddy. ALREADY DONE (verified 2026-08-10).**
  The register was stale on this item. `ForecastService.rate_limit_identity` accepts a
  forwarded identity only when the socket peer is inside a configured
  `trusted_proxy_networks` range, and falls back to the socket peer otherwise. Covered by
  `test_forwarded_rate_limit_identity_requires_a_trusted_socket_peer` and
  `test_forwarded_public_clients_do_not_share_auth_failure_buckets`.

- [x] **S2 — Unbounded receipt-store growth. DONE 2026-08-10.**
  `event_id` is caller-controlled and every receipt stores the full raw provider payload, so
  an authenticated client could grow the disk at up to 120 receipts/min with no bound — over a
  three-week judging window.

  **A deletion-based retention policy was considered and rejected.** Receipts exist to be
  replayed after cutoff; evicting them would destroy the exact property they provide, and the
  DELETE trigger would reject it anyway. The shipped shape is a **capacity cap that refuses
  new writes while always allowing replays**:
  - `SqliteReceiptStore(path, max_rows=200_000, max_bytes=512 MiB)`; either cap can be
    disabled with `None`. Non-positive caps are rejected at construction, not on first
    forecast.
  - `save()` checks for an existing row *before* applying the cap, so **a replay of an
    already-issued receipt still succeeds at capacity.** This is the load-bearing case: the
    Miner has publicly committed to those events and must keep honouring them.
  - `ReceiptStoreFull` → HTTP **507**. The service fails closed rather than serve a forecast
    it cannot record — an unrecorded forecast cannot be replayed or verified, which is the
    opposite of what this service sells.
  - `/readyz` surfaces `receipt_store` capacity and goes **503** when full. `/healthz` stays
    **200** — Docker's `HEALTHCHECK` probes `/healthz`, so failing it would turn a capacity
    stall into a restart loop instead of a diagnosable state.
  - Canary gains a `receipt_capacity` check that fails at **<10% headroom**, i.e. *before*
    the cliff. A deployed release that predates the field reports `reported: false` rather
    than failing, since a live host can legitimately lag the repo between merge and redeploy.
  - Caps are overridable via `OATHCAST_RECEIPT_MAX_ROWS` / `OATHCAST_RECEIPT_MAX_BYTES`;
    an unparseable value is a startup error so a typo cannot silently remove a bound.

  **Two bugs were found and fixed while implementing this:**
  - `ReceiptStoreFull` was being swallowed by the provider loop's `except Exception` and
    surfacing as a **502**, which would have sent an operator to debug the upstream provider
    for a local disk problem — and pointlessly re-fetched upstream for each remaining
    provider. Now re-raised alongside `ReceiptConflict`.
  - The `:memory:` connection never had `row_factory` set, so **every name-based column
    access on an in-memory store raised `TypeError`.** Latent because no prior test used one.

  Covered by 18 tests across `tests/test_receipts.py`, `tests/test_service.py`
  (`ReceiptCapacityHandlerTests`), and `tests/test_canary.py`.

- [x] **S3 — Default `event_id` collision. ALREADY DONE (verified 2026-08-10).**
  Also stale. `default_event_id` returns `request-{sha256}` over every canonical question
  field, not just the start timestamp. Covered by
  `test_default_event_id_is_stable_and_bound_to_the_canonical_question`.

- [x] **S4 — Container hardening. DONE 2026-08-10.**
  `Dockerfile` now creates a non-root `oathcast` account, runs `USER 1000:1000`, and adds a
  `HEALTHCHECK` against `/healthz` (which is served before the auth check, so the probe
  carries no credentials and needs no extra packages).

  **The UID choice is load-bearing and was nearly wrong.** The first draft used UID 10001.
  A bind mount preserves *host* ownership, and the durable EC2 directory
  `/home/ec2-user/oathcast/data` is owned by uid 1000 — so a container running as 10001
  cannot write receipts, while `/healthz` and `/readyz` both still report 200. That failure
  mode is silent: the Miner looks healthy and fails every forecast at persistence time.

  This was initially masked by a testing artifact: Docker Desktop on macOS virtualizes
  ownership for named volumes and reported `WRITE OK` for a directory it claimed was owned
  by uid 1000. A kernel-level check with real uid separation showed
  `PERMISSION DENIED`. The image is pinned to UID/GID 1000:1000 to match the host, and
  verified end to end: non-root identity, `/healthz` 200, `/readyz` 200, unauthenticated
  forecast 401, Docker health `healthy`, receipt DB created on the mount.
  **Changing this UID requires chown-ing the host directory in the same change.**

- [x] **S5 — Receipts are digested, not signed. DONE 2026-08-10.**
  The gap: anyone with the SQLite file can rewrite a receipt *and* recompute a valid
  `receipt_sha256`, producing a **self-consistent forgery** that every per-receipt check
  accepts. Triggers only stop SQL-level mutation, not edits to the file.

  Shipped a hash chain plus an external anchoring workflow:
  - `SqliteReceiptStore.chain_head(limit=None)` — `chain[0] = sha256(domain)`,
    `chain[i] = sha256(chain[i-1] || event_id || recomputed_digest)`, ordered by `rowid`.
  - `scripts/anchor_receipt_head.py --output` writes an anchor record; `--verify` recomputes
    a published anchor and **exits 1** on mismatch.
  - `receipt_digest` moved from `service.py` into `receipts.py` so store and service hash
    identically; `service.py` now imports it.
  - `SqliteReceiptStore.get` raises the new `ReceiptTampering` when stored bytes do not match
    a *present* digest field. Receipts with no digest field stay readable — refusing them
    would turn a missing feature into data loss.

  **Three design points that are load-bearing:**
  1. **Chain, not set digest.** A head published at N receipts stays verifiable forever:
     recomputing over the first N rows must reproduce it. A digest over the whole set would
     go stale on every new receipt, so nobody would keep verifying it.
  2. **Ordered by `rowid`, not `created_at`.** `created_at` is wall-clock; a replayed or
     clock-skewed receipt could reorder an already-published prefix and break a valid anchor.
     Deletes are trigger-blocked, so rowids are never reused and prefix order is stable.
  3. **Chain uses the *recomputed* digest, not the receipt's own `receipt_sha256`.** This is
     the whole point — a forger who rewrites both still moves the head.

  **The anchor is only worth anything once published where OathCast cannot rewrite it**
  (git commit, X post, Explorer memo). An anchor file living only in this repo proves nothing
  against someone who can also edit the repo. This is stated in the script docstring.

  **First real anchor written 2026-08-10** against the live production store:
  `head_sha256 8a63dba5…40e230` over 6 receipts, `integrity_check: ok`, no self-reported
  digest mismatches, first receipt 2026-08-04T18:51:27Z. Committed to this repo at
  `artifacts/receipt-anchors/anchor-2026-08-10.json` and verified byte-identical to the host
  copy (`ad116b0f…e835c4`). **By the standard in the paragraph above, this is not yet
  evidence** — the git commit is the weakest of the three publication venues, since the same
  party controls the repo and the receipts. It demonstrates the chain is internally consistent
  and establishes a dated prefix; it becomes third-party checkable only when a head is
  published somewhere append-only and outside this repo's control. The X post or an Explorer
  memo is the step that actually closes this, and it is still open.

  Covered by 16 tests (`tests/test_receipt_chain.py`, `tests/test_receipt_anchor.py`),
  including a self-consistent forgery that defeats the per-receipt check but still moves the
  head, and a truncated store that must **not** verify against a shorter prefix. Tamper
  simulation edits raw database bytes length-preservingly, because the immutability trigger
  correctly refuses the SQL path. CLI verified end to end: clean verify `ok: true`, tampered
  verify `ok: false` naming `demo-2`, exit code 1, overwrite refused without `--overwrite`.

- [ ] **S6 — Small hardening items.** (S6a, S6b done; S6c open by design.)
  - [x] **S6a — provider body byte cap. DONE 2026-08-10.** `fetch_json` capped at 2 MB
    (`MAX_PROVIDER_BODY_BYTES`). An unbounded `read()` let a hostile or malfunctioning
    upstream exhaust memory. The implementation reads one byte past the cap so a body
    sitting exactly on the limit is accepted while a larger one is detected rather than
    silently truncated into a misleading JSON parse error, and rejects an over-cap declared
    `Content-Length` before reading any body. Covered by 9 tests in
    `tests/test_service.py::ProviderBodyCapTests`.
  - [x] **S6b — lat/lon range validation. ALREADY DONE (verified 2026-08-10).**
    `_parse_coordinate` enforces finite values within [-90, 90] and [-180, 180]; covered by
    `test_query_parser_rejects_nonfinite_and_out_of_range_coordinates`.
  - [ ] **S6c — dispatcher default is plaintext HTTP to a raw IP**
    (`src/oathcast/payment.py`) — already barred from signing; keep it preflight-only and
    switch to HTTPS the moment an official HTTPS dispatcher exists. Open by design, not by
    omission: it is blocked on Telegraph publishing one.

---

## B. Purpose / winning gaps

Pattern at review time: every **live** box in `docs/submission-checklist.md` is unchecked —
no registration, no paid request, no Explorer presence, no demo video, no real users, no X
evidence. Judging weights (from `handoff.md` §14):

| Track | Biggest weight | State at review |
|---|---|---|
| Miner | Performance 75% | Raw Open-Meteo passthrough; parity risk with competitors |
| Application | Real usage 45% + X 25% | Planning Desk has zero users; no public surface |
| Script Author | Improvement 50% | Blocked on official WASM harness (platform gate) |

- [ ] **P1 — No differentiation in forecast quality (attacks the 75% weight).**
  **Superseded in priority by P0 below.** OathCast serves Open-Meteo's probability verbatim.
  The originally-proposed lever was **calibration**: use the observation-ingestion boundary
  (`src/oathcast/ground_truth.py`, `scripts/validate_observations.py`) and the leakage-safe
  backtest (`src/oathcast/backtest.py`) to post-process raw probabilities (e.g., isotonic
  regression) and improve Brier/reliability.

  **Correction (2026-08-10):** Brier is *not* currently part of Telegraph's scoring model
  (`handoff.md:87`, `:249`). The live scorer is a `0..1` composite over cosine similarity,
  BM25 word overlap, and response-length quality — all computed on the **response text**.
  A calibration layer therefore improves a number nobody is currently scoring. Calibration
  remains worth doing for honesty and for the Track 2 story, and observation accumulation
  still compounds with time, but it is no longer the highest-leverage Track 1 item.

- [x] **P0 — Renderer is the real Track 1 lever. DONE 2026-08-10.**
  Since Miner performance (75% of Track 1) is scored on response text, `src/oathcast/render.py`
  *is* the scoring surface. The shipped v1 sentence scored **0.4424** on the local proxy —
  below the 0.55 good-response threshold — for two concrete reasons: ISO-8601 stamps tokenize
  into fragments (`2026`, `08`, `17t15`, `00z`) that match nothing a resolution would say, and
  the sentence never answered the yes/no question that was asked.

  `semantic_text_v2` now leads with IPCC AR6 calibrated wording, states the window in readable
  UTC clock time, and keeps the exact percentage. Measured gains (local proxies, **not**
  Telegraph scores) are in `docs/renderer-experiment.md`; evidence JSON in
  `artifacts/renderer-benchmark/`. v1 is retained as `render_forecast_content_v1` so the change
  stays measurable and reversible. Covered by 10 tests in `tests/test_render.py`, including a
  1001-point sweep asserting the renderer trips no anti-gaming issue at any probability.

- [ ] **P2 — No demand surface for Track 3.**
  Real usage is 45% of the Application score; the cash guardrail needs 100 real requests
  (~15 users x 7 requests). The Planning Desk is currently a local script. Build a simple
  public web intake surface and start recruiting real pilot users (Lagos event organizers,
  vendors, market groups) **now** — recruiting takes calendar time; the Track 3 window opens
  2026-08-31.

- [ ] **P3 — No public product surface.**
  The pitch is a "public calibration court" but the public interface is a raw JSON API.
  Build a small read-only dashboard at `oathcastcourt.duckdns.org`: open cases, receipts,
  resolved outcomes, per-Miner scorecard, owned-Miner-disabled ablation. Much of this exists
  as Markdown shells (`src/oathcast/presentation.py`, Explorer evidence templates); it needs
  a web layer. Feeds judges (Usefulness/Creativity/Depth = 25%) and X content.

- [ ] **P4 — Single-provider SPOF during judging.** (Collection harness DONE 2026-08-10;
  the equivalence verdict is blocked on accumulating data.)
  Only `open_meteo` is production-verified; WeatherAPI/OpenWeather remain gated as
  unverified at `service.py:396`.

  **Provider choice: WeatherAPI, not OpenWeather.** WeatherAPI's free tier needs no credit
  card and issues a key instantly. OpenWeather One Call 3.0 — which is what
  `adapters/openweather.py` targets — requires a card-backed subscription, and its daily cap
  defaults to 2,000 rather than the free 1,000, so it can bill without the operator doing
  anything wrong. OpenWeather's genuinely card-free tier is the classic 3-hour forecast API,
  which does not carry the hourly `pop` field the adapter reads. Deferred under the standing
  no-new-cost rule.

  **The key alone does not close P4.** A live key was verified on 2026-08-10 (one call, Lagos,
  parsed cleanly through the existing adapter — no code change needed). The gate stays closed
  because `chance_of_rain` has unstated threshold semantics. There is a concrete reason to
  doubt equivalence, visible in the adapters: `open_meteo.py:62` selects the point at
  **`horizon_end`** (its probability is documented as ">0.1 mm in the *preceding* hour"), while
  `weatherapi.py:88` selects at **`horizon_start`** (WeatherAPI labels an hourly block by the
  hour it begins). Both readings match their own provider docs, but if either is wrong the two
  providers are silently answering about different 60-minute windows. The first two collected
  pairs diverge in the **same direction**: 12:00Z **open_meteo 0.01 vs weatherapi 0.13**, and
  16:00Z **0.00 vs 0.11**, against a 0.2305 climatology. The second is the sharper contrast —
  "impossible" against "better than 1 in 10" for the identical hour. But both are *dry* calls,
  so **two cases cannot yet separate the window hypothesis from Lagos simply being dry**; a
  consistent gap is the expected signature, not proof of it. This is why the collector runs on
  a schedule instead of being called once and declared conclusive.

  **`scripts/collect_provider_pairs.py` (new) collects the evidence.** Neither free tier sells
  a historical *forecast* archive, so this can only accumulate forward — a key on 2026-08-16
  yields one day of data before Track 1 opens. Two modes: `collect` appends one unresolved case
  per location at a fixed lead time; `resolve` fills outcomes from an independent observation
  export once the window has closed. Output is the schema `backtest_providers.py` already
  consumes, verified by loading a written dataset through `load_chronological_cases`.

  Design points that are load-bearing:
  - **The key is read only from `WEATHERAPI_KEY` in the environment**, never an argument, since
    arguments land in shell history and process listings. Every error path is scrubbed: urllib
    copies the request URL — which carries the key as a query parameter — into connection-failure
    messages, so an unscrubbed error would write the key into a scheduled job's log. A live
    401 test passed only because that particular message omits the URL; the regression test
    forces a URL-bearing error and asserts `<redacted>` is present, so the scrub cannot be
    silently removed.
  - **A failed provider is recorded with `status: "missing"`, not dropped.** Dropping the case
    would bias the comparison toward whichever provider is more available.
  - **A changed lead time is refused** unless `--allow-lead-change` is passed. Providers compared
    at different lead times measure lead time, not the providers.
  - **Writes are atomic and validated before install** — the dataset is written to a temporary
    file, loaded through the real backtest loader, and only then `os.replace`d. A scheduled job
    that writes an unloadable file is worse than one that writes nothing, because the failure
    surfaces at analysis time.
  - **An open window is never resolved** — that would be leakage — and resolution is idempotent.

  **The climatology placeholder was refused, and that mattered.** `load_locations` rejects
  `climatology_source: "UNSET"`, because Brier skill is measured against that baseline. The
  0.35 sitting in `fixtures/brier_cases.json` is an unsourced development value. The frozen
  replacement is **0.2305**, derived from ERA5 via Open-Meteo's archive API: all 7,440 August
  hours 2015-2024 at the Lagos point, 1,715 with >0.1 mm, matching the question's own threshold.
  Two caveats are recorded in `fixtures/collection_locations.json` rather than hidden: ERA5 is
  ECMWF reanalysis retrieved through Open-Meteo, so it is **not fully independent of the
  `open_meteo` provider**; and the annual rate ranges 0.1089 (2020) to 0.3602 (2021), so a
  single-season sample would have been unrepresentative.

  Covered by 23 tests in `tests/test_collect_provider_pairs.py`; none touch the network.

  **Scheduling: both legs, not one — both now installed and cross-checked.** GitHub Actions
  runs hourly against the `data/provider-pairs` branch and needs no inbound port; the EC2 host
  leg is a **systemd timer**, not cron (`docs/p4-host-collection.md`), installed in the
  redeploy's SSH window and firing every 3 hours at :07.

  The losslessness argument below is no longer just an argument: the host's dataset was pulled
  and merged into the branch copy on 2026-08-10 and added **0 cases** — and going further than
  dedupe, the two legs' probabilities for the same hours are **byte-identical across all four
  provider readings**. Two independent collectors, different networks, different schedulers,
  same answers. That also means a case's value does not depend on which leg produced it, and it
  isolates the provider divergence below as a genuine *provider* disagreement rather than
  collector noise. Running both
  is provably lossless rather than merely redundant: `case_id` is `slug-YYYYMMDDTHHMMZ` with
  `issued_at` floored to the hour and `merge_cases` dedupes on it, so two collectors in the
  same hour converge on **one** case, and drift into a different hour yields extra coverage
  instead of corruption. Both run at lead 3, so the lead-change guard never trips. Their
  failure modes are uncorrelated — GitHub queue load versus this host dying — and the marginal
  cost is zero. The Actions leg is deliberately over-requested (hourly for a 3-hourly need)
  because **GitHub delivers scheduled runs best-effort**: measured on this repository over
  2026-08-06→10 (102.3 h), the `*/15` canary requested 409 runs and received **96 (23%)**, with
  gaps of median 49 min and **max 360 min**, two of them over 180 min. That 23% is *not* the
  drop rate a `0 */3` schedule would see — GitHub sheds high-frequency schedules first — but
  the two six-hour silent windows in four days are directly observed, and a missed hour is
  permanently unrecoverable because no free tier sells a historical *forecast* archive.

  **Scope correction on the provider key.** An earlier draft treated putting `WEATHERAPI_KEY`
  on the Miner host as crossing a documented boundary. It does not: `.env.example:4` has
  carried it since the first spike and `service.py:244` reads it from the Miner's own
  environment. The rule that does exist is narrower — the *payment wallet* private key stays
  local and never enters the Miner container. A leaked weather key costs free quota; it grants
  no access to receipts and cannot move funds. Conflating the two would have blocked a safe
  action while teaching the wrong rule.

  **Still open:** accumulate paired cases, obtain an independent observation export (the
  bundled one is a fixture whose independence is not asserted), run the chronological backtest,
  and only then decide whether `weatherapi` earns `documented_match` and a place in
  `verified_providers`.

- [ ] **P5 — 3-active-Miners guardrail depends on other participants.**
  Cannot be controlled; start coordinating in the official Discord early instead of
  discovering on 2026-09-01 that OathCast is the only weather Miner.

---

## C. What can be done now vs blocked (as of 2026-08-07)

### Can do now (no external dependencies)

**Closed 2026-08-10:** P0 (renderer), S1, S2, S3, S4, S5, S6a, S6b. Test suite 94 → 193.
P4's collection harness is built, its `WEATHERAPI_KEY` secret is set, and it is collecting
hourly on GitHub Actions; its verdict still needs accumulated data and an independent
observation export.

Still open, in priority order:

| Item | Effort | Files |
|---|---|---|
| ~~Set `OATHCAST_MINER_API_KEY` repo secret~~ **DONE 2026-08-10 17:57:36Z** — piped host → `gh secret set` without entering the session. The canary now actually exercises the authenticated path | done | `.github/workflows/oathcast-canary.yml` |
| **Publish a receipt-chain head outside this repo** (X post or Explorer memo). The first anchor exists and is committed, but repo-only publication is not third-party evidence — see §A2 | one post | `artifacts/receipt-anchors/` |
| P4 install the EC2 host collector leg (second, uncorrelated schedule) | 20 min inside the SSH window | `docs/p4-host-collection.md` |
| P3 public dashboard | medium | new web layer, existing presentation code |
| P2 Planning Desk public web intake | medium | `src/oathcast/pilot.py` area |
| P4 independent observation export for resolution | medium | `ground_truth.py` boundary |
| P1 calibration layer + observation accumulation | medium, time-sensitive | `src/oathcast/backtest.py` area |
| S6c dispatcher HTTPS switch (preflight-only until then) | tiny | `src/oathcast/payment.py` |
| Day-one registration runbook (30-min checklist) | small | new doc |
| X cadence with preparation/evidence posts | ongoing | `docs/x-update-drafts.md` |
| Demo video v1 (routing, ablation, receipts) | medium | new artifact |

### Blocked (platform or schedule gates)

- Miner registration — opens 2026-08-17 (frozen YAML / official validator).
- Paid Telegraph requests / Track 3 demand — opens 2026-08-31; needs official HTTPS
  dispatcher, compatible signer/SDK, funded Base Sepolia wallet.
- Script Author WASM work — harness not yet released by Telegraph.
- Explorer evidence — requires registration first.
- 3-Miners guardrail — depends on other participants (lobby in Discord; cannot control).

---

## C0. Telegraph team answers (received 2026-08-10, official Discord)

Three questions were asked after the 168-hour Explorer lookback found neither the
settlement signature nor the payer address in the feed. The answers resolve the
attribution risk that P2/P3 planning was hedging against.

| # | Question | Answer | Consequence |
|---|---|---|---|
| 1 | How is a paid request attributed on the Explorer? | Each signal gets a **unique hash**, searchable on the Telegraph Explorer. Feature was **under development**, going live **EOD 2026-08-10**. | Explains the negative lookback: the attribution path did not exist yet, so absence was **not** evidence of a broken integration. Re-run the canary reconciliation after the feature ships and re-check the 2026-08-09 canary. |
| 2 | Does devnet rehearsal count as Track 3 preparation? | Yes — "same HTTP requests be it on testnet or mainnet." | The Solana-devnet canary is a **valid rehearsal** of the Track 3 request path. It still does **not** count as qualifying demand (see §E), but the integration work transfers. |
| 3 | Does the consumer need a registered identity/key/header? | No. The **paying wallet is the unique identifier**; no additional consumer-side configuration. | Removes an assumed blocker from the Track 3 build. No consumer registration, key issuance, or custom header work is needed. |

**Actions arising**

- [ ] **T1 — Re-run Explorer reconciliation after the hash feature ships.**
  Re-check the archived 2026-08-09 canary
  (`artifacts/payment-canary/execute-2026-08-09.json`, currently
  `explorer_reconciliation.status: "not_found_yet"`) and update it in place with the
  result. Until then that field stays as-is and must not be described as a failure.
- [ ] **T2 — Capture the per-signal hash in canary evidence.**
  Once live, record the signal hash alongside the settlement signature so every request has
  an independently checkable Explorer link. Files: `payment-canary/`, evidence schema.
- [ ] **T3 — Drop consumer-identity work from the Track 3 plan.** Answer 3 makes it dead
  scope. The paying wallet is the identity.

**Still not answered / still true:** the 100-real-request and 3-active-Miner guardrails are
unchanged, and a rehearsal is not demand. Answer 2 confirms the request *path* transfers; it
does not convert devnet traffic into qualifying Track 3 usage.

---

## D. Suggested execution order

**Done 2026-08-10:** P0 renderer, then the full security batch (S1–S6b), then the P4
collection harness. 193 tests passing. All of it **pushed** (`d48d13f`, `1c218c0`; CI green)
after a session in which 24 files existed only on one laptop. The scheduled collector is
live on GitHub Actions and verified by live dispatch.

Remaining, in order:

1. ~~**One command, today:** set `WEATHERAPI_KEY` as a repository secret.~~ **DONE 2026-08-10
   by the operator.** Collection is live and confirmed three independent ways rather than by
   the run's green tick: step conclusion `success` (not `skipped`), branch commit `71ee312`,
   and case count 1→2, then re-validated through the real `load_chronological_cases`. Two
   paired cases now exist, both with both providers `valid`.
2. ~~**Next, needs an SSH window:** ship S2/S5 to `oathcastcourt.duckdns.org`.~~ **DONE
   2026-08-10.** `2026-08-10-hardened-v5` is live and serving renderer v2 through public
   HTTPS with auth — the Track 1 lever (75% of that track) is no longer worth zero. Window
   17:04:57Z → 18:13:02Z, port 22 scoped to a /32: **58 accepted sessions from one IP, 0
   failed or invalid-user attempts** (read from the `sshd` *journal*; `/var/log/secure` is
   empty on AL2023 and reports a misleading zero). Everything needing SSH was done in the one
   window — build, cutover, collector install, receipt anchor, and pulling the anchor and
   dataset back. Details in `handoff.md` and `DEPLOYMENT.md`.
   **2c. The UID fix nearly shipped broken, and the runbook was the reason.** v5 drops to
   `uid=1000`, and a bind mount preserves *host* ownership. `DEPLOYMENT.md` said to `chown`
   the **directory** — which was already correct — while the receipts **file** inside it was
   `root:root 644`. Following the runbook literally would have produced a Miner returning 200
   on `/healthz` and `/readyz` while every forecast failed to persist, because neither probe
   touches the receipt store. `test -w` and `BEGIN IMMEDIATE` both said "writable"; only a
   real `CREATE TABLE` against an exact-ownership copy exposed
   `attempt to write a readonly database`. **For SQLite, trust only an actual write, and
   check the file, not the directory.**
   **2d. The host has no cron.** AL2023 ships without `cronie`, so the runbook's `crontab -e`
   was unexecutable. Replaced with a systemd timer — no new package or daemon, and
   `Persistent=true` recovers a run missed while the instance was down, which cron cannot and
   which matters because a missed collection hour is permanently unrecoverable.
   **2a. Console and region.** The security group can only be reached through the AWS console:
   no `aws` CLI, no `~/.aws`, no environment credentials, so there is no fallback if the
   session fails. The console must be in **`eu-north-1` (Stockholm)** — security groups,
   instances, and key pairs are regional, so `oathcast-web` is invisible from any other region.
   Start only with a stable session and ~30 uninterrupted minutes: a half-open security group
   plus an expired console session is the worst state to be in. Re-check the public IP
   immediately before writing the /32 rule.
   **2b.** ~~The canary is green and blind.~~ **RESOLVED 2026-08-10.** Both secrets are set
   (`WEATHERAPI_KEY` 15:54:46Z, `OATHCAST_MINER_API_KEY` 17:57:36Z); the active Miner key was
   piped host → `gh secret set` without being rendered into the session. For the record of what
   was wrong: 96 consecutive "successes" had skipped "Verify public Miner" entirely because the
   secret was unset. **The key is self-generated, not Telegraph-issued** — the YAML `auth` block
   only declares that clients send `Authorization: Bearer <value>`, and the operator picks the
   value. The copy at `~/Downloads/oathcast-miner.env` is the **retired** key (proved by a smoke
   test returning 401 on the authenticated call).
   **2e. The canary was also pinned to a fixture that could never keep passing.**
   `fixtures/question.json` names a fixed 2026-08-17T15:00Z horizon, which is past Open-Meteo's
   rolling 7-day window today *and* past its own `forecast_cutoff` after 2026-08-17T12:00Z — so
   it 502s now, works for six days, then fails permanently, and a genuine outage would look
   identical. `smoke_miner.py` now computes a rolling horizon (noon UTC tomorrow) anchored to
   the **UTC day**, not to `now`: a per-run horizon would have made each of the 96 daily runs a
   unique question and written 96 synthetic receipts a day into the store meant to evidence
   *real* demand. 5 boundary tests added.
3. **This week:** P3 dashboard + P2 web intake — the visible product; recruit pilot users in
   parallel, because recruiting takes calendar time and Track 3 opens 2026-08-31.
4. **This week:** source an independent observation export so collected cases can be resolved.
   Without it P4 accumulates forecasts it cannot score.
5. **Ongoing:** P1 — observation accumulation compounds with time. No longer the top Track 1
   lever (see P1 correction) but still worth starting early.
6. **Before 2026-08-17:** day-one registration runbook, demo video v1, X cadence start.
7. **Once the per-signal hash ships:** T1/T2 from §C0.

---

## E. Standing rules carried into this work

- Do not present fixtures, local demos, or automated traffic as qualifying hackathon demand.
- Do not sign a payment or fund a wallet before the Track 3 gates in `handoff.md` §5 are met.
- Keep all claims labeled: live vs synthetic vs pending vs platform-dependent.
- Security fixes that change request handling must be covered by the unittest suite before
  redeploying to `oathcastcourt.duckdns.org`; bump the release ID and update the canary's
  expected release/source digests accordingly.
