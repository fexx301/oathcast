"""Does carrying structured details in the WFV response body cost proxy score?

Predeclared 2026-10-22, before any score was computed:
- Cases: the 12 stored cases and 3 GT renderings of artifacts/s2-wfv-wording/run2-grammar-fix.json,
  rebuilt offline with the current verify.py (commit ed22a8e wording + details()).
- Shapes scored as miner_answer:
    A  compact body: {content, verified, verdict, observed_value, unit}       (deployed shape)
    B  A + "details" (claim, period, location, variable, comparison, source, limitations)
    D  A + "receipt_url" only (details served by a receipt route instead)
    C  content text only (reference: the earlier benchmark's assumption)
- Scorers available: S1 #533 (oathcast_weather_scorer.wasm) and WEATHER_FORECAST champion #3173
  (wf_4e3.wasm). Telegraph's MiniLM baseline is not on disk; not run.
- Rule: ship B if, across the 6 scorer x GT combinations, mean(B) >= mean(A) - 0.01 in at least 5
  and no combination drops by more than 0.03. Else ship D if it meets the same rule. Else keep A
  and expose details only through the receipt route without a body pointer.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from benchmark_verify_wording import CASES, TODAY  # noqa: E402
from oathcast.verify import parse_archive, parse_geocoding, parse_verify_request, render_answer, verify  # noqa: E402

parser = argparse.ArgumentParser(description="score WFV response-body shapes")
parser.add_argument("--tester", required=True, help="go-tester binary built from scoring-modules/oathcast-weather/go-tester")
parser.add_argument("--wasm", action="append", required=True, help="name=path, e.g. wf_3173=wf_4e3.wasm")
parser.add_argument("--out", required=True)
args = parser.parse_args()
SCORERS = {name: Path(path) for name, path in (item.split("=", 1) for item in args.wasm)}
stored = {c["id"]: c for c in json.loads((REPO / "artifacts/s2-wfv-wording/run2-grammar-fix.json").read_text())["cases"]}

lines = []
for case_id, location, day, variable, value, unit, question in CASES:
    case = stored[case_id]
    if "error" in case:
        continue
    request = parse_verify_request({"location": location, "date": day, "variable": variable,
                                    "forecast_value": value, "unit": unit, "question": question}, today=TODAY)
    place = parse_geocoding({"results": [case["geocoding_top"]]}, location)
    result = verify(request, parse_archive(case["archive_payload"], place, request))
    detail = result.to_dict()
    compact = {"content": render_answer(result, "observed_only"), "verified": detail["verified"],
               "verdict": detail["verdict"], "observed_value": detail["observed_value"], "unit": detail["unit"]}
    shapes = {
        "A": json.dumps(compact, ensure_ascii=False),
        "B": json.dumps({**compact, "details": result.details()}, ensure_ascii=False),
        "D": json.dumps({**compact, "receipt_url": "https://oathcastcourt.duckdns.org/v1/forecast/verify/receipts/"
                         "verify-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}, ensure_ascii=False),
        "C": compact["content"],
    }
    for gt_name, gt_text in case["ground_truths"].items():
        for shape, answer in shapes.items():
            lines.append({"id": f"{case_id}|{gt_name}|{shape}", "question": case["question"],
                          "ground_truth": gt_text, "miner_answer": answer})

batch = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"
report = {}
for scorer, wasm in SCORERS.items():
    out = subprocess.run([args.tester, "batch", str(wasm)], input=batch,
                         capture_output=True, text=True, check=True).stdout
    for row in out.splitlines():
        record = json.loads(row)
        if record.get("error"):
            raise SystemExit(f"{scorer} {record['id']}: {record['error']}")
        case_id, gt, shape = record["id"].split("|")
        report.setdefault(f"{scorer}|{gt}", {}).setdefault(shape, []).append(record["score"])

means = {combo: {shape: round(sum(v) / len(v), 4) for shape, v in shapes.items()} for combo, shapes in report.items()}
def passes(shape):
    deltas = [m[shape] - m["A"] for m in means.values()]
    return sum(d >= -0.01 for d in deltas) >= 5 and min(deltas) >= -0.03, [round(d, 4) for d in deltas]
summary = {"means": means, "B": passes("B"), "D": passes("D"), "n_per_mean": {k: len(v["A"]) for k, v in report.items()},
           "wasm_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in SCORERS.items()}}
Path(args.out).write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
