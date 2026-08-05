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

## x402 boundary

payment.py implements the safe HTTP shape:

1. Query /integrations without payment.
2. Make a Miner request.
3. Decode Payment-Required from a 402 response.
4. Inject a signer-produced PAYMENT-SIGNATURE and retry.
5. Retain the settlement header.

It never fabricates a signature. A real signer or the official PayAI-compatible
SDK must be injected before paid requests are attempted. Signed clients require
an Application-side `SqlitePaymentJournal`; it records hashes, state, budgets,
and settlement evidence but never private keys or replayable proofs. The
current Base Sepolia USDC contract and dispatcher URL are documented in
.env.example and must be rechecked before live use.

Use `TelegraphX402Client.preflight_miner()` first. It performs the unpaid
request only. Before any signer is allowed to run, the client requires HTTPS,
an explicit approved Miner/endpoint set, an expected recipient, an amount cap,
and an unused one-shot budget. If the challenge supplies a resource URL, it
must match the exact request URL. The current live challenge omits that field,
so the target and endpoint allowlists are mandatory. A missing settlement
header or transport error is treated as an unknown outcome; the durable journal
blocks retries and further spending until it is reconciled.

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

Live payment is currently blocked: the documented dispatcher value is HTTP,
and no real signer, funded wallet, protocol-compatible SDK, or settlement
reconciliation proof has been established. Keep the signer disabled.

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

## Deployment gate

The service is not a registered Miner until it has a public HTTPS URL, a
validated YAML, a live upstream path, the 0.01 USDC floor, and a successful
paid request. The Application is not demo-complete until it has also made a
paid request to at least one independent external Miner and its decision is
still functional when OathCast is disabled. Do not count local, discovery, or
fixture traffic as hackathon demand.
