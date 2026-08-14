# Renderer experiment: `semantic_text_v1` → `semantic_text_v2`

Date: 2026-08-10
Status: **local development evidence only.** Every number here comes from local
proxy scorers. Telegraph has since published the scoring-module ABI and tester,
but OathCast has not reproduced the platform's Canonical Script. Nothing in this
document is a Telegraph score or a prediction of one.

## Why the renderer is the Track 1 lever

Miner performance is 75% of the Track 1 score. At the time of this experiment,
team guidance described a `0..1` cosine/BM25/length composite over response
text. The finalized public contract now establishes only that an identity-blind
WASM module receives question, ground truth, and Miner answer and returns a
score in `[0, 1]`; it does not make this local proxy the Canonical Script. Brier
remains a separate local domain metric.

That makes `src/oathcast/render.py` the scoring surface. A calibration layer
improves a number that is not currently being scored; the sentence the Miner
returns is the number that is.

## What was wrong with v1

```
At Lagos, the probability of measurable precipitation > 0.1 mm from
2026-08-17T15:00:00Z to 2026-08-17T16:00:00Z is 70.55%.
```

Two concrete defects:

1. **ISO-8601 stamps tokenize into garbage.** `2026-08-17T15:00:00Z` becomes
   `2026`, `08`, `17t15`, `00`, `00z` — fragments that match nothing a resolution
   would ever say, and that register zero matches against the time-window pattern
   in `script_benchmark.py`.
2. **It never answers the question.** The asked question is a yes/no about
   occurrence. v1 states a probability *about* the event but shares almost no
   vocabulary with a resolution sentence.

Measured: **0.4424** on the proxy lane, below the 0.55 good-response threshold.

## What v2 does

```
Measurable precipitation > 0.1 mm is likely to occur in Lagos in the hour from
15:00 to 16:00 UTC on 17 August 2026. Probability: 70.55%.
```

- Leads with **IPCC AR6 calibrated uncertainty language**, then gives the exact
  percentage. The ladder is a published external standard, deliberately not
  wording tuned against a local scorer — so it stays defensible whatever the
  Canonical Script turns out to measure.
- States the window in readable UTC clock time plus a spelled-out date.
- **Never asserts a resolved outcome.** No "Yes"/"No", no "occurred". A forecast
  says what is likely; claiming an unresolved event happened would be both
  dishonest and, when wrong, worse than an honest probability.

## Method, and two ways it was initially wrong

The harness is `scripts/benchmark_renderer.py`. Because a forecast is scored
after resolution, each variant is scored against **both** ground-truth branches
and combined as `p · score_occurred + (1−p) · score_not_occurred`.

Two self-inflicted errors were caught during the work and are worth recording,
because both inflated the apparent result:

**1. Circular ground truth.** The first winning variant scored 0.84 partly
because it contained the phrase `during the requested UTC hour` — which had also
been hand-authored into the single ground-truth sentence it was scored against.
Fixed by scoring each branch against **five deliberately varied paraphrases**
(corpus / terse / instrument-observation / verdict / plain speech) and averaging.
The honest gain dropped from +0.37 to **+0.18**.

**2. A recall-only proxy that rewards padding.** The proxy scorer measures
recall — matched truth tokens over truth tokens — so it can only ever be raised
by adding words. A deliberately verbose variant (`resolution_concepts`) topped
that lane at 0.6600 while scoring **last but one** on the F1 integrity lane
(0.4889), which penalises padding through precision. Earlier team guidance
described cosine and BM25, both of which normalise for length, so the F1 lane
was chosen as a closer local analogue. **The harness now ranks on the guard
lane.** Ranking
on the proxy alone would have shipped the longest sentence on merit it did not
have.

The decisive check was an ablation. `question_vocabulary` (with the borrowed
phrase) and `no_borrowed_phrase` (without it) score **identically** on the guard
lane — 0.5411 on the main question, 0.5828 on a second, differently-shaped
question. The borrowed phrase contributed no meaning; it only echoed vocabulary
the harness itself had authored. The shorter, non-self-referential variant was
promoted.

## Results

Dense grid, 17 probabilities from 0.02 to 0.98, main question. Full JSON in
`artifacts/renderer-benchmark/dense-grid-2026-08-10.json`.

| variant | proxy (recall) | **guard (F1)** | chars | integrity |
|---|---:|---:|---:|---|
| question_vocabulary | 0.6202 | **0.5411** | 159 | ok |
| **no_borrowed_phrase → shipped v2** | 0.5933 | **0.5411** | **140** | ok |
| natural_time | 0.5092 | 0.5269 | 112 | ok |
| answers_question | 0.5298 | 0.5088 | 128 | ok |
| resolution_concepts | 0.6600 | 0.4889 | 218 | ok |
| v1_current_shipped | 0.4424 | 0.4882 | 120 | ok |

Ranking is on the guard lane, tie-broken toward brevity. The ranking was
confirmed stable on a second question with a long location name and a different
date.

**Honest summary of the gain:** roughly **+0.05 on the guard lane** and **+0.15
on the proxy lane** versus v1. The proxy figure is the length-biased one and
should be treated as the weaker number.

## Integrity constraints discovered

Two things constrain the wording independently of any score:

- **No phrase below 50% may contain a bare `likely`.** Word-boundary polarity
  checks read `\blikely\b` as a positive claim, so "slightly less likely than
  not" reads positive while the number says negative — a self-contradiction that
  scores zero, and genuine ambiguity for a human reader. This was found only
  after densifying the probability grid; the original sparse grid skipped the
  0.33–0.5 band entirely and hid it. For the same reason "as likely as not" is
  closed at-or-above 0.50 and never below.
- **Band comparisons use inequalities, not `== 0.50`.** An exact float equality
  silently fails to match accumulated values — caught by a test sweep.

`polarity_mismatch` is deliberately **not** treated as an integrity failure. It
compares the response against resolved ground truth, so it fires whenever a
forecast that said "likely" meets an event that did not happen. That is *being
wrong*, which is unavoidable for any probabilistic forecaster — not gaming. The
only way to never trip it would be to stop stating a direction at all.

## Coverage

`tests/test_render.py`, 10 tests: envelope shape, byte-exact v2 JSON, byte-exact
v1 regression, ISO-stamp removal, event-id mismatch on both renderers, the
no-resolved-outcome rule, the full ladder, the sub-50% `likely` ban, float
boundaries, and a **1001-point sweep** asserting the renderer trips no
anti-gaming issue at any probability against both ground-truth branches.

Full suite: 126 tests passing.

## Limitations

- Telegraph's scoring-module ABI/tester are public, but the Canonical Script's
  exact scoring logic is not reproduced here; these remain local proxies.
- The question and ground-truth wording approximate platform inputs rather than
  proving the production evaluation corpus.
- A proxy gain is directional evidence only, not a predicted protocol score.
- No variant that trips an anti-gaming issue is eligible regardless of score.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/benchmark_renderer.py \
  --probability 0.02 --probability 0.05 --probability 0.12 --probability 0.20 \
  --probability 0.30 --probability 0.34 --probability 0.40 --probability 0.45 \
  --probability 0.50 --probability 0.55 --probability 0.60 --probability 0.66 \
  --probability 0.70 --probability 0.80 --probability 0.90 --probability 0.95 \
  --probability 0.98 \
  --output artifacts/renderer-benchmark/dense-grid-2026-08-10.json
```

A sparse grid is not sufficient: the original five-point grid skipped the
0.33–0.5 band and hid a fatal self-contradiction. Keep the band boundaries
(0.10, 0.33, 0.50, 0.66, 0.90) and points either side of each.
