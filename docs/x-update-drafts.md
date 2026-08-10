# X update drafts

These are drafts only. Publish manually after checking the evidence attached to
each post. Tag `@Telegraphprotoc` in every update, and never describe fixtures,
direct upstream calls, or the local queue as live Telegraph usage.

## Draft 1 — the problem

Weather forecasts are easy to display and hard to trust.

We’re building OathCast: a calibration court for short-horizon machine
forecasts. It freezes the question before the weather window, compares answers
from competing Miners, and settles the result against an independent
observation.

Preparation build underway with @Telegraphprotoc.

## Draft 2 — the Application spine

OathCast is not a weather chatbot.

The Application asks: “What decision changes if rain is likely during this exact
hour?” It compares owned and independent Miner responses, records why the
decision moved, and keeps working when our own Miner is disabled.

Local demo evidence is labeled as preparation; live Telegraph traffic comes
only after the official payment and registration gates open. @Telegraphprotoc

## Draft 3 — anti-gaming Script Author work

We added a local adversarial evaluator benchmark for the future Script Author:

- wrong yes/no outcomes
- contradictory probabilities
- wrong time windows
- malformed and overlong responses
- keyword stuffing

The candidate rejects the fixed adversarial corpus while preserving good
responses. This is development evidence, not Telegraph’s Canonical Script.
@Telegraphprotoc

## Draft 4 — chronological provider work

Provider comparison needs more than a pretty average.

Our local backtest now uses a frozen warmup/holdout split, resolution-aware
history, simultaneous-timestamp batching, coverage, common-case Brier, and
end-to-end utility. The current fixture is synthetic and explicitly not live
provider evidence.

Building the measurement layer before making claims. @Telegraphprotoc

## Draft 5 — pilot invitation

We’re preparing a small planning pilot for OathCast.

Give us one real outdoor decision, one location, and one exact UTC hour. The
current intake stores no contact details and makes no paid or Telegraph call;
it helps us design the Application around decisions people actually make.

If your plan changes when rain crosses 50%, we want to hear the question.
@Telegraphprotoc

## Draft 6 — live evidence template (publish only after verification)

Today OathCast routed `[N]` legitimate Application questions through Telegraph
using `[N]` active Miners. Explorer evidence: `[link]`.

The Application decision changed when `[external Miner / response]` was
included. After the `[observation source]` resolution, the scorecard showed
`[result]`. Payment/settlement evidence and limitations are documented here:
`[repository link]`.

No synthetic or self-generated traffic was included. @Telegraphprotoc

## Draft 7 — receipt-chain anchor (ready to publish)

This is the one draft here whose *publication* is the deliverable rather than the
writing. The anchor in `artifacts/receipt-anchors/anchor-2026-08-10.json` is
committed, but a commit is the weakest venue — the same party controls the repo
and the receipts. An X post is timestamped by a platform OathCast does not
control, which is the entire point. See `improvements.md` §A2.

**Publish exactly these digits.** `head_sha256` and `receipt_count` are both
load-bearing: `--verify` recomputes the chain over the *first N* rows, so a post
with the hash but not the count commits to nothing checkable.

    head   8a63dba5afce18ba52eafd00ee1024f7596a94ece114bd51b4d950b66040e230
    count  6
    window 2026-08-04T18:51:27Z -> 2026-08-10T17:46:37Z

### Thread version (preferred — 3 posts, each under 280)

**1/3**

> A forecast record you can rewrite isn't a record.
>
> OathCast stores every forecast as a receipt and hashes them into a chain.
> Publishing the head today, so the past is fixed before we have anything to gain
> by editing it. @Telegraphprotoc

**2/3**

> head: 8a63dba5afce18ba52eafd00ee1024f7596a94ece114bd51b4d950b66040e230
> count: 6
> window: 2026-08-04T18:51Z -> 2026-08-10T17:46Z
>
> These 6 are our own canary and smoke runs. Not user demand — the mechanism is
> what's live, not the volume.

**3/3**

> Why a chain and not a checksum: the digest is recomputed from stored bytes, so
> rewriting a receipt AND its own hash field still moves the head. And a head
> published at 6 stays checkable forever — recompute over the first 6 rows.
>
> Anchor + code: [repo link]

### Single-post version (269 chars, if the thread is too much)

> OathCast receipt-chain head, published so we can't quietly rewrite it later:
>
> 8a63dba5afce18ba52eafd00ee1024f7596a94ece114bd51b4d950b66040e230
>
> 6 receipts, through 2026-08-10T17:46Z. Canary traffic, not user demand.
>
> Any edit to those 6 moves the head. @Telegraphprotoc

Lengths checked, not eyeballed: 236 / 234 / 268 (3/3 counted with a real URL at
X's flat 23 chars) and 269 for the single post. Worth checking because the hash is
64 characters and truncation would land exactly on it, turning the one
load-bearing string into a broken fragment.

### What this post does and does not do

It **does** fix the prefix at a third-party timestamp. After posting, those 6
receipts cannot be revised without contradicting a record OathCast cannot edit or
backdate. That constrains us, which is what makes it worth anything.

It **does not** let a reader verify anything today: the receipt store is on the
Miner host and is not publicly readable, so nobody outside can recompute the
chain. Publishing is a commitment, not a proof, and the post must not be worded
as one. What closes that gap is a public read-only head endpoint — see
`improvements.md` §A2 — which needs a redeploy and therefore the next SSH window.

**Do not restate the count as forecasts served.** Six receipts across six days,
mostly canary, is not adoption evidence, and the guardrail against presenting
automated traffic as demand applies to our own X posts before anyone else's.
