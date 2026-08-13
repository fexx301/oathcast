# P4 collection on the EC2 host

Runbook for scheduling `collect_provider_pairs.py` on the `oathcastcourt`
staging host. Written 2026-08-10.

## Installed 2026-08-10 — this runbook has been executed

Both legs now run. The host leg was installed during the v5 redeploy window and
is confirmed collecting on its **scheduled** path, not merely by hand:

    oathcast-collect.timer   enabled, active   next fire 18:07 UTC
    manual fire              Result=success  ExecMainStatus=0
    valid_provider_attempts  2               (both providers answered)
    cases_added              0               (same-hour dedupe, as designed)
    timer-driven run 18:07Z  produced lagos-20260810T1800Z

The dataset held 2 cases at 18:09 UTC — `lagos-20260810T1700Z` and
`lagos-20260810T1800Z`, both with `open_meteo` **and** `weatherapi`. The second
was written by the timer rather than by a manual invocation, which is the part
that actually proves the schedule works.

Steps 1-6 below are the record of what was done; step 7 (closing SSH) is the
operator action that ends the window. Deviations found while executing are
recorded in the steps themselves — notably **step 6, where `cron` does not exist
on this host.**

## Status: this is the second leg, not the only one

`.github/workflows/collect-provider-pairs.yml` requests an hourly run and appends
to the `data/provider-pairs` branch. That leg needed no AWS access, its
`WEATHERAPI_KEY` secret is set, and it is **confirmed collecting** — verified by
step conclusion `success` (not `skipped`), a commit on the data branch, and a case
count that actually increased. So **collection is not blocked on this runbook.**

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
In the AWS console, **switch the region to `eu-north-1` (Stockholm) first** — security
groups, instances, and key pairs are regional objects, so `oathcast-web` does not
appear at all from any other region, and an empty list reads exactly like a deleted
group. Then add an inbound rule to that security group: TCP 22 from **your current
public IP as a /32**, never `0.0.0.0/0`. Re-check the public IP immediately before
writing the rule; it changes. Note the time; this rule comes back out in step 7.

There is no CLI fallback — no `aws` binary, no `~/.aws`, no environment credentials —
so the console session is the only path. Start with a stable session and roughly 30
uninterrupted minutes: a half-open security group and an expired console session is
the worst possible combination.

**2. Copy the collection files to the host.**
The host runs a container image, not a source checkout, so the collector and its
dependencies need to be present as source. From the repository root:

    ssh -i <key> ec2-user@oathcastcourt.duckdns.org 'mkdir -p ~/oathcast/collection'
    tar --exclude='__pycache__' -cf - src scripts fixtures \
      | ssh -i <key> ec2-user@oathcastcourt.duckdns.org \
          'tar -xf - -C ~/oathcast/collection'

Use tar-over-SSH on this macOS workstation. Its installed rsync crashed during
the verified v5 transfer, and a piped `tail` then obscured rsync's real exit
status; the tar path is the proven transfer method. Verify the copied files on
the host before enabling the timer.

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

**Amazon Linux 2023 has no `cron`.** An earlier draft of this step said
`crontab -e`; on this host that returns `crontab: command not found` — `cronie`
is not installed and `crond` is inactive. Installing it would add a package and
a daemon to do what the already-running init system does natively, so use a
systemd timer instead. Two unit files, both installed and confirmed firing on
2026-08-10:

`/etc/systemd/system/oathcast-collect.service`

    [Unit]
    Description=OathCast provider-pair collection

    [Service]
    Type=oneshot
    User=ec2-user
    Group=ec2-user
    ExecStart=/home/ec2-user/oathcast/collection/run-collect.sh
    StandardOutput=append:/home/ec2-user/oathcast/collection/collect.log
    StandardError=append:/home/ec2-user/oathcast/collection/collect.log
    NoNewPrivileges=true
    PrivateTmp=true

`/etc/systemd/system/oathcast-collect.timer`

    [Unit]
    Description=Run OathCast provider-pair collection every 3 hours

    [Timer]
    OnCalendar=*-*-* 00/3:07:00
    Persistent=true
    AccuracySec=1min

    [Install]
    WantedBy=timers.target

Then:

    sudo systemctl daemon-reload
    sudo systemctl enable --now oathcast-collect.timer
    systemctl list-timers oathcast-collect.timer --no-pager

Every 3 hours at :07 — off the hour, because every scheduler on earth fires at
:00. That yields ~8 cases/day, ~56 by 2026-08-17, which is enough to split into
warmup and holdout. Daily collection would yield 7 total, too few to split.

`Persistent=true` is the reason this beats cron on a host that can be stopped: a
run missed while the instance was down fires once on the next boot. Cron simply
loses it, and a missed collection hour is **permanently unrecoverable** — no free
tier sells a historical *forecast* archive.

`chmod 600 collect.log`. The script scrubs the key from its own error output —
including the URL-bearing connection failures urllib produces — but a
world-readable log is still a mistake.

**7. Close SSH again (operator).** Remove the port-22 rule. Confirm the security
group is back to 80/443 only, and record the open/close times in the local
operations notes.

Everything that needs SSH should be finished first — during the 2026-08-10
window that meant the v5 build, the collector install, the receipt anchor, and
pulling both the anchor and the dataset back. Check that list before closing;
reopening for a forgotten step costs another exposure window.

What the 2026-08-10 window actually cost, from the host's own `sshd` journal
(`/var/log/secure` is empty on AL2023 — it logs to journald, and reading the
wrong file reports a reassuring zero):

    accepted sessions        58, all from a single source IP
    failed / invalid-user    0
    first accept             17:04:57Z
    last accept              18:13:02Z
    rule removed             ~20:45Z (operator)

Zero failed attempts is the /32 scoping working: with the rule scoped to one
address, scan traffic never reached `sshd` at all. A `0.0.0.0/0` rule would have
collected background scanning within minutes.

**Closure verified from outside the host, not assumed.** After the operator
removed the rule: TCP 22 filtered, TCP 80 still reachable, HTTPS `/healthz` 200
reporting `2026-08-10-hardened-v5`. The port-80 control is the point — it
distinguishes "the SSH rule was removed" from "the host or the whole security
group went dark," which look identical if you only test 22.

One trap worth recording: the first check used `timeout 15 bash -c ...`, and
**macOS has no `timeout`**, so the command exited 127 and the `||` branch printed
"closed" — a *passing* security result produced entirely by a missing binary. Use
`nc -z -G 5 -w 5 <host> 22` on macOS, and always test a control port alongside.

## Retrieving the data

Two legs write to two places: this host writes a local file, and the Actions leg
writes `paired-forecasts.json` on the `data/provider-pairs` branch. They are not
automatically joined.

**Merged and cross-checked 2026-08-10.** The host's 2 cases merged into the
branch's 4 and added **zero** — both host hours were already present, so the
`case_id` dedupe worked on real data from two independent collectors rather than
only in theory:

    branch cases 4  +  host cases 2  ->  merged 4,  host-only added 0

The overlap is worth more than the dedupe. Comparing the two legs case by case,
every provider value was **identical**:

    lagos-20260810T1700Z  open_meteo 0.0   / 0.0     weatherapi 0.13 / 0.13
    lagos-20260810T1800Z  open_meteo 0.01  / 0.01    weatherapi 0.14 / 0.14

Two collectors, different networks, different clocks, same numbers. That is
evidence the collector is deterministic for a given question, so a case's value
does not depend on which leg wrote it — which is exactly what has to be true
before the two legs can be treated as interchangeable.

It also sharpens the P4 divergence: `weatherapi` reads 0.13-0.14 where
`open_meteo` reads 0.00-0.01 on the same hour, and that gap now reproduces
across independent collectors. It is a real disagreement between the providers,
not collection noise. Whether it is the `horizon_start`/`horizon_end` selection
bug is still unresolved.

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
