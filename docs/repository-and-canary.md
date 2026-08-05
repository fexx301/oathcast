# Repository and external-canary setup

The project contains a no-state canary at
`.github/workflows/oathcast-canary.yml`. It checks the public staging Miner
every 15 minutes, verifies the expected v3.2 release identity, rejects
unauthenticated forecasting, and uses the API key only through the repository
secret `OATHCAST_MINER_API_KEY`.

The workflow is intentionally fail-closed. It skips cleanly until the project
is pushed to a hosted repository and that secret is configured; once the secret
exists, scheduled and manual runs perform the authenticated checks.

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

Add `OATHCAST_MINER_API_KEY` as an Actions secret, not as a repository variable
and not in YAML. Use the GitHub repository Settings → Secrets and variables →
Actions → New repository secret flow. The value must be the active staging
Bearer token, and it must never be committed or printed.

After the remote and secret exist, run the workflow manually once from the
Actions tab. Confirm that the job reports:

- `/healthz` = `200`;
- `/readyz` = `200`;
- unauthenticated forecast = `401`; and
- authenticated forecast = `200` with a receipt and request ID.

This proves service availability only. It is not Miner registration, paid
Telegraph traffic, Explorer demand, or Track 3 qualification.

## Current blocker

The repository is now public at `https://github.com/fexx301/oathcast` and the
reviewed `main` branch is pushed. The remaining canary step is to add the active
staging Bearer token as the `OATHCAST_MINER_API_KEY` Actions secret. Until then,
scheduled and manual canary runs skip the authenticated check without exposing
or inventing a credential.
