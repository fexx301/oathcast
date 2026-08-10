# Provider-pair collection data

Machine-written branch. `paired-forecasts.json` is appended to by
`.github/workflows/collect-provider-pairs.yml` and, when it is running, by the
EC2 collector. Do not hand-edit it.

Each case is one location at a fixed 3-hour lead, recording every provider's
answer for the same one-hour UTC window. A provider that failed is recorded as
`status: "missing"` rather than dropped, so availability differences cannot bias
the comparison. Cases are keyed by `case_id` (`slug-YYYYMMDDTHHMMZ`, floored to
the hour), so two collectors running in the same hour converge on one case
instead of double-counting.

Cases are collected **unresolved**. Scoring them needs an independent
observation export; the bundled `fixtures/observation_export.json` on `main` is
a development fixture whose independence is not asserted.

This is the operator's own provider-account traffic. It is not Telegraph
traffic and is not hackathon demand.
