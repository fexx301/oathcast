#!/usr/bin/env python3
"""Run a non-destructive public Miner release and auth smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from oathcast.protocol import outbound_headers


ROOT = Path(__file__).resolve().parents[1]


def format_timestamp(moment: datetime) -> str:
    return f"{moment:%Y-%m-%dT%H:%M:%SZ}"


def rolling_horizon(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Pick a horizon that is inside every provider's window, at any run time.

    A fixed calendar date cannot survive as a recurring canary, because it is
    squeezed between two independent failure modes:

    * too far out -- Open-Meteo publishes a rolling 7 days, and
      ``select_exact_point`` correctly refuses to substitute a neighbouring
      hour, so a horizon past the window surfaces as ``provider_unavailable``
      and a 502;
    * too far back -- ``service.py:435`` rejects a request issued at or after
      its ``forecast_cutoff``.

    ``fixtures/question.json`` asks for 2026-08-17T15:00Z, which is beyond the
    window today and permanently past its own cutoff after 2026-08-17T12:00Z.
    It therefore fails now, works for a few days, then fails forever.

    This targets the gap between those bounds: 12:00-13:00 UTC on the *next*
    UTC day -- never nearer than 11 hours, never further than 36, and it moves
    forward on its own.

    Anchoring to the next UTC *day* rather than ``now + N hours`` is the load
    bearing part. The receipt hash is derived from the canonical question, so
    an identical question replays one receipt instead of writing a new row. A
    horizon that moved with every run would make each of the 96 daily canary
    runs a distinct question and write 96 receipts a day of synthetic traffic
    into the store that is meant to be evidence of real demand. Stable within
    the day, this costs at most one receipt per day.
    """

    tomorrow = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    start = tomorrow.replace(hour=12)
    return start, start + timedelta(hours=1), start - timedelta(hours=1)


def header_value(headers: dict[str, str], name: str) -> str | None:
    """Read an HTTP header without depending on proxy casing."""

    wanted = name.lower()
    return next((value for key, value in headers.items() if key.lower() == wanted), None)


def json_sha256(value: object) -> str:
    """Hash a public JSON value canonically for release-to-release comparison."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def request_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], object]:
    request = Request(url, headers=outbound_headers(headers))
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return response.status, dict(response.headers.items()), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"body": body[:500]}
        return exc.code, dict(exc.headers.items()), payload
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


def receipt_capacity_check(ready: object, *, min_headroom_percent: float) -> dict[str, object]:
    """Alert on a filling receipt store *before* it starts refusing forecasts.

    A full store makes every new forecast return 507, so the useful signal is
    headroom, not the cliff itself. Whichever caps are configured are checked;
    an uncapped store trivially passes.

    A deployed release older than the capacity change does not report the
    field at all. That is recorded as ``reported: False`` rather than failed --
    the canary runs against a live host that can legitimately lag the repo
    between a merge and a redeploy -- but it stays visible in the output so the
    absence is never mistaken for a healthy reading.
    """

    check: dict[str, object] = {"name": "receipt_capacity", "ok": True, "reported": False}
    if not isinstance(ready, dict):
        return check
    capacity = ready.get("receipt_store")
    if not isinstance(capacity, dict):
        return check

    check["reported"] = True
    check["accepting_new_receipts"] = capacity.get("accepting_new_receipts")
    headrooms: list[float] = []
    for used_key, max_key, label in (
        ("rows", "max_rows", "rows"),
        ("used_bytes", "max_bytes", "bytes"),
    ):
        limit = capacity.get(max_key)
        used = capacity.get(used_key)
        if not isinstance(limit, (int, float)) or not isinstance(used, (int, float)):
            continue
        if limit <= 0:
            continue
        headroom = max(0.0, (limit - used) / limit) * 100.0
        check[f"{label}_headroom_percent"] = round(headroom, 3)
        headrooms.append(headroom)

    if capacity.get("accepting_new_receipts") is False:
        check["ok"] = False
        check["error"] = "receipt store is full; new forecasts will return 507"
        return check
    if headrooms and min(headrooms) < min_headroom_percent:
        check["ok"] = False
        check["error"] = (
            f"receipt store headroom {min(headrooms):.2f}% is below the "
            f"{min_headroom_percent}% threshold"
        )
    return check


def receipt_write_check(ready: object, *, required: bool = False) -> dict[str, object]:
    """Require the deployed readiness surface to prove SQLite write access.

    This is intentionally stricter than the capacity check. The post-v5
    release exists partly to close a production failure where health/readiness
    looked healthy while the receipt database could not persist forecasts. The
    recurring canary may still observe a deliberately older live release during
    a staged cutover, so absence is visible but only fails when the v6 release
    smoke opts in with ``required=True``.
    """

    check: dict[str, object] = {
        "name": "receipt_store_write",
        "ok": not required,
        "required": required,
        "reported": False,
    }
    if not isinstance(ready, dict):
        check["error"] = "readyz payload is not an object"
        return check
    probe = ready.get("receipt_store_write")
    if not isinstance(probe, dict):
        check["error"] = "readyz does not report receipt_store_write"
        return check

    check["reported"] = True
    check["probe"] = probe.get("probe")
    check["rolled_back"] = probe.get("rolled_back")
    check["cached"] = probe.get("cached")
    check["ok"] = (
        probe.get("ready") is True
        and probe.get("probe") == "sqlite_transactional_write"
        and probe.get("rolled_back") is True
    )
    if not check["ok"]:
        check["error"] = "transactional receipt-store write probe is not ready"
    return check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("OATHCAST_PUBLIC_URL", "https://oathcastcourt.duckdns.org"))
    parser.add_argument("--token-env", default="OATHCAST_MINER_API_KEY")
    parser.add_argument("--expected-release-id")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--expected-image-digest")
    parser.add_argument(
        "--require-receipt-write-probe",
        action="store_true",
        help="fail unless /readyz proves the transactional SQLite write probe",
    )
    parser.add_argument(
        "--min-receipt-headroom-percent",
        type=float,
        default=10.0,
        help="fail the canary when receipt-store headroom drops below this percentage",
    )
    parser.add_argument(
        "--question-file",
        type=Path,
        default=None,
        help=(
            "question to smoke with. Defaults to a rolling horizon (12:00-13:00 UTC "
            "tomorrow) so the check keeps working as dates pass; pass a file to pin one."
        ),
    )
    parser.add_argument(
        "--question-output",
        type=Path,
        default=None,
        help="write the exact non-secret question used by this run for a later replay",
    )
    parser.add_argument("--skip-authenticated", action="store_true")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    checks: list[dict[str, object]] = []

    health_status, health_headers, health = request_json(f"{base_url}/healthz")
    health_ok = health_status == 200 and isinstance(health, dict) and health.get("ok") is True
    checks.append({"name": "healthz", "status": health_status, "ok": health_ok})
    release_id = health.get("release", {}).get("release_id") if isinstance(health, dict) else None
    release = health.get("release", {}) if isinstance(health, dict) else {}
    if args.expected_release_id is not None and release_id != args.expected_release_id:
        checks.append({
            "name": "release_id",
            "expected": args.expected_release_id,
            "actual": release_id,
            "ok": False,
        })
    if args.expected_source_sha256 is not None and release.get("source_sha256") != args.expected_source_sha256:
        checks.append({
            "name": "source_sha256",
            "expected": args.expected_source_sha256,
            "actual": release.get("source_sha256"),
            "ok": False,
        })
    if args.expected_image_digest is not None and release.get("image_digest") != args.expected_image_digest:
        checks.append({
            "name": "image_digest",
            "expected": args.expected_image_digest,
            "actual": release.get("image_digest"),
            "ok": False,
        })

    ready_status, _, ready = request_json(f"{base_url}/readyz")
    checks.append({"name": "readyz", "status": ready_status, "ok": ready_status == 200 and isinstance(ready, dict) and ready.get("ready") is True})
    checks.append(
        receipt_capacity_check(ready, min_headroom_percent=args.min_receipt_headroom_percent)
    )
    checks.append(receipt_write_check(ready, required=args.require_receipt_write_probe))

    if args.question_file is not None:
        question = json.loads(args.question_file.read_text(encoding="utf-8"))
        horizon_start = question["horizon_start"]
        horizon_end = question["horizon_end"]
        forecast_cutoff = question["forecast_cutoff"]
    else:
        question = json.loads(
            (ROOT / "fixtures" / "question.json").read_text(encoding="utf-8")
        )
        start, end, cutoff = rolling_horizon(datetime.now(timezone.utc))
        horizon_start = format_timestamp(start)
        horizon_end = format_timestamp(end)
        forecast_cutoff = format_timestamp(cutoff)
        # The fixture's event_id names a fixed date. Left alone it would stamp
        # "2026-08-17-1500z" onto receipts for a horizon that is no longer that
        # hour. event_id is excluded from the request hash (service.py:603), so
        # naming the real horizon costs no replay stability and keeps the stored
        # receipt honest about what was asked.
        question["event_id"] = f"canary-lagos-{start:%Y%m%dT%H%M}z"

    question["horizon_start"] = horizon_start
    question["horizon_end"] = horizon_end
    question["forecast_cutoff"] = forecast_cutoff
    if args.question_output is not None:
        args.question_output.parent.mkdir(parents=True, exist_ok=True)
        args.question_output.write_text(
            json.dumps(question, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    params = {
        "event_id": question["event_id"],
        "location_name": question["location_name"],
        "lat": f"{question['latitude']:.6f}",
        "lon": f"{question['longitude']:.6f}",
        "horizon_start": horizon_start,
        "horizon_end": horizon_end,
        "forecast_cutoff": forecast_cutoff,
        "threshold_mm": str(question["threshold_mm"]),
    }
    forecast_url = f"{base_url}/v1/forecast/point?{urlencode(params)}"
    unauthorized_status, _, _ = request_json(forecast_url)
    checks.append({"name": "unauthorized_rejected", "status": unauthorized_status, "ok": unauthorized_status == 401})

    if not args.skip_authenticated:
        token = os.getenv(args.token_env)
        if not token:
            checks.append({"name": "authenticated_forecast", "ok": False, "error": f"{args.token_env} is not set"})
        else:
            forecast_status, forecast_headers, forecast = request_json(
                forecast_url,
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "smoke-release"},
            )
            receipt_sha256 = header_value(
                forecast_headers, "X-OathCast-Receipt-SHA256"
            )
            response_request_id = header_value(
                forecast_headers, "X-OathCast-Request-ID"
            )
            response_ok = (
                forecast_status == 200
                and isinstance(forecast, dict)
                and isinstance(forecast.get("content"), str)
                and bool(receipt_sha256)
                and bool(response_request_id)
            )
            forecast_check: dict[str, object] = {
                "name": "authenticated_forecast",
                "status": forecast_status,
                "ok": response_ok,
                "event_id": question["event_id"],
            }
            if receipt_sha256:
                forecast_check["receipt_sha256"] = receipt_sha256
            if response_request_id:
                forecast_check["request_id"] = response_request_id
            if isinstance(forecast, dict):
                forecast_check["public_response_sha256"] = json_sha256(forecast)
            checks.append(forecast_check)

    result = {
        "base_url": base_url,
        "release_id": release_id,
        "release": release,
        "question": question,
        "checks": checks,
    }
    result["ok"] = all(bool(check.get("ok")) for check in checks)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
