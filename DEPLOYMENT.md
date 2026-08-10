# Deployment and payment boundary

The repository is ready to package as one public HTTPS OathCast Miner. The
hardened v3.2 release is running in Docker on AWS EC2 behind private host port
8080, with Caddy terminating HTTPS at `https://oathcastcourt.duckdns.org`.
Set the environment variables from `.env.example` and validate the canonical
YAML before registration. The exact deployed release, public smoke output, and
runtime details are archived under `artifacts/release-evidence/`.

## Timing

Do not spend the remaining Railway trial during preparation. The current
target is to deploy around Aug 16–17, 2026 so the trial overlaps the official
Aug 17–Sep 7 hackathon window. Railway's current trial terms are recorded in
the handoff; use another host only if a pre-launch public URL becomes necessary.

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

`payment-canary/` is the current live-compatible boundary. It uses the official
x402 fetch/SVM client packages and permits one unpaid preflight followed by at
most one explicitly enabled paid retry. Before loading a signer it validates
x402 v2, Solana Devnet, Circle's Devnet USDC mint, the 0.01-USDC cap, recipient,
fee payer, Miner ID, endpoint, and complete request URL. It never fabricates a
signature and never emits signing material or a raw settlement header.

The current Telegraph dispatcher is HTTP and its challenge removes the
`/miner-dispatcher` gateway prefix from the canonical resource. The canary
supports this only through `--allow-insecure-http-devnet`, pinned to
`http://13.237.89.59:7044`; redirects, other HTTP authorities, changed paths,
and changed queries fail closed. Remove this exception when Telegraph exposes
HTTPS.

After settlement, the canary queries Solana Devnet RPC and requires a confirmed,
error-free transaction with the expected signature, fee payer, mint, and exact
token movement. Telegraph Explorer reconciliation is still required before a
request is counted as local evidence. `src/oathcast/payment.py` remains a legacy
Base-Sepolia policy/journal regression harness and must not be used as the
current signer.

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

## Release provenance and smoke test

Create a source manifest before each deployment:

    PYTHONPATH=src python3 scripts/validate_miner_drafts.py
    PYTHONPATH=src python3 scripts/create_release_manifest.py \
      --release-id 2026-08-04-preflight \
      --output /tmp/oathcast-release-manifest.json

Build the image with the manifest's `source_sha256` and a unique release ID:

    docker build \
      --build-arg OATHCAST_RELEASE_ID=2026-08-04-preflight \
      --build-arg OATHCAST_SOURCE_SHA256=<manifest-source-sha256> \
      -t oathcast:2026-08-04-preflight .

After deployment, verify the exact release without printing secrets:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-04-preflight

The smoke test is non-destructive with respect to Telegraph and uses one
ordinary authenticated request against the OathCast service only; it does not
create paid demand. Record its JSON output with the release manifest.

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

The staging host is an Amazon Linux 2023 `t3.micro` in `eu-north-1`. The
`oathcast:staging` image runs as container `oathcast` with restart policy
`unless-stopped`; `/healthz` and authenticated forecast smoke tests have
passed. The Miner is bound to loopback port 8080 and the `oathcast-caddy`
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

## Release 2026-08-10 — renderer v2 and security batch (NOT YET DEPLOYED)

The repository is ahead of the running host. These changes are merged and covered
by 170 local tests but the live service still runs the earlier release. Redeploy
before treating any of it as live behaviour.

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
  replays of existing receipts always succeed. `/readyz` reports `receipt_store`
  capacity and returns 503 when full.
- **Provider bodies are capped at 2 MB** (`MAX_PROVIDER_BODY_BYTES`).
- **`get()` now raises on a rewritten receipt** whose bytes disagree with its
  recorded digest.

**The UID is load-bearing — read this before rebuilding.** The image is pinned to
UID/GID 1000:1000 to match the `ec2-user` owning the durable host directory
`/home/ec2-user/oathcast/data`. A bind mount preserves *host* ownership, so a
container running as any other UID cannot write receipts — while `/healthz` and
`/readyz` both still return 200. Every forecast then fails at persistence time and
nothing in the health surface says so. **Changing this UID requires chown-ing the
host directory in the same change.** Docker Desktop on macOS virtualizes ownership
for named volumes and will report a false success here; verify with real UID
separation, not a named volume.

**Steps**

    PYTHONPATH=src python3 -m unittest discover -s tests -t .   # expect 170 OK
    PYTHONPATH=src python3 scripts/validate_miner_drafts.py
    PYTHONPATH=src python3 scripts/create_release_manifest.py \
      --release-id 2026-08-10-hardened-v5 \
      --output /tmp/oathcast-release-manifest.json

    docker build \
      --build-arg OATHCAST_RELEASE_ID=2026-08-10-hardened-v5 \
      --build-arg OATHCAST_SOURCE_SHA256=<manifest-source-sha256> \
      -t oathcast:2026-08-10-hardened-v5 .

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

Run daily until Track 1 opens on 2026-08-17. Neither Open-Meteo nor WeatherAPI
sells a historical *forecast* archive, so every day not collected is evidence
that cannot be recovered later:

    set -a; source .secrets/weatherapi.env; set +a
    PYTHONPATH=src python3 scripts/collect_provider_pairs.py --mode collect

The key lives in `.secrets/weatherapi.env` (mode 0600, gitignored) and is read
only from the environment. Do not pass it as an argument and do not place it in
the Miner container — this collection runs on the operator's machine against the
operator's own provider accounts, and is not Telegraph traffic or hackathon demand.

Under `cron`, source the secret inside the job rather than exporting it globally,
and keep the job's log out of any shared location. The script scrubs the key from
its own error output, but a log that is world-readable is still a mistake.

`--mode resolve --observations <path>` fills outcomes once windows close. It
needs an **independent** observation export; the bundled
`fixtures/observation_export.json` is a development fixture whose independence is
not asserted, so results resolved against it are not evidence of provider quality.

## Deployment gate

The service is not a registered Miner until it has a public HTTPS URL, a
validated YAML, a live upstream path, the 0.01 USDC floor, and a successful
paid request. The Application is not demo-complete until it has also made a
paid request to at least one independent external Miner and its decision is
still functional when OathCast is disabled. Do not count local, discovery, or
fixture traffic as hackathon demand.
