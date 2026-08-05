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
