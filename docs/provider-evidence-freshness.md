# Provider-evidence freshness monitor

`.github/workflows/provider-evidence-freshness.yml` monitors the forward-collected
dataset on `data/provider-pairs`. It is deliberately separate from the public
Miner canary: it makes no provider or Miner request, needs no secret, and never
edits the dataset.

The workflow requests an hourly run at `:53` and creates two independent job
conclusions:

- **Collection data age:** fails when the newest case's `issued_at` is more than
  **6 hours** old. Exactly 6 hours is allowed; a future-dated latest case fails.
  The data branch is the monitored source, so an unmerged host-local collection
  does not make this check green.
- **Observation resolution lag:** fails when any unresolved case is more than
  **48 hours past `horizon_end`**. Exactly 48 hours is allowed. Open windows and
  already-resolved cases do not count.

Six hours matches the documented worst observed GitHub scheduling gap while
still paging on a longer interruption to an hourly collector. The 48-hour grace
assumes a daily independent observation/resolution process and permits one
missed daily cycle. That observation pipeline does not yet exist, so the
resolution job is expected to turn red when the oldest closed case crosses the
limit; do not resolve it with the bundled development fixture merely to clear
the alert.

The checker exits `0` when the selected condition is healthy, `1` for a stale
selected condition, and `2` when it cannot establish a trustworthy status
(missing file, invalid dataset, timestamp, or threshold). A stale matrix job
does not cancel the other job.

For a deterministic local check against a downloaded branch copy:

    PYTHONPATH=src python3 scripts/check_provider_freshness.py \
      --dataset /tmp/paired-forecasts.json \
      --check collection \
      --now 2026-08-12T18:00:00Z

Use `--check resolution` for the independent observation-lag result.
