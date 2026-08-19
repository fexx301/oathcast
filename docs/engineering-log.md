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
both 42,790 bytes, both matching the recorded platform digests. (Those are the
digests of the build under investigation that day; the scorer has changed since,
and the current artifact is recorded in `release-evidence.json`. The numbers here
are left as measured.) So the obvious
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

## The wrong fix passed every test we had. Twice.

**2026-08-19.** Telegraph's team reframed what the scoring module is for: not
winning the canonical-script slot, but ranking miners well, so a better scorer
improves the network's ranking of intelligence. That changed what to measure. A
pass/fail against the 0.15 margin floor says nothing about ranking quality, which
is why an earlier sweep of 324 generated cases reported **zero** failures while a
real defect sat untouched.

Measuring ranking instead found it immediately. Of 375 generated near-miss-shaped
pairs, 66 ranked the wrong answer above the right one, and one template failed
**45 of 45**: every pair whose ground truth restates its question.

The cause is a deliberate decision that is right almost everywhere. `fact_anchors`
drops ground-truth tokens that already appear in the question, because an anchor
the question gives away proves nothing about what the answer knows. But for "Is
Everest the tallest mountain on Earth?" answered by "Yes, Everest is the tallest
mountain on Earth.", *every* content token is in the question. The anchor set comes
out empty, the assessment returns `None`, and the score falls back to lexical
overlap with no entity check anywhere in the path. So copying the truth and
swapping the subject beat a correct paraphrase, 0.8625 against 0.3700.

The fix is one narrow rule: penalise an answer that both drops an entity the
question and truth agree on *and* asserts an entity foreign to both. Each half
alone is innocent, which is the whole difficulty. It took three attempts, and the
instructive part is which tests caught which mistake:

**Ungated, it broke seven native tests.** Caught immediately by the existing
suite, because correct answers do each half routinely: "Paris." drops France, and
"ECMWF expects rain tomorrow." drops the city while naming a forecast source.

**Using the ordinary clause boundary instead of the sentence boundary.** A comma
counts as a clause boundary, so the capital in "Yes, **K2** is the tallest
mountain on Earth." read as sentence-initial, and sentence-initial capitals are
discarded as uninformative (otherwise "That is correct." names an entity called
That). The substituted subject became invisible. **Every suite stayed green** —
native tests, ABI tests, fixture corpus, all 88 factual pairs — and only the
375-pair generator showed it, as inversions going from 9 back to 42.

**Gating on the assessment being `None`.** Also green everywhere, and also wrong:
a long ground truth yields an acronym candidate, an acronym alone is enough to
return `Some`, and the assessment then existed while still saying nothing about
which entity was bound. It silently missed 9 of the 45. The gate now asks whether
any binding anchor exists, which is what was meant all along.

Two of those three would have shipped. The pre-existing suite is not weak — it
catches contradictions, stuffing, polarity, numerics, JSON, ABI abuse — but it was
built to answer "is this answer scored correctly", and the question here is "are
these two answers ordered correctly." A suite cannot catch a defect in a property
it never measures.

The generator was not innocent either. It charged three inversions to the scorer
that belonged to its own table: one subject row had the same string for the
attribute and the rival attribute, so the "wrong" answer was the ground truth
verbatim and correctly scored 1.0. A measurement instrument needs its own
invariants, and that row now fails a check rather than a scorer.

Result: 66 inversions to 21, the 45-of-45 template to 3 of 45, ranking-pool
inversions 2 to 1, worst separation `-0.4925` to `-0.0459`, with no regression in
any pair margin under either wazero engine. What remains is a different defect:
18 pairs keep the entity and swap the attribute, usually by inserting an ordinal
like "second". An entity-binding rule cannot reach those by construction, so they
are recorded as the next measured target rather than folded into this one.

## A capitalised word silently changed which scoring model ran

**2026-08-19.** With the entity-binding defect closed, nine of the twelve
remaining generated inversions were a clean ordinal insertion: against the truth
"Everest is known as the tallest mountain on Earth.", the wrong answer "Everest is
known as the **second** tallest mountain on Earth." scored `0.839423` while a
correct paraphrase scored `0.804167`. One inserted word inverts the claim and
*raises* lexical overlap, and because the entity is still right, nothing in the
anchor path objects. Ordinals are a closed word class, so this one is genuinely
easy: penalise a rank the ground truth does not support, asymmetrically, since
dropping an ordinal is ordinary paraphrase while adding one is a different claim.
That took the inversions from 21 to 12.

The last three were the interesting ones, and they were not an ordering defect at
all. `identity_binary/Mercury` scored the *correct* answer `0.490000` against the
swapped-subject answer's `0.965625`, and `0.49` is the missing-probability
ceiling. A general-knowledge question was being scored as a weather forecast.

`is_weather_question` returns true for a weather concept plus a binary question,
and it matched concepts against the whole question string regardless of case.
**"Sun" is in the CLEAR synonym group**, alongside clear, sunny and sunshine. So
"Is Mercury the closest planet to the Sun?" satisfied both clauses, picked up
context constraints and the probability ceiling, and had its correct answer pinned
at `0.49`.

This is the most consequential of the three fixes, because of what it implies
about the fixtures we are actually judged on. Telegraph describes that category as
factual paraphrase and lexical discrimination, not weather. Any binary
general-knowledge question containing a proper noun that collides with a weather
synonym — Sun, Storm, Frost, Snow as a surname — was silently routed to the wrong
scoring model. We would have seen a mysterious single-case loss with no way to
attribute it.

The fix was already written elsewhere in the same function: the words "weather"
and "forecast" were guarded on being lowercase, and weather *concepts* were not.
Applying the existing guard consistently fixed all three, and the residual is
stated rather than smoothed over: a *lowercase* weather word used
non-meteorologically still routes a binary question to the weather path, so "Is
ice less dense than water?" is still treated as a forecast. Closing that means
dropping the weather-concept-plus-binary clause, which changes how a genuine
weather question with no temporal cue classifies, so it is asserted as a known
limitation in the tests instead of guessed at.

Two things worth keeping. First, the defect was invisible for the same reason as
the last one: every suite was green, and only a corpus that measures *ordering*
surfaced it. Second, the stopping point. The nine inversions that remain
substitute one superlative for another, "the longest river in Africa" becoming
"the deepest river in Africa" — the same surface operation as the correct
paraphrase "the tallest mountain" becoming "the highest peak", with the opposite
verdict. Separating those needs a synonym lexicon this module does not have, and
any heuristic would penalise exactly the paraphrases the fixture category rewards.
Recording a measured defect is better than trading it for an unmeasured one.

The generator was wrong twice more, both charging inversions to the scorer: one
subject row repeated the same string for the attribute and its rival, and another
gave Tokyo the rival attribute "a port city in Japan", which Tokyo is. A true
statement was being counted as a wrong answer.

## Every local number improved. The only scored one got worse.

**2026-08-19.** Four scoring-module registrations, measured by Telegraph's hidden
32-case fixture set:

| registration | wins | candidate margin | score stddev |
| --- | --- | --- | --- |
| 19 | 31/32 | 0.31248063 | 0.26765382 |
| 41 | 31/32 | 0.37852418 | 0.29563302 |
| 96 | 31/32 | 0.37149292 | 0.29768366 |
| 98 | **28/32** | 0.36663616 | 0.33863735 |

Across that same span the local measurements moved one way only. Generated-pair
inversions fell from 66 of 375 to 9. The ten handwritten ranking pools went from 7
separated with 1 tie and 2 inversions to 10 separated with neither. Five defects
were found, each with a regression test and a ratcheted floor. Every suite was
green at every step.

The external result went neutral, neutral, neutral, then **down three cases**.

So the three middle fixes — entity binding, inserted ordinals, the weather
classifier — were worth nothing against the fixtures that decide promotion, and the
two changes after registration 96 were worth *less* than nothing.

The mechanism is not mysterious in hindsight. Both of those last two changes convert
a signal that previously **capped** a score into one that **zeroes** it:
`relation_mismatch` moved from the ambiguity path, which applies
`score.min(0.49)`, to `contradicted`, which returns zero; and the slot-substitution
rule returns zero as well. A correct answer that trips either falls from a capped
score that could still beat its paired wrong answer to zero, which loses the case
outright. The score standard deviation rising from `0.2977` to `0.3386` is what a
build with more zeros in it looks like.

Both rules were validated against 53 native tests, 88 factual pairs, 27 fixture
cases, 10 pools and 375 generated pairs. All of them agreed. They agreed because
**we wrote them**: a rule that misfires only on shapes we did not think of is
invisible to a corpus we authored. The benchmark is a sound regression guard for the
properties it encodes. It is not a proxy for a fixture set we cannot see, and
treating a rising local score as evidence of external progress was the error.

Two things worth keeping, beyond the obvious one about held-out data.

**A hard zero is asymmetric.** It can only ever remove a win that a capped score
might have kept. A cap that is too aggressive costs margin; a zero that is too
aggressive costs the case. Given uncertainty about whether a signal means "false" or
merely "unclear", the cap is the conservative choice, and I chose the zero twice in
one build.

**The measurement I trusted was the one I could improve.** The local benchmark gave
fast, legible, monotonically improving feedback, and the external one gave a single
integer every few hours. That asymmetry in convenience is exactly how optimisation
pressure ends up pointed at the wrong target, and the safeguard is not better local
metrics but a standing suspicion of them.

Registration 96 remains the best externally measured state. Reverting the two
zeroing rules is what the only scored measurement indicates, and it will make the
local ranking numbers worse: the tie and the inversion both come back. That conflict
is real, and the external measure is the one that decides.

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

The entity-binding entry is the same lesson from the other side. Nothing there
returned a false green through a broken probe; every test ran and reported
honestly. Two wrong fixes still passed, because the suite measured whether an
answer scores correctly and the defect was in whether two answers *order*
correctly. That is the sharpest form of the pattern in this list: a green suite is
evidence about the properties it measures and about nothing else, and the way out
is not a better probe but a new measurement.

That is also the argument for the receipt chain, and the reason the anchor is
described as a commitment rather than a proof. A system that scores forecasts has
to be able to distinguish evidence from the absence of failure, starting with its
own.
