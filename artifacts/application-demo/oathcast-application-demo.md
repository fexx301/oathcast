# OathCast Application evidence demo

> **DEVELOPMENT FIXTURE ONLY** — presentation `application_evidence_markdown_v1`. This run is not Telegraph traffic, payment evidence, official demand, or a live ground-truth claim.

## Forecast case

- **Event:** `dev-lagos-2026-08-17-1500z`
- **Location:** Lagos (6.5244, 3.3792)
- **Window:** `2026-08-17T15:00:00Z` → `2026-08-17T16:00:00Z` (UTC)
- **Cutoff:** `2026-08-17T12:00:00Z`
- **Predicate:** `precipitation > 0.1 mm`

## Miner comparison

| Miner | Ownership | Probability | Valid | Latency (ms) | Transport |
| --- | --- | ---: | --- | ---: | --- |
| oathcast-weather | owned | 90.00% | yes | 0.01 | development_http_or_injected |
| independent-weather-alpha | external | 20.00% | yes | 0.00 | development_http_or_injected |
| independent-weather-beta | external | 30.00% | yes | 0.00 | development_http_or_injected |

## Live decision

- **Aggregate probability:** 46.67%
- **Event likely:** no
- **Recommended action:** `plan_for_no_event`
- **External Miner used:** yes
- **External influence detected:** yes
- **Application request ID:** `app-b5db3c20020c43539f3ba48212445395`

## Later resolution

- **Status:** `resolved`
- **Outcome:** 1 (`1` means the event occurred; `0` means it did not)
- **Observed precipitation:** 0.25 mm
- **Observation source:** `development-fixture-observation`
- **Observation ID:** `fixture-observation-1`

## Durable evidence

- **Question SHA-256:** `c69272144fb4cfec7d6b5f95ad35e3892da2e22498b526cdc28a5f12ec4f62be`
- **Decision SHA-256:** `c40df940220e874e2483995dca4a617bd6f384a695bf5925cbd7d1627fad0151`
- **Resolution SHA-256:** `923ee483cf8f1d22d0f7787a6bf8088fb2d38a901d794f5569af9801a37a84fa`
- **Protocol/payment receipts:** not present in this fixture run

## Owned-Miner-disabled ablation

- **Ablation passed:** yes
- **Owned Miner disabled:** yes
- **External replies remained usable:** yes
- **Fallback aggregate probability:** 25.00%

## Miner response details

### `oathcast-weather`

- **Normalized content:** Fixture probability: 90%
- **Raw response:**

```json
{
  "content": "Fixture probability: 90%",
  "probability": 0.9
}
```

### `independent-weather-alpha`

- **Normalized content:** Fixture probability: 20%
- **Raw response:**

```json
{
  "content": "Fixture probability: 20%",
  "probability": 0.2
}
```

### `independent-weather-beta`

- **Normalized content:** Fixture probability: 30%
- **Raw response:**

```json
{
  "content": "Fixture probability: 30%",
  "probability": 0.3
}
```

## Interpretation boundary

This demo proves that the Application can compare owned and external responses, retain case evidence, resolve an exact observation window, and continue with the owned Miner disabled. It does not prove Miner registration, WASM scoring, paid Telegraph traffic, Explorer activity, or official Track 3 qualification.
