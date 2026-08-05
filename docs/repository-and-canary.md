# Repository and external-canary setup

The project contains a no-state canary at
`.github/workflows/oathcast-canary.yml`. It checks the public staging Miner
every 15 minutes, verifies the expected v3.2 release identity, rejects
unauthenticated forecasting, and uses the API key only through the repository
secret `OATHCAST_MINER_API_KEY`.

The workflow is intentionally fail-closed. It cannot become active until the
project is pushed to a hosted repository and that secret is configured.

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

Create an empty private or public repository using the final repository name,
then add its remote and push the reviewed commit:

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

The local project previously had no Git remote, and the available local GitHub
CLI session is not authenticated. Therefore the code and workflow can be
prepared locally, but remote creation, push, and secret configuration still
require the repository owner to authenticate and choose the final repository
name.
