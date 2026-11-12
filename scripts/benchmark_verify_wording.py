#!/usr/bin/env python3
"""Measure WEATHER_FORECAST_VERIFY answer wording against a proxy scorer.

The real ground-truth text for WEATHER_FORECAST_VERIFY is not published, so
each case is scored against three plausible ground-truth renderings (a raw
archive record, an observed-values sentence and a verdict sentence). The
ground-truth templates deliberately differ from OathCast's answer templates so
that no style wins by copying a ground truth word for word.

The cases and the acceptance rule were fixed before any observation was
fetched; see the Season II event note. Scores come from a caller-supplied WASM
module run through the go-tester's batch mode, so the numbers describe that
module, not Telegraph's live champion.

Usage:
    python scripts/benchmark_verify_wording.py --wasm <module.wasm> \
        --tester <go-tester binary> --out artifacts/s2-wfv-wording
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oathcast.verify import (  # noqa: E402
    VerifyError,
    archive_url,
    geocoding_url,
    parse_archive,
    parse_geocoding,
    parse_verify_request,
    render_answer,
    verify,
)

TODAY = date(2026, 9, 28)
USER_AGENT = "oathcast-verify-benchmark/1 (+https://github.com/fexx301/oathcast)"
TXLENS_VERIFY = "https://telegraph-onchain-tx-lookup-miner.onrender.com/weather-forecast-verify"

# Fixed 2026-09-28 before any observation was fetched.
CASES = [
    ("lisbon", "Lisbon", "2026-09-15", "precipitation", "12", "mm",
     "The forecast for Lisbon on 15 September 2026 called for 12 mm of rain. How much actually fell, and did the forecast verify?"),
    ("london", "London", "2026-09-10", "temperature_max", "21", "C",
     "London's high on 10 September 2026 was forecast at 21°C. What was the observed maximum, and did the forecast verify?"),
    ("tokyo", "Tokyo", "2026-09-05", "temperature_max", "31", "C",
     "Tokyo was forecast to reach 31°C on 5 September 2026. Did that forecast verify against what was observed?"),
    ("chicago", "Chicago", "2026-09-12", "wind_max", "25", "mph",
     "Chicago's forecast for 12 September 2026 said winds would peak at 25 mph. What was the observed peak wind, and did it verify?"),
    ("lagos", "Lagos", "2026-09-08", "precipitation", "20", "mm",
     "Yesterday's forecast for Lagos on 8 September 2026 called for 20 mm of rain. What did stations record, and did the forecast verify?"),
    ("sydney", "Sydney", "2026-09-14", "temperature_min", "9", "C",
     "The overnight low in Sydney on 14 September 2026 was forecast at 9°C. What was observed, and did the forecast verify?"),
    ("mumbai", "Mumbai", "2026-09-03", "precipitation", "45", "mm",
     "Mumbai was forecast to get 45 mm of rain on 3 September 2026. How much fell, and did the forecast verify?"),
    ("denver", "Denver", "2026-09-18", "temperature_max", "82", "F",
     "The high in Denver on 18 September 2026 was forecast at 82°F. What was observed, and did the forecast verify?"),
    ("reykjavik", "Reykjavik", "2026-09-11", "wind_max", "40", "km/h",
     "Reykjavik's forecast for 11 September 2026 had maximum winds of 40 km/h. What was observed, and did the forecast verify?"),
    ("cape-town", "Cape Town", "2026-09-16", "precipitation", "0", "mm",
     "The forecast for Cape Town on 16 September 2026 said it would stay dry, with no rain. Did it rain, and did the forecast verify?"),
    ("berlin", "Berlin", "2026-09-07", "temperature_min", "12", "C",
     "Berlin's minimum temperature on 7 September 2026 was forecast at 12°C. What was the observed low, and did the forecast verify?"),
    ("sao-paulo", "São Paulo", "2026-09-19", "precipitation", "5", "mm",
     "São Paulo was forecast to get 5 mm of rain on 19 September 2026. How much actually fell, and did the forecast verify?"),
]

STYLES = ("observed_only", "verdict_first", "observed_then_verdict")
BASELINE_STYLE = "observed_only"


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public origins
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str | None:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed public origin
            return response.read().decode("utf-8")
    except Exception:  # an unreachable comparison miner is recorded, not fatal
        return None


def ground_truths(result) -> dict[str, str]:
    obs = result.observation
    unit = obs.variable.unit
    day = obs.start.isoformat()
    record = {
        "latitude": obs.grid_latitude,
        "longitude": obs.grid_longitude,
        "timezone": obs.timezone,
        "daily_units": {"time": "iso8601", obs.variable.archive_field: unit},
        "daily": {"time": [day], obs.variable.archive_field: [obs.value]},
    }
    verdict = "verified" if result.verdict == "verified" else "not verified"
    return {
        "gt_data": json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        "gt_sentence": f"Observed {obs.variable.label} at {obs.place.name} ({day}): {obs.value:g} {unit}.",
        "gt_verdict": (
            f"Forecast {result.forecast_value:g} {unit}; observed {obs.value:g} {unit} at {obs.place.name} "
            f"on {day}. Result: {verdict}."
        ),
    }


def txlens_text(location: str, day: str) -> str | None:
    raw = fetch_text(f"{TXLENS_VERIFY}?{urlencode({'location': location, 'date': day})}")
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip() or None
    for key in ("answer", "content", "summary", "result"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wasm", required=True)
    parser.add_argument("--tester", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--skip-txlens", action="store_true")
    parser.add_argument(
        "--from-report",
        help="rescore the cases, answers and ground truths stored in an earlier report; no network",
    )
    parser.add_argument("--name", default="report", help="output file stem")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wasm_sha256 = hashlib.sha256(Path(args.wasm).read_bytes()).hexdigest()

    cases, lines = [], []
    if args.from_report:
        cases = json.loads(Path(args.from_report).read_text())["cases"]
        for case in cases:
            if "error" in case:
                continue
            for gt_name, gt_text in case["ground_truths"].items():
                for style, answer in case["answers"].items():
                    lines.append({"id": f"{case['id']}|{gt_name}|{style}", "question": case["question"],
                                  "ground_truth": gt_text, "miner_answer": answer})
    for case_id, location, day, variable, value, unit, question in ([] if args.from_report else CASES):
        request = parse_verify_request(
            {"location": location, "date": day, "variable": variable,
             "forecast_value": value, "unit": unit, "question": question},
            today=TODAY,
        )
        geo_payload = fetch_json(geocoding_url(location))
        place = parse_geocoding(geo_payload, location)
        archive_payload = fetch_json(archive_url(place, request))
        try:
            observation = parse_archive(archive_payload, place, request)
        except VerifyError as exc:
            cases.append({"id": case_id, "error": str(exc)})
            continue
        result = verify(request, observation)
        answers = {style: render_answer(result, style) for style in STYLES}
        answers["raw_archive_json"] = json.dumps(archive_payload, ensure_ascii=False, separators=(",", ":"))
        if not args.skip_txlens:
            incumbent = txlens_text(location, day)
            if incumbent:
                answers["txlens_live"] = incumbent
        truths = ground_truths(result)
        cases.append({
            "id": case_id, "question": question, "result": result.to_dict(),
            "answers": answers, "ground_truths": truths,
            "archive_payload": archive_payload, "geocoding_top": geo_payload.get("results", [None])[0],
        })
        for gt_name, gt_text in truths.items():
            for style, answer in answers.items():
                lines.append({"id": f"{case_id}|{gt_name}|{style}", "question": question,
                              "ground_truth": gt_text, "miner_answer": answer})

    batch = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"
    completed = subprocess.run(
        [args.tester, "batch", args.wasm], input=batch, capture_output=True, text=True, check=True
    )
    scores = {}
    for row in completed.stdout.splitlines():
        record = json.loads(row)
        if record.get("error"):
            raise SystemExit(f"scorer error for {record['id']}: {record['error']}")
        scores[record["id"]] = record["score"]

    summary: dict[str, dict] = {}
    scored_cases = [case for case in cases if "error" not in case]
    for gt_name in ("gt_data", "gt_sentence", "gt_verdict"):
        per_style = {}
        styles = sorted({style for case in scored_cases for style in case["answers"]})
        for style in styles:
            values = [scores[f"{c['id']}|{gt_name}|{style}"] for c in scored_cases if style in c["answers"]]
            deltas = [
                scores[f"{c['id']}|{gt_name}|{style}"] - scores[f"{c['id']}|{gt_name}|{BASELINE_STYLE}"]
                for c in scored_cases if style in c["answers"]
            ]
            wins = sum(delta > 0 for delta in deltas)
            worst = min(deltas) if deltas else 0.0
            per_style[style] = {
                "n": len(values),
                "mean": round(sum(values) / len(values), 4) if values else None,
                "wins_vs_observed_only": wins,
                "worst_delta_vs_observed_only": round(worst, 4),
                "passes": style in STYLES and style != BASELINE_STYLE and len(values) == 12
                and wins >= 9 and worst >= -0.05,
            }
        summary[gt_name] = per_style

    passing = {
        style: sum(summary[gt][style]["passes"] for gt in summary)
        for style in STYLES if style != BASELINE_STYLE
    }
    best = max(passing, key=passing.get)
    decision = best if passing[best] >= 2 else "unproven: ship observed_only text, verdict as a structured field"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rescored_from": args.from_report,
        "wasm_sha256": wasm_sha256,
        "engine": "compiler",
        "cases_scored": len(scored_cases),
        "case_errors": [c for c in cases if "error" in c],
        "summary": summary,
        "gt_formats_passed": passing,
        "decision": decision,
        "scores": scores,
        "cases": cases,
    }
    (out / f"{args.name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("cases_scored", "summary", "gt_formats_passed", "decision")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
