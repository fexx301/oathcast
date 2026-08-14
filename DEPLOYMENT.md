# Deployment and payment boundary

The repository packages one public HTTPS OathCast Miner. The
`2026-08-12-hardened-v6` release is running in Docker on AWS EC2
behind private host port 8080, with Caddy terminating HTTPS at
`https://oathcastcourt.duckdns.org`.
Set the environment variables from `.env.example`. Registration is complete and
the registered YAML is frozen; validate a new canonical YAML only before a
separately authorized update or re-registration. The exact deployed release,
public smoke output, and runtime details are archived under
`artifacts/release-evidence/`.

## Current deployment status

The deployed Miner is v6; the v5 and v4 sections below are historical release
records. The public decision UI is deployed separately. Its safe public surface
is a read-only release/status page plus a client-only development fixture. It
has no Telegraph-backed runner, accepts no live Planning Desk intake, and
returns 503 for decision requests. Live decisions must not be enabled until the
authenticated, budgeted payment path has been reviewed and deployed.

UI-only replacements must override the Dockerfile health check, which targets
the Miner on port 8080. Run the UI on Docker bridge networking, publish only
`127.0.0.1:8787:8787`, bind the process to `0.0.0.0:8787` inside the container,
and probe `http://127.0.0.1:8787/health` internally. Caddy sends `/v1/*` to the
Miner, so the public decision fail-closed probe is `/api/decision`, not
`/v1/decision`. The disabled API returns 503 before reading a request body.

## Local run

    OATHCAST_REQUIRE_AUTH=false PYTHONPATH=src python3 -m oathcast.service

Health: http://127.0.0.1:8080/healthz

Forecast endpoint:

    http://127.0.0.1:8080/v1/forecast/point?event_id=dev-1&location_name=Lagos&lat=6.5244&lon=3.3792&start=2026-08-17T15:00:00Z&end=2026-08-17T16:00:00Z

The default service uses the validated Open-Meteo path. WeatherAPI and
OpenWeather are local adapter experiments and cannot be production failovers
until their event equivalence is validated. New forecasts are rejected at or
after their cutoff; an immutable SQLite receipt is stored under
`/data/oathcast/receipts.sqlite3` and its hash is returned in
`X-OathCast-Receipt-SHA256`. Mount `/data/oathcast` to durable host storage
when deploying the corrected image.

The receipt store is capacity-bounded (default 200,000 rows / 512 MiB, override
with `OATHCAST_RECEIPT_MAX_ROWS` and `OATHCAST_RECEIPT_MAX_BYTES`, or `none` to
disable a cap). Once full it returns HTTP 507 for **new** events rather than
serving a forecast it cannot record; replays of existing receipts keep working,
so already-issued commitments are always honoured.

## x402 boundary

`payment-canary/` is the retained one-shot Solana rehearsal boundary. It uses
the official x402 fetch/SVM client packages and permits one unpaid preflight
followed by at most one explicitly enabled paid retry. Before loading a signer
it validates x402 v2, the approved amount cap, recipient, fee payer, Miner ID,
endpoint, and complete request URL. It never fabricates a signature or emits
signing material or a raw settlement header.

The raw-IP `--allow-insecure-http-devnet` path is preserved only to reproduce
the authorized August 9 canary. Current official docs use an HTTPS dev node and
offer Base Sepolia or Solana Devnet payment choices. For any future separately
authorized request, treat the exact received `accepts[]` entry as authoritative
for network, asset, amount, recipient, fee payer when present, and resource;
keep redirects and mismatched authorities/paths/queries fail-closed.

After a Solana settlement, the canary queries Devnet RPC and requires a
confirmed, error-free transaction with the expected signature, fee payer, mint,
and exact token movement. Preserve Telegraph `signal_hash`/node evidence and
independent settlement proof separately; the August 9 result remains a rehearsal,
not qualifying demand. `src/oathcast/payment.py` remains a legacy Base-Sepolia
policy/journal regression harness and must not be used as the current signer.

Set `OATHCAST_MINER_API_KEY` on the host to enforce the Bearer token declared in
the canonical YAML. Keep the payment wallet local; never put its private key
in the Miner container.

The deployed service must also set `OATHCAST_REQUIRE_AUTH=true`; startup fails
closed when the API secret is absent. Keep `/data/oathcast` on a durable host
volume so event receipts survive a container replacement. During a future key
rotation, set `OATHCAST_MINER_API_KEYS` to a temporary comma-separated overlap,
recreate the container so its `--env-file` is reloaded, run the public smoke
test with both credentials, and then remove the retired key. The staging
rotation on 2026-08-04 followed that procedure; see the non-secret evidence
artifact in `artifacts/release-evidence/`.

Telegraph's dedicated registration credential is a persistent client credential,
not a disposable validation token: the portal returned `api_key_stored: true`.
Keep it active until Telegraph documents and verifies a coordinated replacement.
A rollback must therefore recreate the pinned image from the **current** owner-only
env file and preserved mount/network/restart settings. Starting a container that
predates the Telegraph credential can restore process availability while silently
breaking Telegraph routing. The sanitized 2026-08-13 cutover evidence is
`artifacts/release-evidence/oathcast-2026-08-13-telegraph-credential-cutover.json`.
That procedure was rehearsed with both credentials on `127.0.0.1:18080` against
a disposable copy of the verified 12-row backup. The replay was identical for
both credentials, the source/copy SHA-256 stayed exact, row count stayed 12,
the transactional write probe rolled back, and the disposable container and
directory were removed. The live receipt database was never mounted.

## Release provenance and smoke test

Create a source manifest before each deployment:

    PYTHONPATH=src python3 scripts/validate_miner_drafts.py
    PYTHONPATH=src python3 scripts/create_release_manifest.py \
      --release-id 2026-08-12-hardened-v6 \
      --output /tmp/oathcast-release-manifest.json

Build the image with the manifest's `source_sha256` and a unique release ID:

    docker build \
      --build-arg OATHCAST_RELEASE_ID=2026-08-12-hardened-v6 \
      --build-arg OATHCAST_SOURCE_SHA256=<manifest-source-sha256> \
      -t oathcast:2026-08-12-hardened-v6 .

After deployment, verify the exact release without printing secrets:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-12-hardened-v6 \
      --require-receipt-write-probe

The smoke test is non-destructive with respect to Telegraph and uses one
ordinary authenticated request against the OathCast service only; it does not
create paid demand. Record its JSON output with the release manifest.

For the cutover itself, freeze one v5 question and its safe fingerprints before
replacing the container, then replay that exact question after v6 starts:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-10-hardened-v5 \
      --question-output /tmp/oathcast-v5-replay-question.json \
      > /tmp/oathcast-v5-before.json

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-12-hardened-v6 \
      --require-receipt-write-probe \
      --question-file /tmp/oathcast-v5-replay-question.json \
      > /tmp/oathcast-v6-after.json

    PYTHONPATH=src python3 scripts/compare_release_replay.py \
      --before /tmp/oathcast-v5-before.json \
      --after /tmp/oathcast-v6-after.json \
      --output /tmp/oathcast-v5-to-v6-replay.json

The comparator requires distinct release IDs and exact equality of the event
ID, receipt hash, and canonical public-response hash. Archive all three JSON
files as release evidence. It never needs or records the Bearer token.

Before changing the live container, treat these as hard gates rather than
follow-up checks:

1. Transfer the complete candidate file set, including every currently
   untracked runtime file; reproduce source digest
   `2bc559e5673297b84e119ac03c0b63304638c13c6ee985ab42a9bd44dbfb4a66`
   on the host and abort on any mismatch.
2. Back up a pre-v6 live database with the standard-library SQLite online
   backup API, opening the source in read-only URI mode. Do not construct the
   v6 `SqliteReceiptStore` against the live legacy database merely to back it
   up: initialization may create the write-probe table. Verify source and
   backup integrity plus row-count equality, then audit the stored receipt JSON
   for missing `public_response` without printing receipt content; any such row
   will intentionally replay as 503 under v6 and must be understood before
   cutover. `scripts/backup_receipts.py` is appropriate after v6 owns the schema.
3. Build v6 and record its image digest. Start a disposable candidate container
   on loopback with a copy of the receipt database; require health, readiness,
   the transactional write probe, authenticated forecast, replay, non-root UID,
   and durable-volume behavior to pass before replacing v5.
4. Capture the v5 replay question/fingerprints above and preserve the stopped v5
   container as the immediate rollback target.

After cutover, require the strict v6 smoke, the exact replay comparator, a
second persistence/restart check against the real host volume, and a manual
canary pass. Roll back immediately on any identity, replay, readiness, or
persistence mismatch. Update the canary release/source/image pins only after
the real v6 image digest exists.

Live execution remains gated on a dedicated faucet-funded Solana-devnet wallet.
The unpaid Miner-18 preflight passed on 2026-08-09 and is archived under
`artifacts/payment-canary/`; it did not read a wallet or create demand.

Before choosing external Miners, run:

    PYTHONPATH=src python3 scripts/discover_live_miners.py

This is a read-only registry check. It prints current active weather
capabilities and advertised minimum prices, but it does not count as demand.
For evidence, retain a timestamped snapshot:

    PYTHONPATH=src python3 scripts/discover_live_miners.py \
      --output /tmp/telegraph-integrations-2026-08-04.json

Then inspect one target without paying:

    PYTHONPATH=src python3 scripts/preflight_miner.py --miner-id 18 --endpoint predict

This must be treated as a preflight only. Do not attach a signer until the
challenge's network, USDC asset, recipient, optional resource URL, exact
amount, and target are all approved.

## Current AWS staging

The staging host is an Amazon Linux 2023 `t3.micro` in `eu-north-1`
(Stockholm) — instance `i-0c4948734b7a6326c`, security group `oathcast-web`,
key pair `oathcast-ec2`. Region matters operationally: the security group,
the instance, and the key pair are all regional objects, so the console must
be switched to `eu-north-1` before any of them is visible. The
`oathcast:2026-08-12-hardened-v6` runs as container `oathcast` with restart
policy `unless-stopped`; stopped container
`oathcast-image-identity-rollback-20260813` preserves the immediate pre-identity
replacement state on the same pinned image. `/healthz`, `/readyz`, authenticated forecast,
transactional write, and restart/replay smoke tests have passed. The Miner is
bound to loopback port 8080 and the `oathcast-caddy`
container uses host networking to terminate HTTPS and redirect HTTP to it. The
security group exposes only ports 80 and 443. DuckDNS currently maps
`oathcastcourt.duckdns.org` to `13.49.229.253`. An EC2-side DuckDNS updater is
now installed and runs from a root-only token file every five minutes because
the public IPv4 is ephemeral.

Deployment verification on 2026-08-03:

- `https://oathcastcourt.duckdns.org/healthz` returned 200 with a trusted
  certificate.
- `/readyz` is the deployment readiness endpoint; it reports release identity,
  auth configuration, and receipt-store configuration without exposing secrets.
- HTTP `/healthz` returned a 308 redirect to HTTPS.
- Unauthenticated `/v1/forecast/point` returned 401 with `WWW-Authenticate`.
- An authenticated near-term Lagos forecast returned 200 through Caddy.
- Public port 8080 timed out; it remains loopback-only.
- The temporary EC2 Instance Connect prefix-list rule was removed after updater
  setup; only HTTP and HTTPS remain in the security group.

Hardened release verified and deployed on 2026-08-04:

- Release ID: `oathcast-2026-08-04-hardened-v3-2`
- Source manifest digest: `df62b88dbb4a4e6661e944a910269058f910d9f1201c27b5ac1833f19496d976`
- Source bundle SHA256: `66c8b2b455db48d0a11e9ca5c820ba3e8723b9e4a96cb346b9fd966ef322438b`
- Runtime image ID: `sha256:34183579a239fdfd3048519c8c5551a428a7ed2665b2df6b312b86e36bd1d71`
- Production container: `oathcast:v3-2-correct`, restart policy
  `unless-stopped`, durable bind `/home/ec2-user/oathcast/data:/data/oathcast`.
- The deployed v3.2 artifact passed the local 64-test suite, compile check,
  four Miner-draft validations, and disposable container smoke. Follow-on
  local recovery/canary/ablation work raises the current repository suite to
  66 tests but is not part of this v3.2 image.
- Public health, readiness, unauthenticated rejection, and authenticated
  forecast checks passed. The authenticated forecast was replayed after a
  container restart with the same receipt hash, proving durable receipt
  replay for the checked event.
- Evidence files: `oathcast-2026-08-04-hardened-v3-2-manifest.json`,
  `oathcast-2026-08-04-hardened-v3-2-public-smoke.json`, and
  `oathcast-2026-08-04-hardened-v3-2-runtime-evidence.json`.
- Staging key rotation evidence: overlap old/new `200/200`, then retired old
  `401` and active new `200`; public health/readiness remained `200`. The
  security group was restored to public ports 80/443 only.

## Release 2026-08-10 — renderer v2 and security batch (DEPLOYED 2026-08-10)

**Deployed and verified live.** The running host is:

    release_id    2026-08-10-hardened-v5
    source_sha256 8b1788cae3c43bcadc03a7e1d9c5b390553fd7798b587e7a3b989ed833a10d46
    image_digest  sha256:22a7c7893e2339cae070de9b2676845684008a438133eb69a098e914c4f96b3f
    runs as       uid=1000(oathcast)          <-- v4 ran as root

Verified through public HTTPS with authentication rather than from inside the
host: 200 with receipt
`276ca07d0e5a64e9f64d5b1dbcd08a6e7177c68d22e6adbf845a3af13b5e281c` and renderer
v2 text — *"Measurable precipitation > 0.1 mm is very unlikely to occur in Lagos
in the hour from 20:00 to 21:00 UTC on 10 August 2026. Probability: 0%."*
Renderer v2 is therefore the **scored surface** as of this release.

Cutover facts worth keeping: the 5 pre-existing receipts survived (6 after the
verification call), v4 is preserved stopped as `oathcast-v4-rollback-20260810`
as the rollback target, a byte-verified backup sits at
`/home/ec2-user/oathcast/receipts-preV5-backup.sqlite3`, and
`oathcast-decision-ui` was deliberately left on v4 rather than cutting both at
once. Replay determinism was checked after cutover: an identical question
returned an identical hash and wrote no new row.

**Corrected 2026-08-10.** An earlier version of this section claimed `/readyz`
reported no release ID and that the host was still serving the 2026-08-04 v3.2
image. Both claims were wrong, and the error was mine: `/readyz` nests release
identity one level down, so reading `release_id` at the top level returned
nothing and I read that absence as "no release." The host at that point ran

    release_id    oathcast-2026-08-09-readiness-v4
    source_sha256 d3069ec17f6fc7f24224b3ebc3803e665500f24f9e71fd948fbc98b9b20de233
    image_digest  sha256:6cd62e074ccd33aa1233e6e750d9163bcd09870981c05a92045ecd3938e6a66a

The gap was one release, not two. The v3.2 record at "Hardened release verified
and deployed on 2026-08-04" above stays as written: it is accurate history, not a
current-state claim. Read `/readyz` as `release.release_id`, never `release_id`.

**The canary was green and blind — resolved with this redeploy.** Every scheduled
canary run before 2026-08-10 took its *skip* path: `OATHCAST_MINER_API_KEY` was
not configured as a repository secret, so "Verify public Miner" was skipped and
the job succeeded having checked nothing. 96 consecutive green runs verified
nothing. Both secrets are now set — `WEATHERAPI_KEY` 15:54:46Z and
`OATHCAST_MINER_API_KEY` 17:57:36Z, the latter read from the host's `.env` and
piped straight into `gh secret set` without being rendered anywhere.

**A second canary defect, found while verifying this release.** Setting the
secret exposed the next layer: the smoke test pinned `fixtures/question.json`,
whose horizon is a fixed 2026-08-17T15:00Z. That is **past Open-Meteo's rolling
7-day window today** — `select_exact_point` correctly refuses to substitute a
neighbouring hour, so it surfaces as `provider_unavailable` → 502 — and it goes
**past its own `forecast_cutoff` after 2026-08-17T12:00Z**, which `service.py:435`
rejects. It therefore fails today, works for six days, then fails forever, and a
genuine outage would be indistinguishable from it. `smoke_miner.py` now computes
a rolling horizon: 12:00–13:00 UTC *tomorrow*, giving 12–36 h of lead and a
cutoff always ≥11 h in the future.

Anchoring to the next UTC **day** rather than `now + N hours` is deliberate and
load-bearing. The receipt hash derives from the canonical question, so an
identical question replays one receipt instead of writing a row. A horizon that
moved with every run would make each of the 96 daily canary runs a distinct
question and write **96 synthetic receipts per day** into the store that is meant
to be evidence of *real* demand. Stable within the day, it costs at most one.

**What changes at runtime**

- **Response text changed.** `render.py` now emits `semantic_text_v2`, which leads
  with IPCC AR6 calibrated wording and readable UTC clock time instead of ISO-8601
  stamps. This is the Miner's scored surface, so the change is deliberate and
  measured; see `docs/renderer-experiment.md`. Receipts issued before the redeploy
  keep their original text, which is correct: a receipt records what was actually
  answered.
- **Container runs as non-root**, `USER 1000:1000`, plus a `HEALTHCHECK` against
  `/healthz`.
- **Receipt store is capacity-bounded.** New receipts past the cap return HTTP 507;
  replays of existing receipts always succeed. V6 adds two post-v5 hardening
  changes: replay returns the stored, digest-covered `public_response` instead
  of invoking the current renderer, and `/readyz` reports a cached
  transactional SQLite write probe. This bullet records the v5→v6 change; both
  behaviors are now deployed.
- **Legacy replay fails closed.** A receipt without a stored `public_response`
  cannot prove what bytes the Miner originally served. It returns HTTP 503
  `receipt_store_unavailable`; do not regenerate or backfill it with the current
  renderer. Recover the original response evidence from a verified backup.
- **Provider bodies are capped at 2 MB** (`MAX_PROVIDER_BODY_BYTES`).
- **`get()` now raises on a rewritten receipt** whose bytes disagree with its
  recorded digest.

**The UID is load-bearing — read this before rebuilding.** The image is pinned to
UID/GID 1000:1000 to match the `ec2-user` owning the durable host directory
`/home/ec2-user/oathcast/data`. A bind mount preserves *host* ownership, so a
container running as any other UID cannot write receipts — while `/healthz` and
`/readyz` both still return 200. Every forecast then fails at persistence time and
nothing in the health surface says so. Docker Desktop on macOS virtualizes ownership
for named volumes and will report a false success here; verify with real UID
separation, not a named volume.

**Measured on the host 2026-08-10 — this WOULD have broken the v5 deploy, and was
fixed in the window.** Keep this section: the trap is a property of bind mounts and
recurs on every host that has ever run the container as root.

The check is not "is the *directory* owned by 1000", which was the assumption that
nearly let this through — and which the previous version of this very section
encouraged, by saying only that changing the UID "requires chown-ing the host
directory." The directory was already correct. The file inside it was not:

    /home/ec2-user/oathcast/data                 uid=1000 gid=1000 mode=700
    /home/ec2-user/oathcast/data/receipts.sqlite3 uid=0    gid=0    mode=644   <-- root

The running v4 container has **no `USER` set and runs as root** (`docker inspect
oathcast --format '{{.Config.User}}'` is empty), so it created the database as root.
v5 runs as 1000, and 1000 cannot write a root-owned `mode 644` file even inside a
directory it owns — SQLite needs write permission on the *file*, and the directory
bit only governs create/unlink. Proved on a byte-copy with identical ownership so
production receipts were never touched:

    as UID 1000, file root:root 644  -> OperationalError: attempt to write a readonly database
    after chown 1000:1000 on file   -> write succeeded

**Two probes disagree here; trust the SQLite one.** `test -w` reported the file as
writable while a real `BEGIN IMMEDIATE` on the *live* database appeared to succeed —
`test -w` is unreliable under some overlay/bind combinations, and a `BEGIN IMMEDIATE`
that touches no page can defer the write fault. Only an actual write (`CREATE TABLE`)
against an exact-ownership copy gives a trustworthy answer.

Required step in the v5 window, before starting the new container:

    sudo chown -R 1000:1000 /home/ec2-user/oathcast/data

Then verify a real forecast persists and replays after the switch — a 200 from
`/healthz` proves nothing about this failure mode.

**Done 2026-08-10, in that order**: backup taken and verified by digest equality
*before* the chown, chown applied, content digest re-verified after, then the v5
container started and a real authenticated forecast through public HTTPS returned
200 with receipt `276ca07d…5e281c` and persisted. Replay of the identical question
returned the identical hash and wrote no new row. The receipt count went 5 → 6,
which is the only observation that actually distinguishes "persisting" from
"returning 200 and dropping the write."

**Steps**

    PYTHONPATH=src python3 -m unittest discover -s tests -t .   # full suite passes locally
    PYTHONPATH=src python3 scripts/validate_miner_drafts.py
    PYTHONPATH=src python3 scripts/create_release_manifest.py \
      --release-id 2026-08-10-hardened-v5 \
      --output /tmp/oathcast-release-manifest.json

Run on 2026-08-10 at commit `1c218c0`: 193 tests OK, drafts valid, and the
manifest covers 56 files with

    source_sha256 = 8b1788cae3c43bcadc03a7e1d9c5b390553fd7798b587e7a3b989ed833a10d46

That digest is reproducible across runs, so the host build can be checked
against it. **Re-run the manifest if any tracked file changes before the build**
— the digest covers the tree, not the release name.

**If the host reproduces a different digest, diff the manifests before assuming a
bad transfer.** On 2026-08-10 the host produced `a5badc5a…` against the pinned
`8b1788ca…`. The cause was not corruption: `create_release_manifest.py`'s
`INCLUDE` tuple is `("src", "miners", "scripts", "Dockerfile", "Caddyfile",
"pyproject.toml", ".env.example")` — and **`.env.example` is a dotfile**, which the
sync had excluded. All 55 shared files matched byte-for-byte; one file was simply
absent. After confirming `.env.example` carries empty values for every
secret-bearing key, and that the real `.env` (mode 600) was untouched, copying it
reproduced the pinned digest exactly. Diffing the two file lists takes a minute
and tells you which of "wrong bytes" or "missing file" you have.

**Do not use macOS `rsync` for this transfer.** rsync 3.4.0 on macOS crashes with
`buffer overflow: recv_rules (exclude.c:1683)`. Worse, the `rsync exit=0` printed
next to the crash was `tail`'s exit status, not rsync's — a pipeline reports the
*last* command's status, so this failure can read as success. Use tar over SSH and
verify a digest on both ends:

    tar -cf - --exclude __pycache__ src scripts fixtures | ssh -i <key> host 'tar -xf - -C ~/oathcast/collection'

    docker build \
      --build-arg OATHCAST_RELEASE_ID=2026-08-10-hardened-v5 \
      --build-arg OATHCAST_SOURCE_SHA256=8b1788cae3c43bcadc03a7e1d9c5b390553fd7798b587e7a3b989ed833a10d46 \
      -t oathcast:2026-08-10-hardened-v5 .

**This build happens on the host, which is why the redeploy needs SSH.** Every
image the host has ever run was built locally on it — `oathcast:staging`, then
`oathcast:v3-2-correct`, then `oathcast:oathcast-2026-08-09-readiness-v4`, then
`oathcast:oathcast-2026-08-10-hardened-v5`. There is no registry to pull from,
so the source has to reach the host and be built there. Reopening port 22 scoped
to a /32 is a specific, time-bounded maintenance operation and needs explicit
authorization. Since the window has to open anyway, install the P4 collector
timer in the same window — see `docs/p4-host-collection.md` (Amazon Linux 2023
has no cron; the runbook now uses systemd). The v4 container is
the rollback target; leave it stopped rather than removed, as was done for v3.2
(`oathcast-v3-2-rollback-20260809`).

After deployment, verify without printing secrets:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-10-hardened-v5

Then confirm the new surfaces specifically:

- `docker inspect --format '{{.State.Health.Status}}' oathcast` is `healthy`.
- `docker exec oathcast id -u` prints `1000` (non-root).
- `/readyz` includes `receipt_store` with `accepting_new_receipts: true`.
- A receipt is written to the host bind mount — this is what a wrong UID breaks
  silently, so check the file, not the health endpoint.
- Update the canary's expected release and source digests, then run it.

**Keep the canary verifying.** Update the three pins in
`.github/workflows/oathcast-canary.yml` to the new release ID, the
`source_sha256` above, and the image digest that `docker images --no-trunc` shows
after the build. `OATHCAST_MINER_API_KEY` is already a repository secret. If it
is missing, the workflow now fails visibly instead of skipping. Confirm the run
reaches and passes "Verify public Miner".

**Write the first receipt anchor after the redeploy** (S5). The anchor is only
worth something once published where OathCast cannot rewrite it:

    PYTHONPATH=src python3 scripts/anchor_receipt_head.py \
      --database /home/ec2-user/oathcast/data/receipts.sqlite3 \
      --output artifacts/receipt-anchors/anchor-2026-08-10.json \
      --note "post-redeploy baseline"

Commit that file, and re-verify later with `--verify`; a non-zero exit means the
anchored prefix has been altered.

    PYTHONPATH=src python3 scripts/anchor_receipt_head.py \
      --database /home/ec2-user/oathcast/data/receipts.sqlite3 \
      --verify artifacts/receipt-anchors/anchor-2026-08-10.json

## Provider-equivalence collection (P4, time-sensitive)

Continue collecting throughout the hackathon window. Neither Open-Meteo nor
WeatherAPI sells a historical *forecast* archive, so every missed collection
hour is evidence that cannot be recovered later.

**Scheduled leg (live).** `.github/workflows/collect-provider-pairs.yml` requests
an hourly run and appends to the `data/provider-pairs` branch. It is deliberately
over-requested: GitHub delivers scheduled runs best-effort — measured on this
repository over 2026-08-06..10, the `*/15` canary received 96 of 409 requested
runs (23%), with gaps up to 360 minutes. Over-requesting cannot double-count,
because `case_id` floors `issued_at` to the hour, so extra runs converge on one
case. The workflow now fails if `WEATHERAPI_KEY` is absent or fewer than two
provider attempts are valid. A degraded case is still committed with the failed
provider marked `missing`, preserving availability evidence without presenting
the run as a successful pair.

**Host leg (installed under systemd).** `docs/p4-host-collection.md`
installs the same collector under a systemd timer on the EC2 host. Both legs can run: the
merge dedupes by `case_id`, and their failure modes are uncorrelated (GitHub
queue load vs host death). Install it inside the redeploy window rather than
opening port 22 twice.

**Manual run.**

    set -a; source .secrets/weatherapi.env; set +a
    PYTHONPATH=src python3 scripts/collect_provider_pairs.py --mode collect

The key lives in `.secrets/weatherapi.env` (mode 0600, gitignored) and is read
only from the environment. Do not pass it as an argument. It is safe on the Miner
host — `.env.example` has carried `WEATHERAPI_KEY` since the first spike and
`service.py` reads it there — but each scheduling leg is another copy to rotate.
The rule that does apply is narrower: keep the payment wallet local, and never
put its private key in the Miner container. This collection runs against the
operator's own provider accounts, and is not Telegraph traffic or hackathon demand.

In the systemd service wrapper, source the secret inside the job rather than
exporting it globally, and keep the job's log out of any shared location. The
script scrubs the key from its own error output, but a log that is world-readable
is still a mistake.

`--mode resolve --observations <path>` fills outcomes once windows close. It
needs an **independent** observation export; the bundled
`fixtures/observation_export.json` is a development fixture whose independence is
not asserted, so results resolved against it are not evidence of provider quality.

## Deployment gate

The Miner registration gate is complete. Transaction
`0x937d45d8108b905a551608707755e47899a41046436038a315a859d2f497b5d2`
confirmed on Base Sepolia, emitted sequential registration ID `78`, and
`getMiner(78)` returns the exact approved record. Telegraph's portal and
dispatcher both report `oathcast-weather` active under routing ID `64173`; do
not confuse that YAML routing ID with the on-chain registration ID. The
post-submit evidence is
`artifacts/registration-drafts/oathcast-weather-registration-confirmation-2026-08-13T1940Z.json`.

A paid request remains a separate consumption/demand milestone, not a Miner-
registration prerequisite. The
Application is not demo-complete until it has made a
paid request to at least one independent external Miner and its decision is
still functional when OathCast is disabled. Do not count local, discovery, or
fixture traffic as hackathon demand.
