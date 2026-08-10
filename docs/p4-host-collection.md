# P4 collection on the EC2 host

Runbook for scheduling `collect_provider_pairs.py` on the `oathcastcourt`
staging host. Written 2026-08-10.

## Status: this is the second leg, not the only one

`.github/workflows/collect-provider-pairs.yml` now requests an hourly run and
appends to the `data/provider-pairs` branch. That leg needed no AWS access and
is already live, so **collection is not blocked on this runbook.**

Install this one anyway, inside the redeploy's SSH window. GitHub delivers
scheduled runs best-effort — measured on this repository over 2026-08-06..10,
the `*/15` canary received 96 of 409 requested runs (23%), with gaps up to 360
minutes — so a host `cron` that actually fires on time is worth having. Both
legs can run: cases converge by `case_id` (floored to the hour), so two
collectors in the same hour produce one case rather than a duplicate, and their
failure modes are uncorrelated — GitHub queue load versus this host dying.

## Why the host rather than the laptop

The comparison needs cases accumulated at a fixed lead time. A laptop that
sleeps misses slots silently, and a missed slot cannot be backfilled — no free
provider tier sells a historical *forecast* archive. The EC2 host is already
always-on and already paid for.

## Scope correction

An earlier draft of this decision described putting `WEATHERAPI_KEY` on the
Miner host as crossing a documented boundary. **It does not.** `.env.example`
line 4 has carried `WEATHERAPI_KEY` since the first spike, and
`src/oathcast/service.py:244` reads it from the Miner's own environment — the
provider key was always intended to live there.

The rule that does exist is narrower and unchanged: `DEPLOYMENT.md` — "Keep the
payment wallet local; never put its private key in the Miner container." The
Solana signing key stays off this host. A weather provider key and a payment key
are different secrets with different blast radii; conflating them would have
blocked a safe action while teaching the wrong rule.

What a leaked WeatherAPI key actually costs: someone else spends the free quota,
or runs up a bill if the plan is ever upgraded. It grants no access to OathCast,
no access to receipts, and no ability to move funds. Rotate it from the
WeatherAPI dashboard; no OathCast redeploy is required.

## The blocker: SSH is closed

The security group exposes ports 80 and 443 only. The standing instruction is:
*do not reopen SSH except for a specific, time-bounded maintenance operation.*
This installation is such an operation, but it needs an explicit decision and
console access, so **steps 1 and 7 are operator actions and are not automated.**

**Do this inside the redeploy window.** The v5 redeploy also needs SSH — the host
builds its image from source and there is no registry to pull from — so opening
port 22 once covers both. Opening it a second time for collection alone is a
worse trade than waiting for that window.

## Steps

**1. Open SSH, scoped and time-bounded (operator).**
In the AWS console, add an inbound rule to the security group: TCP 22 from
**your current public IP as a /32**, never `0.0.0.0/0`. Note the time; this rule
comes back out in step 7.

**2. Copy the collection files to the host.**
The host runs a container image, not a source checkout, so the collector and its
dependencies need to be present as source. From the repository root:

    ssh -i <key> ec2-user@oathcastcourt.duckdns.org 'mkdir -p ~/oathcast/collection'
    rsync -av -e "ssh -i <key>" \
      --exclude '__pycache__' \
      src scripts fixtures \
      ec2-user@oathcastcourt.duckdns.org:~/oathcast/collection/

**3. Place the key in an owner-only env file.**
Follow the pattern already used for `/home/ec2-user/oathcast/.env`. On the host:

    umask 077
    printf 'WEATHERAPI_KEY=%s\n' '<paste key>' > ~/oathcast/collection/.env
    chmod 600 ~/oathcast/collection/.env

Paste the key interactively. Do not pass it on a command line that lands in
`~/.bash_history`, and do not echo the file back to check it — use
`wc -c < ~/oathcast/collection/.env`, which should be the key length plus 16.

**4. Verify one run before scheduling it.**

    cd ~/oathcast/collection
    set -a; . ./.env; set +a
    PYTHONPATH=src python3 scripts/collect_provider_pairs.py --mode collect --dry-run

Expect `"valid_provider_attempts": 2`. A `1` means the key did not load and only
Open-Meteo answered — fix that before scheduling, or the schedule will quietly
collect single-provider cases that prove nothing.

**5. Install the wrapper.**
Write `~/oathcast/collection/run-collect.sh`:

    #!/bin/bash
    set -euo pipefail
    cd /home/ec2-user/oathcast/collection
    set -a; . ./.env; set +a
    exec /usr/bin/python3 scripts/collect_provider_pairs.py \
      --mode collect \
      --dataset /home/ec2-user/oathcast/collection/paired-forecasts.json

Then `chmod 700 run-collect.sh`. Sourcing the env inside the wrapper keeps the
key out of the crontab and out of any other process's environment.

**6. Schedule it.**

    crontab -e
    7 */3 * * * /home/ec2-user/oathcast/collection/run-collect.sh >> /home/ec2-user/oathcast/collection/collect.log 2>&1

Every 3 hours at :07 — off the hour, because every scheduler on earth fires at
:00. That yields ~8 cases/day, ~56 by 2026-08-17, which is enough to split into
warmup and holdout. Daily collection would yield 7 total, too few to split.

`chmod 600 collect.log`. The script scrubs the key from its own error output —
including the URL-bearing connection failures urllib produces — but a
world-readable log is still a mistake.

**7. Close SSH again (operator).** Remove the port-22 rule. Confirm the security
group is back to 80/443 only, and record the open/close times in `handoff.md`.

## Retrieving the data

Two legs write to two places: this host writes a local file, and the Actions leg
writes `paired-forecasts.json` on the `data/provider-pairs` branch. They are not
automatically joined.

Pull the host's copy during a maintenance window:

    scp -i <key> \
      ec2-user@oathcastcourt.duckdns.org:~/oathcast/collection/paired-forecasts.json \
      /tmp/host-pairs.json

Then merge it into the branch copy. Merging is by `case_id`, so overlapping hours
collapse rather than double-count, and `--allow-lead-change` is not needed while
both legs run at the same lead:

    git fetch origin data/provider-pairs
    git worktree add --detach /tmp/pairs FETCH_HEAD
    PYTHONPATH=src python3 - <<'PY'
    import json, pathlib, sys
    sys.path.insert(0, "scripts")
    from collect_provider_pairs import merge_cases, write_dataset
    branch = pathlib.Path("/tmp/pairs/paired-forecasts.json")
    existing = json.loads(branch.read_text())
    host = json.loads(pathlib.Path("/tmp/host-pairs.json").read_text())
    merged, added = merge_cases(existing, host)
    write_dataset(branch, merged)
    print(f"added {len(added)} host-only cases")
    PY

`write_dataset` validates through the real backtest loader before replacing the
file, so a bad merge fails loudly instead of installing an unloadable dataset.

Because retrieval needs SSH, prefer pulling during a window you are opening
anyway. If it becomes routine, that is the signal to drop the host leg and let
the Actions leg — which needs no inbound port — carry collection alone.

## What this still does not do

Collection produces **unresolved** cases. Turning them into an equivalence
verdict needs an independent observation export — the bundled
`fixtures/observation_export.json` is a development fixture whose independence is
not asserted. Until that exists, the host will faithfully accumulate forecasts
that cannot yet be scored.
