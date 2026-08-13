# Repository and external-canary setup

The project contains a no-state canary at
`.github/workflows/oathcast-canary.yml`. It checks the public staging Miner
every 15 minutes, verifies the expected v6 release identity, rejects
unauthenticated forecasting, and uses the API key only through the repository
secret `OATHCAST_MINER_API_KEY`.

The workflow is intentionally fail-closed. The repository and secret are
already configured; if the secret disappears, scheduled and manual runs fail
visibly rather than reporting a skipped success.

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
  after the v6 cutover;
- unauthenticated forecast = `401`; and
- authenticated forecast = `200` with a receipt and request ID.

This proves service availability only. It is not Miner registration, paid
Telegraph traffic, Explorer demand, or Track 3 qualification.

## Current scope

The public canary proves Miner availability only. It does not prove Miner
registration, paid Telegraph traffic, Explorer demand, or Track 3 qualification.

Provider collection and resolution have their own read-only monitor at
`.github/workflows/provider-evidence-freshness.yml`; see
`docs/provider-evidence-freshness.md`. Keeping it separate prevents a healthy
Miner from hiding stale evidence, or stale evidence from disguising a service
outage.
