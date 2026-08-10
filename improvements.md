# OathCast improvement register

Review date: 2026-08-07 (work log updated 2026-08-10)
Context: Full-project review (security, purpose, hackathon-winning potential) ahead of the
Hackathon 1 window (Track 1 opens 2026-08-17; Track 3 opens 2026-08-31).
Baseline at review time: 94/94 tests passing, no secrets in git history, staging live at
`https://oathcastcourt.duckdns.org` (v3.2, auth enforced, `/readyz` healthy).

**Status 2026-08-10: 193/193 tests passing.** The renderer rework (P0) and the entire
security batch (S1–S6b) are merged and covered, and the P4 collection harness is built and
verified against both live providers. Nothing in this file's completed items has
been deployed yet — the running host is still v4 and lags these changes until the redeploy
described in §D step 1.

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
  providers are silently answering about different 60-minute windows. The first collected pair
  showed **open_meteo 0.01 vs weatherapi 0.13** for the same hour — a 13x divergence against a
  0.2305 climatology, in case one.

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
P4's collection harness is built; its verdict needs accumulated data.

Still open, in priority order:

| Item | Effort | Files |
|---|---|---|
| P4 run the collector daily until Track 1 opens | tiny per run, time-sensitive | `scripts/collect_provider_pairs.py` |
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
collection harness. 193 tests passing.

Remaining, in order:

1. **Daily from now until 2026-08-17:** run `collect_provider_pairs.py --mode collect`. This
   is the only item that cannot be compressed later — neither provider sells a historical
   forecast archive, so every day not collected is a day of evidence that cannot be recovered.
2. **Before redeploying:** ship S2/S5 to `oathcastcourt.duckdns.org` — bump the release ID,
   rebuild with the pinned UID, update the canary's expected release/source digests, and
   write the first receipt anchor. See `DEPLOYMENT.md` §"Release 2026-08-10".
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
