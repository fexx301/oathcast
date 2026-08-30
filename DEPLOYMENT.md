# Deployment and payment boundary

The repository packages one public HTTPS OathCast Miner. The
`2026-08-30-hourly-v18` release is running in Docker on AWS EC2
behind private host port 8080, with Caddy terminating HTTPS at
`https://oathcastcourt.duckdns.org`.
Set the environment variables from `.env.example`. Registration is complete and
the active portal-pinned YAML is frozen; the repository also retains the
historical canonical source and the separately recorded re-registration source
snapshot. Validate any new YAML before a separately authorized update or
re-registration. The exact deployed release, public smoke output, and runtime
details are archived under
`artifacts/release-evidence/`.

## Current deployment status

The deployed Miner is v18; stopped `oathcast-v17-rollback-20260830` is the
immediate Miner rollback target. Caddy configuration did not change for v18 and
remains pinned by its retained hash. The v17 and earlier sections below are
historical release records. The public decision UI is deployed separately. Its
safe public surface is a read-only release/status page plus a client-only
development fixture. It
has no Telegraph-backed runner, accepts no live Planning Desk intake, and
returns 503 for decision requests. Live decisions must not be enabled until the
authenticated, budgeted payment path has been reviewed and deployed.

V18 has persisted schema-4 receipts containing complete hourly weather fields
that v17 cannot replay. If a rollback becomes necessary, preserve the current
production database unchanged and do **not** restore the pre-v18 backup over it.
Starting v17 may restore old-contract availability while leaving v18 schema-4
events temporarily unreplayable; retain v18 and the current database until those
events can be served again.

UI-only replacements must override the Dockerfile health check, which targets
the Miner on port 8080. Run the UI on Docker bridge networking, publish only
`127.0.0.1:8787:8787`, bind the process to `0.0.0.0:8787` inside the container,
and probe `http://127.0.0.1:8787/health` internally. Caddy sends exact
`/predict`, `/healthz`, `/readyz`, and `/v1/*` requests to the Miner, so the
public decision fail-closed probe is `/api/decision`, not `/v1/decision`. The
disabled API returns 503 before reading a request body.

## Local run

Install the optional registration-tools extra before running the full test or
registration-draft validation commands; production runtime does not import
this tooling:

    python3 -m pip install -e '.[registration-tools]'

    OATHCAST_REQUIRE_AUTH=false PYTHONPATH=src python3 -m oathcast.service

Health: http://127.0.0.1:8080/healthz

Forecast endpoint:

    http://127.0.0.1:8080/predict?event_id=dev-1&location_name=Lagos&lat=6.5244&lon=3.3792&start=2026-08-17T15:00:00Z&end=2026-08-17T16:00:00Z

`/predict` is the registered dispatcher path. `/v1/forecast/point` remains the
canonical internal alias; both exact paths share authentication, rate limits,
semantic JSON output, and receipt identity.

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
      --release-id 2026-08-30-hourly-v18 \
      --output /tmp/oathcast-release-manifest.json

Build the image with the manifest's `source_sha256` and a unique release ID:

    docker build \
      --build-arg OATHCAST_RELEASE_ID=2026-08-30-hourly-v18 \
      --build-arg OATHCAST_SOURCE_SHA256=<manifest-source-sha256> \
      -t oathcast:2026-08-30-hourly-v18 .

Run the production container with
`OATHCAST_ENABLE_TEMPERATURE_WINDOW=true`. The Dockerfile and `.env.example`
remain fail-closed at `false`, so a missing deployment setting must not silently
claim the additive compatibility route is active.

When a reverse proxy is not on loopback, set `OATHCAST_TRUSTED_PROXIES` to only
the proxy's exact IP address or the narrowest required CIDR. A trusted proxy may
set `X-Forwarded-For`, which selects client rate-limit buckets; broad networks
let callers evade per-client limits. Leave the setting empty for direct traffic
or loopback Caddy, which is trusted by default.

After deployment, verify the exact release without printing secrets:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id 2026-08-30-hourly-v18 \
      --expected-source-sha256 5aca88c6890443bc086e0c078d3390eead10461fa734206fcc4937758d5e8b6b \
      --expected-image-digest sha256:d3c29fa9f274d520635b6c3ca413c383ba1de958840ed1eb3105aedceda7e859 \
      --require-receipt-write-probe \
      --require-temperature-window

The smoke test is non-destructive with respect to Telegraph and uses one
ordinary authenticated request against the OathCast service only; it does not
create paid demand. Record its JSON output with the release manifest.

For a cutover, freeze one old-release question and its safe fingerprints before
replacing the container, then replay that exact question after the new release
starts. The current v17-to-v18 comparison is retained at
`artifacts/release-evidence/2026-08-30-hourly-v18-replay.json`.
The earlier v6-to-v7 comparison remains historical evidence at
`artifacts/release-evidence/oathcast-2026-08-16-route-v7-replay.json`.

The general command shape is:

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id <old-release> \
      --question-output /tmp/oathcast-replay-question.json \
      > /tmp/oathcast-before.json

    PYTHONPATH=src python3 scripts/smoke_miner.py \
      --base-url https://oathcastcourt.duckdns.org \
      --expected-release-id <new-release> \
      --require-receipt-write-probe \
      --question-file /tmp/oathcast-replay-question.json \
      > /tmp/oathcast-after.json

    PYTHONPATH=src python3 scripts/compare_release_replay.py \
      --before /tmp/oathcast-before.json \
      --after /tmp/oathcast-after.json \
      --output /tmp/oathcast-release-replay.json

The comparator requires distinct release IDs and exact equality of the event
ID, receipt hash, and canonical public-response hash. Archive all three JSON
files as release evidence. If a source report is not retained, record that
limitation explicitly rather than claiming the comparison can be independently
rerun. The comparator never needs or records the Bearer token.

Before changing the live container, treat these as hard gates rather than
follow-up checks:

1. Transfer the complete candidate file set; reproduce the candidate manifest's
   source digest across its exact file set on the host and abort on any extra,
   missing, or mismatched file.
2. Back up the live database with SQLite's online backup mechanism, opening the
   source in read-only URI mode. Verify source and backup integrity plus
   row-count equality without printing receipt content. Preserve and hash the
   deployed Caddyfile separately.
3. Build a uniquely named image, record its image digest and required feature
   flags, then start a disposable loopback-only candidate with a copy of the
   receipt database. Require health, readiness, the transactional write probe,
   `/predict` authentication, canonical-path parity, every enabled compatibility
   contract and duration boundary, the expected receipt delta, restart replay,
   non-root UID, and durable-volume behavior to pass before cutover.
4. Capture the current release's replay question and safe fingerprints, preserve
   its stopped container as the immediate rollback target, and validate the
   production Caddyfile with the exact production Caddy image. Record explicitly
   whether proxy configuration changes.

After cutover, require the strict smoke against the new release identity, the
exact replay comparator, a second persistence/restart check against the real
host volume, and a manual canary pass. Roll back immediately on any identity,
replay, readiness, boundary, or persistence mismatch. A rollback must respect
receipt-schema compatibility: preserve the newest database and do not overwrite
it with an older backup merely to start the previous container. Restore Caddy
only if its retained hash or routing changed. Update canary identity/evidence
pins only after the real image digest and sanitized release bundle exist.

### v18 candidate gates and credential handling

The v18 release adds complete hourly weather fields to the multi-hour response.
Its candidate checks are stricter than `scripts/smoke_miner.py`, which only
asserts the legacy point contract and the additive temperature compatibility
route. Capture the v17 replay question and fingerprints **before** taking the
online database backup so that the exact receipt is present in the candidate
copy. Then run the following gates against a disposable database copy:

1. Confirm the release directory and container/image names do not already exist;
   reproduce the exact manifest file set and source digest, and record the
   resolved base-image ID, image ID, labels, disk bytes, and free inodes.
2. Load only the runtime allow-list from the owner-only host env file:
   `OATHCAST_MINER_API_KEY`, `OATHCAST_MINER_API_KEYS`, `OATHCAST_REQUIRE_AUTH`,
   `OATHCAST_ENABLE_TEMPERATURE_WINDOW`, `OATHCAST_TRUSTED_PROXIES`, and the
   receipt-cap settings. Reject wallet, signer, private-key, and unrelated
   credential variables. Explicitly override `OATHCAST_RELEASE_ID`,
   `OATHCAST_SOURCE_SHA256`, and `OATHCAST_IMAGE_DIGEST` for the image under
   test. Verify configured credentials only by non-secret fingerprints and
   status codes; never print the values or raw `docker inspect` environment.
3. Exercise unauthenticated `401`, the legacy one-hour response, exact replay
   of schema-1, schema-2, and schema-3 receipts, and the unchanged
   `forecast_hours=24&hourly=2t` response. For the new window contract, require
   valid structured hourly output at 2, 24, and 168 hours, reject 169 hours,
   check contiguous timestamps and units, and verify the public response stays
   below the 64-KiB cap. Persist a schema-4 receipt, restart the candidate,
   and replay it with no provider call; verify the row count delta and SQLite
   integrity before and after restart.
4. After v18 has written a schema-4 receipt, start the pinned v17 image against
   a separate copy of that candidate database. Require v17 startup and
   schema-1/2/3 replay to work, and record that a schema-4 event is refused
   closed rather than silently reinterpreted. Do not use that rehearsal copy
   for production and never restore an older backup over the live database.
5. Preserve the stopped v17 container as the immediate rollback target, keep
   Caddy untouched after checking its exact image and Caddyfile hash, and run
   the same strict identity, replay, and restart checks after cutover. A
   rollback restores process availability with the newest database; it does
   not promise v17 can serve a v18 schema-4 event.

The previously printed secondary bearer is treated as compromised. Because it
is likely Telegraph's persistent registration credential, do not revoke it or
replace it unilaterally during this deployment. After v18 stabilizes, coordinate
an overlap rotation with Telegraph, verify both credentials, retire the exposed
one, and prove that the retired credential receives `401`. If the private
transcript or any captured output is accessible to an untrusted party, stop the
deployment and rotate first. AWS root CLI access is also an operational risk;
do not make additional AWS mutations with it unless unavoidable, and schedule
root-key retirement separately.

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
`oathcast:2026-08-30-hourly-v18` image runs as container `oathcast` with restart
policy `unless-stopped`; stopped container `oathcast-v17-rollback-20260830`
preserves the immediate previous release. `/healthz`, `/readyz`, authenticated
`/predict`, canonical-path parity, the additive 24-hour temperature contract and
parity, transactional write, and restart/replay smoke tests have passed. The
Miner is bound to loopback port 8080 and the `oathcast-caddy`
container uses host networking to terminate HTTPS and redirect HTTP to it. The
temporary widened SSH rule used for evidence capture has been removed; the
security group retains its restricted administrative SSH rule. Host port 8080
remains loopback-only, and public HTTPS was rechecked after the rule cleanup.
DuckDNS currently maps
`oathcastcourt.duckdns.org` to `13.49.229.253`. An EC2-side DuckDNS updater is
now installed and runs from a root-only token file every five minutes because
the public IPv4 is ephemeral.

## Release 2026-08-30 - complete hourly weather window release (CURRENT)

Release v18 supersedes v17 as the live runtime. It retains v17's registered
one-hour precipitation behavior, historical receipt replay, and additive
`forecast_hours=1..24&hourly=2t` temperature contract while replacing the
multi-hour summary-only response with a complete hourly weather envelope. Each
2-to-168-hour response now carries temperature, precipitation amount,
precipitation probability, and 10-metre wind speed for every contiguous UTC
hour. New multi-hour receipts use schema 4; legacy schema 1, 2, and 3 receipts
remain replayable byte-for-byte. The registered YAML, Telegraph registration,
WASM scorer, wallet, and Caddy configuration were unchanged.

- Release ID: `2026-08-30-hourly-v18`
- Source SHA-256:
  `5aca88c6890443bc086e0c078d3390eead10461fa734206fcc4937758d5e8b6b`
- Runtime image ID:
  `sha256:d3c29fa9f274d520635b6c3ca413c383ba1de958840ed1eb3105aedceda7e859`
- Resolved base reference:
  `docker.io/library/python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217`
- The exact deploy manifest is 11,860 bytes with file SHA-256
  `c8a4e0b3920c9df60569e4d19539ebcf4d0356aaf01172680dd7cd4111859464`.
  The retained local 800,768-byte source archive has SHA-256
  `fb6bb7314e2b9953a6274548b1ab6a7f596971fbfb446892cbf4378e2d9ef082`;
  the archive itself is not checked into the repository.
- Caddy configuration was unchanged; retained Caddy SHA-256:
  `29495257def97638a8d14e92d593320f3124d1effff1f7e8a62b58864d4406a1`.
- Candidate standard smoke passed 12/12. Both configured credentials returned
  HTTP 200 in status-only checks, schema 1/2/3 receipts replayed exactly, and
  the candidate matrix passed the legacy one-hour route, 2/24/168-hour
  structured responses, 169-hour rejection, and the legacy temperature route.
- A schema-4 168-hour receipt survived candidate restart with 304 rows and
  SQLite integrity `ok`; response, receipt, and wire fingerprints were exact
  before and after restart. The pinned v17 image replayed schema 1/2/3 against
  a separate copy and refused the schema-4 event with HTTP 409, fail-closed.
- Production public and post-restart smokes passed 12/12. A v17-to-v18 replay
  preserved the event, receipt, and public response; an exact public wire
  replay remained 172 bytes and identical across the v18 restart. Production
  2/24/168-hour canary requests passed, 169 hours returned HTTP 400, and the
  legacy 24-hour temperature envelope passed.
- The online pre-cutover backup has 299 source and 299 backup rows, SQLite
  integrity `ok`, and SHA-256
  `22c95bbbf9602bd586294a3ddde18c2ce8c83436d162172dfcf87b89fa33d279`.
  The live store had 305 rows with integrity `ok` after canary traffic.
- Stopped `oathcast-v17-rollback-20260830` is the immediate rollback target.
  The disposable v18 candidate and v17 rehearsal containers were stopped after
  evidence capture and retained in the release directory.
- Release evidence is retained under
  `artifacts/release-evidence/2026-08-30-hourly-v18-*`, including the manifest,
  public/post-restart smokes, canonical and exact-wire replay records, hourly
  matrices, schema replay, rollback rehearsal, credential fingerprints,
  receipt-backup metadata, and runtime evidence.
- On 2026-08-30, host-side bearer overlap added a new 256-bit key to the
  secondary allow-list while preserving both existing credentials. The v18
  container was recreated with the pre-rotation redacted configuration hash;
  health/readiness remained `200`, unauthenticated `/predict` remained `401`,
  and all three credentials reached authenticated validation through both
  localhost and public HTTPS. Fingerprint-only evidence is retained at
  `artifacts/release-evidence/2026-08-30-hourly-v18-credential-overlap.json`.
  The previous credentials remain accepted temporarily because Telegraph's
  external credential cutover is not yet verified. The local GitHub CLI session
  is invalid, so the Actions secret was not changed and no retirement is
  claimed.

The checked-in scheduled canary is now pinned to the v18 runtime-evidence file.
This local commit does not push the accumulated branch; GitHub's remote schedule
must not be described as v18-pinned until a separately authorized push is made
and the workflow is observed running successfully.

## Release 2026-08-23 - seven-day timestamp-normalizing window release (HISTORICAL; SUPERSEDED BY V18)

Release v17 supersedes v16 as the live runtime. It keeps v16's dispatcher
timestamp normalization and extends the `start`/`end` window implementation to
1 through 168 hours. Normalization still applies only to multi-hour requests;
the registered one-hour point behavior is unchanged. The separate additive
`forecast_hours=1..24&hourly=2t` temperature compatibility contract remains
unchanged. At v17 deployment time the registered YAML, Caddy configuration, and
Miner registration were unchanged. The later 2026-08-27 YAML re-registration
updated the portal-pinned registration representation; the v17 runtime, Caddy
configuration, and image were not rebuilt.

- Release ID: `2026-08-23-window-v17`
- Source SHA-256:
  `9d939f53931b4895d8abf3eb6c0ae2a1f12c6e282980f8c862ae86c7806b628f`
- Runtime image ID:
  `sha256:3cc91107208ffa806b025d79297e64b695329255f6329714e32464a7a7eaae8c`
- The exact deploy manifest is 11,307 bytes with file SHA-256
  `48bfeeb9bd21b9b89eae7561b8851f04a4a7d0c2db92576989a2d7701eb74e5d`.
  The retained local 744,448-byte source archive has SHA-256
  `9338335a0d135197587acd1c8370ab5f28c428054283b6eb91cb385ec9af0efa`;
  the archive itself is not checked into the repository.
- Caddy configuration was unchanged; retained Caddy SHA-256:
  `29495257def97638a8d14e92d593320f3124d1effff1f7e8a62b58864d4406a1`.
- Candidate release smoke passed 12/12, including both production credentials.
  Schema 1, 2, and 3 receipts replayed exactly. A candidate 168-hour receipt
  survived restart with 168 contiguous hours and SQLite integrity `ok`; only
  its post-restart state was retained, so the repository does not claim an
  independently reproducible pre/post hash comparison. A 169-hour request
  correctly returned HTTP 400.
- Production public smoke passed 12/12, including post-restart smoke. The
  v16-to-v17 replay preserved event ID, receipt SHA-256, and public-response
  SHA-256. A dispatcher-shaped 168-hour request returned HTTP 200, persisted
  168 contiguous hours, and left SQLite integrity `ok`.
- Local full Python discovery passed `526/526`; focused canary tests passed
  `44/44`; and the v17 evidence identity loader accepted the retained bundle.
- Stopped `oathcast-v16-rollback-20260823` is the immediate rollback target.
  The disposable candidate `oathcast-v17-candidate-20260823` was stopped after
  evidence capture. Caddy and the registered YAML were unchanged.
- The definitive pre-v17 receipt backup has 188 source and 188 backup rows,
  SQLite integrity `ok`, and SHA-256
  `69e87081f142abd805ab61b7d960f6bf719db2d816cd720d71e673b376d04b77`.
- Release evidence is retained under:
  `artifacts/release-evidence/oathcast-2026-08-23-window-v17-manifest.json`,
  `oathcast-2026-08-23-window-v17-public-smoke.json`,
  `oathcast-2026-08-23-window-v17-replay.json`,
  `oathcast-2026-08-23-window-v17-postrestart-smoke.json`,
  `oathcast-2026-08-23-window-v17-restart-replay.json`,
  `oathcast-2026-08-23-window-v17-168-hour-smoke.json`,
  `oathcast-2026-08-23-window-v17-receipt-backup.json`, and
  `oathcast-2026-08-23-window-v17-runtime-evidence.json`.

The scheduled canary must resolve identity from the v17 runtime-evidence file
and verify its linked manifest and public smoke before relying on scheduled
runs. This release involved no YAML replacement, upload, signing, wallet
action, registration, commit, or push.

## Release 2026-08-19 - timestamp-normalizing window release (HISTORICAL; SUPERSEDED BY V17)

Release v16 makes the registered multi-hour route robust to dispatcher timestamp
choice without changing the registered YAML. It accepts timezone-aware
ISO/RFC3339 bounds, rounds the start to the nearest whole UTC hour using
deterministic half-up rounding, and preserves the original integral 1-to-24-hour
duration. Omitted cutoffs receive an auditable first-hour implicit grace window;
explicit cutoffs remain exact.

- Release ID: `2026-08-19-window-v16`
- Source manifest digest:
  `a1902dce6ff550a5aa2a28899ce5a01e7cd483d7e6484bde5327a0a2e743f2e1`
- Runtime image ID:
  `sha256:7ac7f6f81cac9e66e33187e140ae21f76d6e7ab4b3e6fc6c9d6944312aaedc28`
- The exact deploy manifest is 11,307 bytes; its file SHA-256 is
  `ee13deff7b84825b2367428ff4f6fc86d06002900dd14eb15259a34c2eec0dc8`.
  The 175,243-byte clean source bundle has SHA-256
  `845b1715ecd47d4805c1bbbe78b154ba70b715c028fb29f5bb1104e2956cdfb3`.
- Final public and disposable smokes passed 12/12. V12-to-v16 replay and
  post-restart replay preserved event, receipt, and public-response identity;
  six accepted boundary offsets passed. The retained boundary artifact records
  those six successes only and is not presented as a complete rejection matrix.
- Stopped `oathcast-v12-rollback-20260819` is the immediate rollback target.
  The disposable v16 container was removed; Caddy and the registered YAML were
  unchanged.
- Evidence:
  `artifacts/release-evidence/oathcast-2026-08-19-window-v16-manifest.json`,
  `oathcast-2026-08-19-window-v16-public-smoke.json`,
  `oathcast-2026-08-19-window-v16-replay.json`, and
  `oathcast-2026-08-19-window-v16-runtime-evidence.json`.

The scheduled canary is pinned to the v16 runtime evidence and uses the same
evidence loader in its repository integrity test. This repin changed no live
runtime, YAML, Miner registration, signer, or wallet state.

## Release 2026-08-19 - window release rebuilt from a clean bundle (HISTORICAL; SUPERSEDED BY V16)

Release v11 is v10's routing change rebuilt so its evidence is reproducible. The
v10 image was built from the host working directory, which carries data, backups
and caddy state beyond the manifest's include set, so the host could not
recompute the manifest digest and `release_identity_from_evidence` had nothing
honest to put in `host_recomputed_source_sha256`. Since `Caddyfile` is in the
manifest include set, the edge change also moved the repository digest after the
v10 manifest was written.

- Release ID: `2026-08-19-window-v11`
- Source manifest digest:
  `03dc6f1dd0d831eb16efb6f2a823a2a1b1bc2fd1cf7372f3422496eeb3fe9659`
- Runtime image ID:
  `sha256:7f91d064a798be592681326ff812d286f718c120ea665fd5b7145b4d2ae03c39`
- Built from `/home/ec2-user/oathcast/source-clean`, a 68-file bundle holding
  exactly the manifest include set. The host recomputed the manifest digest from
  that bundle and it matched the repository value **before** the build, so the
  deployed image corresponds to a tree that reproduces its own digest.
- Image labels carry the identity: `org.opencontainers.image.version` is the
  release ID and `org.opencontainers.image.revision` is the source digest, which
  is what the canary's evidence contract cross-checks.
- All twelve strict public smoke checks passed, and the canary passes 12/12 when
  run exactly as CI invokes it.
- The frozen v8-era question `canary-lagos-20260819T1200z` was replayed by event
  id against v11 and returned the same `receipt_sha256` `4cd26d60...` and the
  same public-response digest. Receipt store integrity `ok`.
- Stopped `oathcast-v10-rollback-20260819` is the immediate rollback target.
- Build from the clean bundle, never from `~/oathcast` directly. The working
  directory holds runtime state that is not in the manifest, so a build from it
  produces an image whose provenance cannot be recomputed on the host.
- Evidence:
  `artifacts/release-evidence/oathcast-2026-08-19-window-v11-manifest.json`,
  `oathcast-2026-08-19-window-v11-public-smoke.json`,
  `oathcast-2026-08-19-window-v11-replay.json`, and
  `oathcast-2026-08-19-window-v11-runtime-evidence.json`.

The canary was pinned to this release's runtime evidence. A later release that
does not repin it leaves the canary asserting a stale identity and failing every
15 minutes, which happened between the v10 and v11 cutovers and again until the
v16 evidence bundle was retained.

## Release 2026-08-18 - 1-to-24-hour window, first cut (HISTORICAL; SUPERSEDED BY V11)

Release v10 answers Telegraph's 24-hour `WEATHER_FORECAST` requests. The handler
previously called `question_from_query` directly, which enforces a one-hour
  span,
so a multi-hour `start`/`end` request was refused with 400 "only accepts one-
  hour
windows": no temperature was returned and the Miner scored `0`.

- Release ID: `2026-08-18-window-v10`
- Source manifest digest:
  `6e7bc21954b6951e10bd78249143639fb151895faf4c0cd114b0ecfeb7b88795`
- Runtime image ID:
  `sha256:757fb40dc01b99420fb1753789a530ed589bdf06f5a2b0dbb17eecfef498fe13`
- Commit `6d2ad28`; runtime flag `OATHCAST_ENABLE_TEMPERATURE_WINDOW=true`.
- The protected registered YAML remains exactly 4,960 bytes with SHA-256
  `9ad11f06fda61960d621b7160e2f27a84daafa21683a24f6a3278427bb56ee0e`.
- All twelve strict public smoke checks passed: health, release ID, source
  digest, image digest, readiness, receipt capacity, transactional write
  rollback, unauthenticated `/predict` rejection, authenticated registered and
  canonical forecast parity, and the temperature response and parity.
- Verified live over public HTTPS: a 24-hour `start`/`end` span returns `200`
  carrying `minimum_hourly_temperature_c`, `maximum_hourly_temperature_c` and
  `probability`; 6-hour and 12-hour spans likewise; the registered one-hour
  contract still returns `content` and `probability` only; and the `hourly=2t`
  path is unchanged.
- The frozen v8 question replayed byte-identically through v10
  (`compare_release_replay.py` reported `ok: true`). The live receipt database
  returned `PRAGMA integrity_check` `ok`.
- Stopped `oathcast-v8-rollback-20260818` is the immediate rollback target.
  Revert with `docker rm -f oathcast`, then `docker start` and `docker rename`
  that container back to `oathcast`.
- Caddy was reconfigured after the Miner cutover to add access logging and the
  security headers that were present in the repository but had never been
  deployed. It now sends `Strict-Transport-Security`, `X-Content-Type-Options`
  and `Referrer-Policy`, caps request bodies at 64KB on both handles, and logs
  one JSON line per request to stdout. The Caddyfile digest is now
  `29495257def97638a8d14e92d593320f3124d1effff1f7e8a62b58864d4406a1`;
  `Caddyfile.pre-access-log-20260818` and stopped container
  `oathcast-caddy-pre-access-log-20260818` are the rollback pair.
- The access log deliberately drops `request>headers`, which would otherwise
  write the Miner's `Authorization` bearer key to disk, and strips the query
  string from the URI, which carries coordinates, location names and event ids.
  Caddy therefore records the caller and the status while the Miner's own
  `forecast_request_refused` record reports the request shape; neither log holds
  the caller's subject. Verified with a sentinel request: the token, the
  coordinates and the location name are all absent from the log.
- **Never `mv` over the bind-mounted Caddyfile.** It is mounted as a file, so the
  mount resolves to an inode at container creation and `mv` orphans it: the host
  gets the new config while the container keeps serving the old one, and both
  `caddy validate` and `caddy reload` report success because they are reading
  the host file and adapting an unchanged mount. Use `cat new > Caddyfile`, which
  truncates in place and preserves the inode, then confirm from inside the
  container with `docker exec oathcast-caddy grep ... /etc/caddy/Caddyfile`. If
  the mount is already orphaned, only recreating the container recovers it. See
  `docs/engineering-log.md`.
- Evidence:
  `artifacts/release-evidence/oathcast-2026-08-18-window-v10-manifest.json`,
  `oathcast-2026-08-18-window-v10-public-smoke.json`, and
  `oathcast-2026-08-18-window-v10-replay.json`.

Two limits are recorded rather than claimed away. In a multi-hour response
`probability` is the maximum one-hour precipitation probability inside the span,
reported with an explicit `probability_semantics` field, whereas the registered
YAML describes a one-hour event probability; the multi-hour branch is therefore
structurally compliant but semantically undeclared, and is provisional until the
window semantics are declared and re-registered. A window whose first hour has
already begun is still refused with 410, and `forecast_hours` shapes other than
exactly `hourly=2t` are still refused with 400 and now logged.

An intermediate `2026-08-18-window-v9` was built and started, then replaced
within minutes: it also relaxed the one-hour point contract's implicit cutoff,
which is hashed into the derived `event_id`, so an unchanged request stopped
replaying its stored receipt and an explicit `event_id` raised `ReceiptConflict`
as HTTP 409. V10 restricts the relaxed cutoff to window requests.

## Release 2026-08-17 - additive temperature compatibility (HISTORICAL; SUPERSEDED BY V10)

Release v8 enables the additive temperature request shape on the existing
public Miner while leaving the protected registered one-hour precipitation YAML
unchanged.

- Release ID: `2026-08-17-temperature-v8`
- Source manifest digest:
  `edeeaacf470b2207f6bbd8439e0720eff0459d9ca5fe214bc3a09d48ae0c639c`
- Runtime image ID:
  `sha256:ae1fff9db3317cd0f6a9d23772df62d93195bd814359e9a3c8d9b21aa0850672`
- Runtime flag: `OATHCAST_ENABLE_TEMPERATURE_WINDOW=true`
- The protected registered YAML remains exactly 4,960 bytes with SHA-256
  `9ad11f06fda61960d621b7160e2f27a84daafa21683a24f6a3278427bb56ee0e`.
- Strict public smoke passed all nine checks: health, readiness, receipt
  capacity/write rollback, unauthenticated `/predict` rejection, authenticated
  registered/canonical forecast parity, and 24-hour temperature response/parity.
- The v7 precipitation receipt and public-response fingerprints replayed
  byte-identically through v8. Forecast and temperature fingerprints also
  survived the live v8 restart; the database remained at 19 rows and
  `PRAGMA integrity_check` returned `ok`.
- Stopped `oathcast-v7-rollback-20260817` is the immediate rollback target.
  Caddy configuration was unchanged and retains SHA-256
  `5273d4429b6a0aa58d374a49c55934e7b3e3931d17dbb9e5590a7850f1b5c970`.
- The temporary SSH maintenance rule was removed. The security group exposes
  only ports 80 and 443; public HTTPS health/readiness returned 200, while
  external probes to ports 22 and 8080 timed out.
- Evidence:
  `artifacts/release-evidence/oathcast-2026-08-17-temperature-v8-manifest.json`,
  `oathcast-2026-08-17-temperature-v8-public-smoke.json`,
  `oathcast-2026-08-17-temperature-v8-replay.json`, and
  `oathcast-2026-08-17-temperature-v8-runtime-evidence.json`.

This is an additive runtime deployment, not a replacement, upload, or
re-registration of `miners/oathcast-weather.yaml`. It proves the public service
can answer the diagnosed 24-hour request shape; it does not establish a
corrected official Telegraph score or retroactively alter epoch `202`.

## Release 2026-08-16 - registered route fix (HISTORICAL; SUPERSEDED BY V8)

Telegraph confirmed the observed Track 1 zero came from its scorer calling the
registered `GET /predict` path and receiving 404, so the extracted Miner answer
was empty. Release v7 routes exact `/predict` through Caddy and accepts it in the
Miner through the same authenticated handler as `/v1/forecast/point`.

- Release ID: `2026-08-16-route-v7`
- Source manifest digest:
  `3789e1ce6903c227e96869f29e570a822b93c59fe860243d840a3f43b6498557`
- Runtime image ID:
  `sha256:8bee7d497c0182b8899ad6ebbe162e89296d732311801edeaa26885665db4fac`
- Caddyfile SHA-256:
  `5273d4429b6a0aa58d374a49c55934e7b3e3931d17dbb9e5590a7850f1b5c970`
- Public `/predict` returned `401` without auth and a non-empty `200` with auth.
- `/predict` and `/v1/forecast/point` returned the same response hash and receipt
  hash through public HTTPS.
- A v6 receipt replayed byte-identically through v7 before and after restart;
  the live row count stayed 17 and `PRAGMA integrity_check` returned `ok`.
- The live container runs as UID/GID `1000:1000`; runtime logs passed the
  credential/query sanitization checks.
- Stopped `oathcast-v6-rollback-20260816` plus the protected old Caddyfile form
  the paired rollback target.
- The temporary SSH rule was removed after maintenance; external checks confirm
  port 22 is closed, port 443 remains healthy, and port 8080 remains private.
- Evidence:
  `artifacts/release-evidence/oathcast-2026-08-16-route-v7-manifest.json`,
  `oathcast-2026-08-16-route-v7-public-smoke.json`,
  `oathcast-2026-08-16-route-v7-replay.json`, and
  `oathcast-2026-08-16-route-v7-runtime-evidence.json`.

This deployment did not retroactively change the old leaderboard epoch. At this
v7 checkpoint, a later epoch-202 observation still scored OathCast `0`, rank
`6/6`, because the live one-hour Miner did not satisfy the scorer's 24-hour
temperature request. The compatibility implementation was still undeployed at
that checkpoint; the current v8 section above supersedes that runtime state.

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

The validation commands below require the `registration-tools` extra described
in Local run.

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

The Miner registration gate is complete. The 2026-08-27 transaction
`0x43748dcaac584be466d32d96a45f4293816295579ffa17ee1c20ec4aa288184c`
confirmed on Base Sepolia with receipt status `1`, emitted current registration
ID `245`, and `getMiner(245)` returns the approved record. Telegraph's portal
and dispatcher report `oathcast-weather` active under routing ID `64173`; do
not confuse that YAML routing ID with the on-chain registration ID. The former
registration ID `78` is deregistered. The sanitized current post-submit
evidence is
`artifacts/registration-drafts/oathcast-weather-cutoff-v2-registration-postflight-2026-08-27.json`.

A paid request remains a separate consumption/demand milestone, not a Miner-
registration prerequisite. The
Application is not demo-complete until it has made a
paid request to at least one independent external Miner and its decision is
still functional when OathCast is disabled. Do not count local, discovery, or
fixture traffic as hackathon demand.
