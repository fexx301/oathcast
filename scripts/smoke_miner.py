#!/usr/bin/env python3
"""Run a non-destructive public Miner release and auth smoke test."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def header_value(headers: dict[str, str], name: str) -> str | None:
    """Read an HTTP header without depending on proxy casing."""

    wanted = name.lower()
    return next((value for key, value in headers.items() if key.lower() == wanted), None)


def request_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], object]:
    request = Request(url, headers={"Accept": "application/json", **(headers or {})})
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("OATHCAST_PUBLIC_URL", "https://oathcastcourt.duckdns.org"))
    parser.add_argument("--token-env", default="OATHCAST_MINER_API_KEY")
    parser.add_argument("--expected-release-id")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--expected-image-digest")
    parser.add_argument("--question-file", type=Path, default=ROOT / "fixtures" / "question.json")
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

    question = json.loads(args.question_file.read_text(encoding="utf-8"))
    params = {
        "event_id": question["event_id"],
        "location_name": question["location_name"],
        "lat": f"{question['latitude']:.6f}",
        "lon": f"{question['longitude']:.6f}",
        "horizon_start": question["horizon_start"],
        "horizon_end": question["horizon_end"],
        "forecast_cutoff": question["forecast_cutoff"],
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
            response_ok = (
                forecast_status == 200
                and isinstance(forecast, dict)
                and isinstance(forecast.get("content"), str)
                and bool(header_value(forecast_headers, "X-OathCast-Receipt-SHA256"))
                and bool(header_value(forecast_headers, "X-OathCast-Request-ID"))
            )
            checks.append({"name": "authenticated_forecast", "status": forecast_status, "ok": response_ok})

    result = {"base_url": base_url, "release_id": release_id, "checks": checks}
    result["ok"] = all(bool(check.get("ok")) for check in checks)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
