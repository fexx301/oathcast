# Engineering log

Decisions from building OathCast, with the evidence that settled them. Not a
changelog — these are the cases where the obvious answer was wrong, and what it
took to find out.

One failure mode dominates this list: **a check that passes without verifying
anything.** It appeared in a CI canary, a filesystem permission probe, a port
scan, a digest comparison, a file transfer, and a config reload. Each time it
looked like a green result. That pattern is why this project stores forecasts as
receipts rather than claims — a system built to hold itself to its record has to
distinguish "verified" from "did not fail."

Dates are UTC. Numbers are measured on this repository and this host, not
estimated.

---

## The canary was green for 96 consecutive runs and verified nothing

**2026-08-10.** A public canary requested runs every 15 minutes against the
deployed Miner and reported success 96 times in a row. Its most important step —
"Verify public Miner", the one making an authenticated forecast request — had
never once executed. `OATHCAST_MINER_API_KEY` was not configured as a repository secret, so
a guard step skipped the check and the run reported success having tested nothing.

Skipping safely when a secret is absent is the right pattern. The defect is that
it makes **the absence of a credential indistinguishable from a passing test at
the run level**, which is the level anyone actually looks at. `gh run list` showed
96 greens. Only `gh run view --json` on the step list showed `skipped`.

The rule that came out of it: *a green workflow is not evidence; the step
conclusion is.* Both this canary and the collector that reuses the pattern now
document an explicit check that the step reads `success`, not `skipped`. Verified
after the fix on run 31446432665 — the auth step reads `success`, against a live
release, on a rolling horizon.

Related: GitHub delivers scheduled workflows best-effort. Over 2026-08-06..10
this repository requested 409 `*/15` runs and received 96 — **23%**, with gaps up
to 360 minutes. Any design that assumes a cron-like schedule on hosted CI is
already wrong; the fix is to over-request and make duplicate work converge.

## Two probes said the database was writable. Both were wrong.

**2026-08-10.** Release v5 dropped the container from root to `uid=1000`. A bind
mount preserves *host* ownership, and the receipt store's file inside an already
correctly-owned directory was still `root:root 644`. Neither health endpoint
touches the receipt store, so `/healthz` and `/readyz` would have returned 200
while every forecast silently failed to persist — invisible to exactly the checks
meant to catch it.

The deployment runbook said to `chown` the **directory**. The directory was
already `1000:1000 mode 700`, so following the runbook literally would have
looked like compliance and still shipped broken.

Two probes disagreed and the reassuring one was wrong. `test -w` reported the
file writable. A `BEGIN IMMEDIATE` transaction appeared to succeed. A real
`CREATE TABLE`, run against an exact-ownership byte copy, failed with
`OperationalError: attempt to write a readonly database`. Fixed with `chown -R`
before starting the new container, and production receipts were never used as the
test subject.

For SQLite write access, trust only an actual SQLite write — and check the file,
not the directory.

## A security check that passed because it failed to run

**2026-08-10.** After closing SSH on the host, the verification was
`timeout 15 bash -c '…' || echo "closed"`. It printed `closed`. **macOS has no
`timeout`**, so the command exited 127 and the `||` branch fired — a passing
security result produced entirely by a missing binary. The port could have been
wide open.

Re-run with `nc -z -G 5 -w 5`, port 22 was genuinely filtered. The fix that
matters more than the command: **test a control port alongside the one you care
about.** Port 80 answering while 22 is filtered distinguishes "the SSH rule was
removed" from "the host went dark" — outcomes that look identical if you only
probe 22.

The same window, audited from the host's own `sshd` journal: 58 accepted
sessions, all from one source IP, **zero** failed or invalid-user attempts. Zero
is the `/32` scoping working — scan traffic never reached `sshd`. A `0.0.0.0/0`
rule collects background scanning within minutes. Worth noting the audit itself
had this bug too: `/var/log/secure` is empty on Amazon Linux 2023 because it logs
to journald, and reading the wrong file reports a reassuring zero.

## Hashing the receipt's own digest would have defeated the point

**2026-08-10.** Receipts are stored in SQLite with triggers blocking `UPDATE` and
`DELETE`. That stops SQL-level mutation and nothing else: anyone who can read the
database file can rewrite a receipt *and* recompute its `receipt_sha256` field,
producing a **self-consistent forgery** that every per-receipt integrity check
accepts. For a project pitched as a calibration court, that gap is the whole
ballgame — a Miner could quietly improve its own record after the fact.

The fix is a hash chain, `chain[i] = sha256(chain[i-1] || event_id ||
recomputed_digest)`. Three design choices are load-bearing, and each had a
plausible alternative that fails:

**Chain the recomputed digest, not the receipt's stored one.** If the chain read
`receipt_sha256` from the receipt, a forger who rewrites both the content and
that field produces a chain that still validates. Recomputing from the stored
bytes means any edit moves the head. This is the entire mechanism; getting it
backwards yields something that looks identical and detects nothing.

**A chain, not a digest over the whole set.** A set digest changes on every new
receipt, so a published value goes stale immediately and nobody keeps checking
it. A chain head published at N receipts stays verifiable forever — recomputing
over the first N rows must reproduce it exactly.

**Order by `rowid`, not `created_at`.** `created_at` is wall-clock. A replayed or
clock-skewed receipt could reorder an already-published prefix and break a valid
anchor. Deletes are trigger-blocked, so rowids are never reused and prefix order
is stable.

Covered by 16 tests, including a self-consistent forgery that defeats the
per-receipt check but still moves the head, and a truncated store that must
**not** verify against a shorter prefix.

## The anchor is a commitment, not a proof — and saying so is the point

**2026-08-10.** First real anchor: head `8a63dba5…40e230` over 6 receipts,
`integrity_check: ok`, no digest mismatches.

It would be easy to present this as proof the record is honest. It isn't, and the
distinction is worth being precise about. The anchor lives on the Miner host and
in a repository — both controlled by the same party that writes the receipts. It
demonstrates the chain is internally consistent and fixes a dated prefix. It is
not third-party evidence of anything.

An anchor's value comes **entirely** from being published somewhere its author
cannot rewrite. Publishing it externally binds us: those 6 receipts can no longer
be revised without contradicting a timestamp we cannot backdate. It still does
not let a reader *verify* anything, because the receipt store is not publicly
readable. A public read-only chain-head endpoint remains pending; do not claim
that the next release exposes it unless it is separately implemented and tested.

Also, deliberately: the 6 receipts are canary and smoke traffic. Six receipts
over six days is not adoption, and any public statement of that number says so.

## The provider disagreement that survived an independent check

**2026-08-10.** Two weather providers disagree about the same Lagos hour:
`open_meteo` reads 0.00–0.01 where `weatherapi` reads 0.13–0.14, against a 0.2305
climatology.

There is a concrete reason to suspect our own code. `open_meteo.py` selects the
point at `horizon_end` (its probability is documented as ">0.1 mm in the
*preceding* hour"); `weatherapi.py` selects at `horizon_start` (WeatherAPI labels
an hourly block by the hour it begins). Each matches its own provider's
documentation. If either reading is wrong, the two are silently answering about
**different 60-minute windows**.

Collection runs on two uncorrelated schedules — hosted CI and a systemd timer on
the host — partly for redundancy and partly as a control. Merging them was a
test of the `case_id` dedupe, which passed: 2 host cases into 4 branch cases
added zero. The more useful result was the overlap. Every provider value was
byte-identical across both collectors, on different networks and clocks. So the
collector is deterministic, a case does not depend on which leg wrote it, and the
divergence is **a real disagreement between providers, not collection noise**.

That still does not resolve it. A 13× gap and a dry climatology are consistent
with the window bug *and* with Lagos simply being dry. Distinguishing them needs
an independent observation export, which does not yet exist — so the honest
status is unresolved, and the forecasts accumulate meanwhile.

## Amazon Linux 2023 has no cron

**2026-08-10.** The collection runbook said `crontab -e`. On this host that
returns `command not found` — `cronie` is not installed and `crond` is inactive.
Installing it would add a package and a daemon to do what the running init system
already does, so the collector uses a systemd timer.

`Persistent=true` is the reason this is better rather than merely equivalent: a
run missed while the instance was stopped fires once on the next boot. Cron loses
it silently. That matters here specifically because **a missed collection hour is
permanently unrecoverable** — no free tier sells a historical *forecast* archive,
so the data point does not exist to backfill.

Confirmed on the scheduled path rather than by hand: the 18:07Z timer firing
produced a case with both providers answering. Firing it manually would have
proved the script works and nothing about whether the schedule does.

## A test fixture with a fixed date is a timer on a false alarm

**2026-08-10.** The canary's question came from a fixture pinning
2026-08-17T15:00Z. That date sits past Open-Meteo's rolling 7-day forecast window
today, so the request fails with `provider_unavailable` → 502. It would work for
six days. Then it passes its own `forecast_cutoff` and fails permanently.

Fails now, works briefly, fails forever — and a real outage looks identical at
every stage. The canary generates a rolling horizon instead.

The interesting part is what *not* to do. The obvious fix, `now + N hours`, is
wrong here: the receipt hash derives from the canonical question, so a per-run
horizon makes each of the 96 daily canary runs a distinct question and writes
**96 synthetic receipts a day** into the store meant to evidence real demand.
Anchoring to the next UTC day keeps the question stable within a day, so the
canary replays one receipt instead of manufacturing hundreds. Covered by 5
boundary tests.

Then the same bug turned up elsewhere, found by asking who else reads that
fixture. `preflight_miner.py` built real dispatcher parameters from it — and that
script exists to diagnose dispatcher problems on registration day, which falls
*after* the fixture's cutoff expires. It would have broken in roughly the window
it was needed, and the failure would have looked like a dispatcher fault. Fixing
one instance of a bug is not the same as fixing the bug.

## The transfer that reported success from the wrong command

**2026-08-10.** Verifying the deployed release meant reproducing a source digest
on the host. It came out `a5badc5a…` against the pinned `8b1788ca…`.

The reflex is to assume a sync error and re-copy. Instead the manifest's
`INCLUDE` tuple was read and the two manifests diffed: all 55 shared files were
byte-identical and exactly one was absent — `.env.example`, because the transfer
excluded dotfiles. It is in the manifest deliberately, and it carries empty
values for every secret-bearing key, so copying it reproduced the pinned digest
exactly. The real `.env` (mode 600) was never touched.

Two things worth keeping. **macOS rsync 3.4.0 crashes** on this transfer with
`buffer overflow: recv_rules (exclude.c:1683)`. And the `rsync exit=0` printed
next to that crash was **`tail`'s** exit status, not `rsync`'s — a pipeline
reports the last command's result, so a crashed transfer announced success. Same
shape as the canary and the port scan: the check ran, printed green, and had
measured something other than what it named. Redone as tar-over-SSH with digest
verification at both ends.

## Optimising the metric nobody scores

**2026-08-10.** A calibration layer was planned as a priority: better-calibrated
probabilities, lower Brier score. The then-current team description of the
scorer — a `0..1` cosine/BM25/length composite over response text — changed the
ranking. Telegraph later published the general WASM scoring contract, but this
entry records the evidence available when the decision was made; it does not
claim the pre-launch formula is the current Canonical Script. Brier remains a
separate local domain metric.

So calibration work improves a number that is not being measured, while the
*renderer* — how the probability is phrased — is the entire scored surface. The
calibration layer was demoted and the renderer rewritten. Worth stating plainly
because the demoted work is the more intellectually interesting of the two; that
is not a reason to do it first.

---

## `caddy reload` said it worked. It had changed nothing.

**2026-08-18.** Adding access logging to the public edge needed a Caddyfile
change. The host file was updated with `mv`, `caddy validate` returned `Valid
configuration`, and `caddy reload` logged `adapted config to JSON` with no
error.
Every signal said the change was live. HTTPS stayed healthy. No new header
appeared, no access log line was written, and the admin API reported no `logs`
block at all.

The Caddyfile is bind-mounted as a **file**, not a directory:
`-v /home/ec2-user/oathcast/Caddyfile:/etc/caddy/Caddyfile:ro`. A file bind
mount
resolves to an inode at container creation. `mv` replaces the path with a *new*
inode, so the host had the new config while the container kept serving the
original one. Host `inode=10035304 size=2183`, container `inode=9730509
size=751`, same path.

Writing in place afterwards did not recover it, because the mount was already
pinned to the inode `mv` had displaced. Only recreating the container
re-resolved the path. That cost a few seconds of HTTPS, which is the real price
of the mistake: the safe operation was `cat new > Caddyfile`, which truncates
and
writes the same inode and needs no restart.

Two things made this convincing rather than obvious. `caddy validate` reads the
file you point it at, so it validated the *host* file and said yes about a
config the server would never load. And `caddy reload` adapts the mounted file,
so with an unchanged file it correctly reports success for a no-op. Neither tool
was wrong; both were answering a narrower question than the one being asked.

Never `mv` over a bind-mounted file. Verify a config change from inside the
container that serves it, not from the host that wrote it.

## Five runtimes agreed. The sixth was the one CI used.

**2026-08-19.** A new ranking benchmark measured the scorer as a judge rather
than a fixture-passer, and its floors were set from a run on this laptop: worst
separation `-0.4925`, pairwise ordering accuracy `100/106`. CI failed on the
first push with `0.9340`, and a worst separation of `-0.3725`. Same commit, same
pinned `rustc 1.95.0`.

The fix that suggests itself is to lower the floor to whatever CI reports. That
would have been the expensive mistake, because it records a number as a property
of our scorer when the scorer was not what changed.

Only 2 of 43 candidate scores differed, both in one pool, and both landed on
Linux at exactly `0.49` — a value that appears in `evaluate` five times, always
as `score.min(0.49)`. A `min` cannot raise a score, so Linux was not clamping
something down to `0.49`; it was computing a *base* score above `0.49` where this
machine computed `0.15`. That ruled out the clamps and pointed at anchor
extraction. Four hypotheses followed, and the first two were both wrong:

**The two builds differ.** True, and irrelevant. darwin/arm64 and linux/amd64
produce different bytes from identical source — `2c1f7ad3` against `1daaf068`,
both 42,790 bytes, both matching the recorded platform digests. So the obvious
story is that the Linux binary is a different program. Running the *Linux bytes
on this Mac* reproduced the darwin scores exactly. The bytes do not carry the
difference, and the platform digests recorded in the evidence were a red herring
pointing at the wrong layer.

**Our Rust is non-deterministic.** No. The scorer keeps a bump allocator across
calls and the harness never deallocates, so allocation history was the obvious
suspect. But the divergence reproduces on a *single* call into a freshly
instantiated module. Nothing about call order is involved.

**The host architecture decides.** No. linux/arm64 agrees with darwin/arm64.

**The engine decides.** Yes. wazero ships two engines, a per-architecture
optimising compiler and a portable interpreter, and the test used the default
without ever saying so. Across six combinations, five agree:

| host | compiler | interpreter |
| --- | --- | --- |
| darwin/arm64 | 0.37 / 0.15 | 0.37 / 0.15 |
| linux/arm64 | 0.37 / 0.15 | 0.37 / 0.15 |
| linux/amd64 | **0.49 / 0.49** | 0.37 / 0.15 |

Those two values give `0.49 - 0.8625 = -0.3725`, exactly CI's number. GitHub's
runners are native amd64, so this is not local emulation. It is wazero v1.12.0's
amd64 compiler backend disagreeing with its own interpreter.

What makes this worth writing down is which assumption broke. A WASM module is
the one artefact whose behaviour is supposed to be independent of the machine
under it, and registration leans on exactly that: Telegraph pins the module by
Keccak over its raw bytes, on the premise that fixed bytes mean fixed behaviour.
Every margin in `release-evidence.json` is only evidence if that holds. So the
first thing to measure was not the ranking metric but the margins — and they
hold on both engines, which is now asserted rather than assumed. The affected
inputs are confined to local pools.

The benchmark now runs on the interpreter, because a ranking number that moves
with the validator's CPU describes the runtime and not the module. Separately,
all 256 corpus inputs are scored under both engines and any divergence outside
the two recorded ones fails the build. Pinning the bug is not the same as fixing
it; the point is that the next one cannot hide inside a threshold.

## What this list has in common

Seven of these are the same failure: a probe that returned a reassuring answer
without testing the thing. A skipped CI step reported as success. `test -w` on a
file that could not be written. A missing binary printing "closed". A `tail`
exit code mistaken for `rsync`'s. A digest that validates a forgery. An empty
log file read as zero intrusion attempts. A config validator and a reloader that
both said yes about a file the server never loaded.

None were caught by the check that was supposed to catch them — each needed a
second, differently-shaped observation: the step conclusion rather than the run
status, a real write rather than a permission bit, a control port alongside the
target, an independent collector rather than a re-run of the same one.

The wazero entry is the one exception, and it is worth separating. That check did
not pass without verifying anything; it failed, correctly, and pointed straight at
the problem. The failure mode there was **a measurement that silently included
the measuring apparatus** — a number attributed to the scorer that partly
described the runtime executing it. The remedy is not a second observation but a
stated one: say which engine produced a number, and assert that the numbers used
as evidence do not depend on the answer.

That is also the argument for the receipt chain, and the reason the anchor is
described as a commitment rather than a proof. A system that scores forecasts has
to be able to distinguish evidence from the absence of failure, starting with its
own.
