# Repository and external-canary setup

The project contains a no-state canary at
`.github/workflows/oathcast-canary.yml`. It requests a run every 15 minutes
(GitHub scheduling is best-effort), checks the public staging Miner, verifies the
expected v18 release identity, rejects unauthenticated `/predict` calls, verifies
registered-path/canonical-path response and receipt parity for both the
registered one-hour forecast and additive 24-hour temperature response, and uses
the API key only through the repository secret `OATHCAST_MINER_API_KEY`.

The workflow is intentionally fail-closed. The repository and secret are
already configured; if the secret disappears, scheduled and manual runs fail
visibly rather than reporting a skipped success.

The current local workflow pins are release `2026-08-30-hourly-v18`, source SHA-256
`5aca88c6890443bc086e0c078d3390eead10461fa734206fcc4937758d5e8b6b`, and
image digest
`sha256:d3c29fa9f274d520635b6c3ca413c383ba1de958840ed1eb3105aedceda7e859`.
The workflow loads those values from the checked-in v18 runtime-evidence file
and verifies its linked manifest and public smoke before sending requests.
The workflow also passes `--require-temperature-window`; changing only the
identity pin would leave the compatibility contract unmonitored.

These v18 workflow and evidence updates are checked in locally. GitHub's remote
scheduled canary remains on its last pushed v11 configuration until this branch
is pushed and the workflow is observed running successfully. Do not describe
the remote schedule as v18-pinned before that happens.

Local verification passes: focused canary tests `44/44`, full Python discovery
`539/539`, and the v18 evidence identity loader accepts the retained bundle.

## Local repository

From the project directory:

```sh
git init -b main
git add .
git diff --cached --check
git commit -m "Prepare OathCast application evidence and canary"
```

Before the first push, inspect the staged file list and confirm that no `.env`,
SQLite database, private key, or API key is included:

```sh
git diff --cached --name-only
git status --short
```

## Hosted repository

The public repository is now available at
`https://github.com/fexx301/oathcast`, with the reviewed `main` branch pushed.
For a future clone or mirror, the equivalent remote setup is:

```sh
git remote add origin <repository-ssh-or-https-url>
git push -u origin main
```

Do not paste a token into a remote URL. If the GitHub CLI is authenticated, its
repository-creation flow is also acceptable; otherwise use the GitHub web UI.

## Canary secret

`OATHCAST_MINER_API_KEY` is configured as an Actions secret, not a repository
variable or YAML value. If it must be rotated, update the secret through GitHub
Settings → Secrets and variables → Actions; never commit or print the value.

After a rotation or workflow change, run the workflow manually once from the
Actions tab. Confirm that the job reports:

- `/healthz` = `200`;
- `/readyz` = `200`;
- `receipt_store_write` reports a successful transactional write and rollback
  after the v18 cutover;
- unauthenticated `/predict` = `401`;
- authenticated `/predict` = `200` with a non-empty answer, receipt, and request
  ID; and
- `/v1/forecast/point` returns the identical response and receipt;
- `forecast_hours=24&hourly=2t` returns the exact 24-hour RFC3339/Kelvin envelope
  on `/predict`; and
- the same temperature request returns the identical response and receipt on
  `/v1/forecast/point`.

This proves service availability only. Miner registration is established by
separate on-chain/portal evidence; the canary itself does not prove paid
Telegraph traffic, Explorer demand, or Track 3 qualification.

## Current scope

The public canary proves Miner availability only. It does not replace the
separate Miner-registration record and does not prove paid Telegraph traffic,
Explorer demand, or Track 3 qualification.

Provider collection and resolution have their own read-only monitor at
`.github/workflows/provider-evidence-freshness.yml`; see
`docs/provider-evidence-freshness.md`. Keeping it separate prevents a healthy
Miner from hiding stale evidence, or stale evidence from disguising a service
outage.
