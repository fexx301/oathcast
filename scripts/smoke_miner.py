#!/usr/bin/env python3
"""Run a non-destructive public Miner release and auth smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from oathcast.artifacts import atomic_write_text
from oathcast.forecast import format_timestamp
from oathcast.protocol import outbound_headers


ROOT = Path(__file__).resolve().parents[1]
REGISTERED_FORECAST_PATH = "/predict"
CANONICAL_FORECAST_PATH = "/v1/forecast/point"
MAX_RESPONSE_BODY_BYTES = 2 * 1024 * 1024

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


def release_identity_from_evidence(path: Path) -> dict[str, str]:
    """Load a deployment identity and verify its checked-in evidence agrees."""

    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read release evidence {path}: {exc}") from exc
    if not isinstance(evidence, dict):
        raise ValueError("release evidence must be a JSON object")

    release_id = evidence.get("release_id")
    source_sha256 = evidence.get("source_sha256")
    image = evidence.get("image")
    image_digest = image.get("image_digest") if isinstance(image, dict) else None
    identity = {
        "release_id": release_id,
        "source_sha256": source_sha256,
        "image_digest": image_digest,
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise ValueError(
            "release evidence must contain release_id, source_sha256, and "
            "image.image_digest"
        )

    source_verification = evidence.get("source_verification")
    labels = image.get("labels") if isinstance(image, dict) else None
    if not isinstance(source_verification, dict) or not isinstance(labels, dict):
        raise ValueError("release evidence is missing source verification or image labels")
    consistency_checks = {
        "source_verification.expected_source_sha256": source_verification.get(
            "expected_source_sha256"
        ),
        "source_verification.host_recomputed_source_sha256": source_verification.get(
            "host_recomputed_source_sha256"
        ),
        "image.image_id": image.get("image_id"),
        "image.labels.org.opencontainers.image.version": labels.get(
            "org.opencontainers.image.version"
        ),
        "image.labels.org.opencontainers.image.revision": labels.get(
            "org.opencontainers.image.revision"
        ),
    }
    expected_values = {
        "source_verification.expected_source_sha256": source_sha256,
        "source_verification.host_recomputed_source_sha256": source_sha256,
        "image.image_id": image_digest,
        "image.labels.org.opencontainers.image.version": release_id,
        "image.labels.org.opencontainers.image.revision": source_sha256,
    }
    for field, actual in consistency_checks.items():
        if actual != expected_values[field]:
            raise ValueError(f"release evidence identity mismatch at {field}")
    if source_verification.get("verified") is not True:
        raise ValueError("release evidence source verification is not marked verified")

    linked_evidence = evidence.get("evidence")
    if not isinstance(linked_evidence, dict):
        raise ValueError("release evidence does not link its manifest and public smoke")
    manifest_ref = source_verification.get("manifest_path")
    if manifest_ref != linked_evidence.get("manifest"):
        raise ValueError("release evidence manifest references disagree")
    linked_identities: tuple[tuple[object, tuple[str, ...]], ...] = (
        (manifest_ref, ("release_id", "source_sha256")),
        (
            linked_evidence.get("public_smoke"),
            (
                "release.release_id",
                "release.source_sha256",
                "release.image_digest",
            ),
        ),
    )
    for reference, fields in linked_identities:
        if not isinstance(reference, str) or not reference:
            raise ValueError("release evidence contains an invalid linked evidence path")
        linked_path = Path(reference)
        if not linked_path.is_absolute():
            linked_path = ROOT / linked_path
        linked_path = linked_path.resolve()
        try:
            linked_path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise ValueError(
                f"linked release evidence escapes the repository: {reference}"
            ) from exc
        try:
            linked = json.loads(linked_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read linked release evidence {reference}: {exc}") from exc
        if not isinstance(linked, dict):
            raise ValueError(f"linked release evidence {reference} must be an object")
        for field in fields:
            actual: object = linked
            for part in field.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            expected = identity[field.rsplit(".", 1)[-1]]
            if actual != expected:
                raise ValueError(
                    f"linked release evidence identity mismatch at {reference}:{field}"
                )

    return identity


def valid_forecast_response(value: object) -> bool:
    """Require the response fields the dispatcher needs to extract an answer."""

    if not isinstance(value, dict):
        return False
    content = value.get("content")
    probability = value.get("probability")
    return (
        isinstance(content, str)
        and bool(content.strip())
        and isinstance(probability, (int, float))
        and not isinstance(probability, bool)
        and 0 <= probability <= 1
        and math.isfinite(probability)
    )


def valid_temperature_window_response(
    value: object,
    *,
    expected_hours: int = 24,
    expected_reference_times: set[datetime] | None = None,
) -> bool:
    """Validate the deployed additive ``forecast_hours/hourly=2t`` envelope."""

    if not isinstance(value, dict) or set(value) != {
        "content",
        "reference_time",
        "hourly",
        "hourly_units",
    }:
        return False
    if not isinstance(value["content"], str) or not value["content"].strip():
        return False
    reference_time = value["reference_time"]
    if not isinstance(reference_time, str):
        return False
    try:
        reference = datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    if reference.tzinfo is None or reference.utcoffset() != timedelta(0):
        return False
    if reference.minute or reference.second or reference.microsecond:
        return False
    if (
        expected_reference_times is not None
        and reference not in expected_reference_times
    ):
        return False

    hourly = value["hourly"]
    units = value["hourly_units"]
    if not isinstance(hourly, dict) or set(hourly) != {"time", "2t"}:
        return False
    if units != {"time": "iso8601", "2t": "K"}:
        return False
    times = hourly["time"]
    temperatures = hourly["2t"]
    if (
        not isinstance(times, list)
        or not isinstance(temperatures, list)
        or len(times) != expected_hours
        or len(temperatures) != expected_hours
    ):
        return False
    parsed_times: list[datetime] = []
    for value_time in times:
        if not isinstance(value_time, str):
            return False
        try:
            parsed = datetime.fromisoformat(value_time.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            return False
        if parsed.minute or parsed.second or parsed.microsecond:
            return False
        parsed_times.append(parsed)
    if not parsed_times or parsed_times[0] != reference + timedelta(hours=1):
        return False
    if any(
        current != previous + timedelta(hours=1)
        for previous, current in zip(parsed_times, parsed_times[1:])
    ):
        return False
    return all(
        isinstance(temperature, (int, float))
        and not isinstance(temperature, bool)
        and math.isfinite(temperature)
        and temperature > 0
        for temperature in temperatures
    )


def temperature_smoke_event_id(moment: datetime) -> str:
    """Return one canary receipt identity per UTC hour."""

    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("temperature smoke time must be timezone-aware")
    utc_hour = moment.astimezone(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return f"smoke-temperature-{utc_hour:%Y%m%dT%H}z"


def _read_response_body(response: object, *, max_body_bytes: int) -> bytes:
    """Read an HTTP body with the same bounded-read invariant as the service."""

    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    response_headers = getattr(response, "headers", {})
    declared = response_headers.get("Content-Length") if response_headers else None
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError):
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > max_body_bytes:
            raise ValueError(f"response exceeds {max_body_bytes} byte cap")
    body = response.read(max_body_bytes + 1)
    if len(body) > max_body_bytes:
        raise ValueError(f"response exceeds {max_body_bytes} byte cap")
    return body


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_body_bytes: int = MAX_RESPONSE_BODY_BYTES,
) -> tuple[int, dict[str, str], object]:
    request = Request(url, headers=outbound_headers(headers))
    try:
        with urlopen(request, timeout=20) as response:
            body = _read_response_body(response, max_body_bytes=max_body_bytes).decode(
                "utf-8"
            )
            return response.status, dict(response.headers.items()), json.loads(body)
    except HTTPError as exc:
        body = _read_response_body(exc, max_body_bytes=max_body_bytes).decode(
            "utf-8", errors="replace"
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"body": body[:500]}
        return exc.code, dict(exc.headers.items()), payload
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


def release_identity_checks(
    release: object,
    expected_identity: dict[str, str | None],
) -> list[dict[str, object]]:
    """Return explicit pass/fail records for every configured release pin."""

    release_fields = release if isinstance(release, dict) else {}
    actual_identity = {
        "release_id": release_fields.get("release_id"),
        "source_sha256": release_fields.get("source_sha256"),
        "image_digest": release_fields.get("image_digest"),
    }
    return [
        {
            "name": name,
            "expected": expected,
            "actual": actual_identity[name],
            "ok": actual_identity[name] == expected,
        }
        for name, expected in expected_identity.items()
        if expected is not None
    ]


def skipped_authenticated_checks(
    *,
    require_temperature_window: bool,
) -> list[dict[str, object]]:
    """Make a public-only smoke visibly partial instead of silently incomplete."""

    names = ["authenticated_forecast", "canonical_path_parity"]
    if require_temperature_window:
        names.extend(
            ["authenticated_temperature_window", "temperature_path_parity"]
        )
    return [
        {
            "name": name,
            "ok": True,
            "skipped": True,
            "reason": "--skip-authenticated",
        }
        for name in names
    ]


def receipt_capacity_check(
    ready: object,
    *,
    min_headroom_percent: float,
    required: bool = False,
) -> dict[str, object]:
    """Alert on a filling receipt store *before* it starts refusing forecasts.

    A full store makes every new forecast return 507, so the useful signal is
    headroom, not the cliff itself. Whichever caps are configured are checked;
    an uncapped store trivially passes.

    A general-purpose smoke can still inspect an older release without failing
    on an absent field. Production canaries opt into ``required=True`` so a
    missing or malformed capacity report cannot be recorded as healthy.
    """

    check: dict[str, object] = {
        "name": "receipt_capacity",
        "ok": not required,
        "required": required,
        "reported": False,
    }
    if not isinstance(ready, dict):
        check["error"] = "readyz payload is not an object"
        return check
    capacity = ready.get("receipt_store")
    if not isinstance(capacity, dict):
        check["error"] = "readyz does not report receipt_store capacity"
        return check

    check["reported"] = True
    required_fields = {
        "rows",
        "max_rows",
        "used_bytes",
        "max_bytes",
        "accepting_new_receipts",
    }
    missing_fields = sorted(required_fields - set(capacity))
    if missing_fields:
        check["ok"] = False
        check["error"] = (
            "receipt_store capacity report is missing: " + ", ".join(missing_fields)
        )
        return check

    accepting = capacity.get("accepting_new_receipts")
    if not isinstance(accepting, bool):
        check["ok"] = False
        check["error"] = "receipt_store accepting_new_receipts must be boolean"
        return check
    check["accepting_new_receipts"] = accepting
    headrooms: list[float] = []
    for used_key, max_key, label in (
        ("rows", "max_rows", "rows"),
        ("used_bytes", "max_bytes", "bytes"),
    ):
        limit = capacity.get(max_key)
        used = capacity.get(used_key)
        if (
            not isinstance(used, int)
            or isinstance(used, bool)
            or used < 0
            or used > 2**63 - 1
        ):
            check["ok"] = False
            check["error"] = (
                f"receipt_store {used_key} must be a non-negative 64-bit integer"
            )
            return check
        if limit is None:
            continue
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit <= 0
            or limit > 2**63 - 1
        ):
            check["ok"] = False
            check["error"] = (
                f"receipt_store {max_key} must be a positive 64-bit integer or null"
            )
            return check
        headroom = max(0.0, (limit - used) / limit) * 100.0
        check[f"{label}_headroom_percent"] = round(headroom, 3)
        headrooms.append(headroom)

    check["ok"] = True
    if accepting is False:
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
        "--release-evidence",
        type=Path,
        help=(
            "load the expected release ID, source SHA-256, and image digest from "
            "a checked-in runtime evidence file"
        ),
    )
    parser.add_argument(
        "--require-receipt-capacity",
        action="store_true",
        help="fail unless /readyz reports a valid receipt-store capacity envelope",
    )
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
    parser.add_argument(
        "--require-temperature-window",
        action="store_true",
        help="also require the deployed 24-hour forecast_hours/hourly=2t response",
    )
    args = parser.parse_args()
    expected_identity = {
        "release_id": args.expected_release_id,
        "source_sha256": args.expected_source_sha256,
        "image_digest": args.expected_image_digest,
    }
    if args.release_evidence is not None:
        try:
            evidence_identity = release_identity_from_evidence(args.release_evidence)
        except ValueError as exc:
            parser.error(str(exc))
        for field, evidence_value in evidence_identity.items():
            explicit_value = expected_identity[field]
            if explicit_value is not None and explicit_value != evidence_value:
                parser.error(
                    f"--expected-{field.replace('_', '-')} conflicts with "
                    f"--release-evidence"
                )
            expected_identity[field] = evidence_value
    base_url = args.base_url.rstrip("/")
    checks: list[dict[str, object]] = []

    health_status, health_headers, health = request_json(f"{base_url}/healthz")
    health_ok = health_status == 200 and isinstance(health, dict) and health.get("ok") is True
    checks.append({"name": "healthz", "status": health_status, "ok": health_ok})
    release_id = health.get("release", {}).get("release_id") if isinstance(health, dict) else None
    release = health.get("release", {}) if isinstance(health, dict) else {}
    checks.extend(release_identity_checks(release, expected_identity))

    ready_status, _, ready = request_json(f"{base_url}/readyz")
    checks.append({"name": "readyz", "status": ready_status, "ok": ready_status == 200 and isinstance(ready, dict) and ready.get("ready") is True})
    checks.append(
        receipt_capacity_check(
            ready,
            min_headroom_percent=args.min_receipt_headroom_percent,
            required=args.require_receipt_capacity,
        )
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
        atomic_write_text(
            args.question_output,
            json.dumps(question, indent=2, sort_keys=True) + "\n",
        )

    params = {
        "event_id": question["event_id"],
        "location_name": question["location_name"],
        "lat": f"{question['latitude']:.6f}",
        "lon": f"{question['longitude']:.6f}",
        "start": horizon_start,
        "end": horizon_end,
        "cutoff": forecast_cutoff,
        "threshold_mm": str(question["threshold_mm"]),
    }
    registered_forecast_url = (
        f"{base_url}{REGISTERED_FORECAST_PATH}?{urlencode(params)}"
    )
    canonical_forecast_url = (
        f"{base_url}{CANONICAL_FORECAST_PATH}?{urlencode(params)}"
    )
    unauthorized_status, _, _ = request_json(registered_forecast_url)
    checks.append(
        {
            "name": "unauthorized_rejected",
            "path": REGISTERED_FORECAST_PATH,
            "status": unauthorized_status,
            "ok": unauthorized_status == 401,
        }
    )

    if not args.skip_authenticated:
        token = os.getenv(args.token_env)
        if not token:
            checks.append({"name": "authenticated_forecast", "ok": False, "error": f"{args.token_env} is not set"})
        else:
            forecast_status, forecast_headers, forecast = request_json(
                registered_forecast_url,
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
                and valid_forecast_response(forecast)
                and bool(receipt_sha256)
                and bool(response_request_id)
            )
            forecast_check: dict[str, object] = {
                "name": "authenticated_forecast",
                "path": REGISTERED_FORECAST_PATH,
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

            canonical_status, canonical_headers, canonical_forecast = request_json(
                canonical_forecast_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-ID": "smoke-release-canonical",
                },
            )
            canonical_receipt_sha256 = header_value(
                canonical_headers, "X-OathCast-Receipt-SHA256"
            )
            checks.append(
                {
                    "name": "canonical_path_parity",
                    "registered_path": REGISTERED_FORECAST_PATH,
                    "canonical_path": CANONICAL_FORECAST_PATH,
                    "status": canonical_status,
                    "ok": (
                        response_ok
                        and canonical_status == 200
                        and canonical_forecast == forecast
                        and bool(canonical_receipt_sha256)
                        and canonical_receipt_sha256 == receipt_sha256
                    ),
                    "receipt_sha256": canonical_receipt_sha256,
                    "public_response_sha256": (
                        json_sha256(canonical_forecast)
                        if isinstance(canonical_forecast, dict)
                        else None
                    ),
                }
            )

            if args.require_temperature_window:
                started_at = datetime.now(timezone.utc)
                expected_reference_time = started_at.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                temperature_event_id = temperature_smoke_event_id(started_at)
                temperature_params = {
                    "event_id": temperature_event_id,
                    "location_name": question["location_name"],
                    "lat": f"{question['latitude']:.6f}",
                    "lon": f"{question['longitude']:.6f}",
                    "forecast_hours": "24",
                    "hourly": "2t",
                }
                temperature_url = (
                    f"{base_url}{REGISTERED_FORECAST_PATH}?"
                    f"{urlencode(temperature_params)}"
                )
                temperature_status, temperature_headers, temperature = request_json(
                    temperature_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Request-ID": "smoke-release-temperature",
                    },
                )
                temperature_receipt = header_value(
                    temperature_headers,
                    "X-OathCast-Receipt-SHA256",
                )
                temperature_request_id = header_value(
                    temperature_headers,
                    "X-OathCast-Request-ID",
                )
                temperature_ok = (
                    temperature_status == 200
                    and valid_temperature_window_response(
                        temperature,
                        expected_reference_times={expected_reference_time},
                    )
                    and bool(temperature_receipt)
                    and bool(temperature_request_id)
                )
                temperature_check: dict[str, object] = {
                    "name": "authenticated_temperature_window",
                    "path": REGISTERED_FORECAST_PATH,
                    "status": temperature_status,
                    "ok": temperature_ok,
                    "event_id": temperature_event_id,
                    "expected_reference_time": format_timestamp(
                        expected_reference_time
                    ),
                    "forecast_hours": 24,
                    "hourly": "2t",
                }
                if temperature_receipt:
                    temperature_check["receipt_sha256"] = temperature_receipt
                if temperature_request_id:
                    temperature_check["request_id"] = temperature_request_id
                if isinstance(temperature, dict):
                    temperature_check["public_response_sha256"] = json_sha256(
                        temperature
                    )
                checks.append(temperature_check)

                canonical_temperature_url = (
                    f"{base_url}{CANONICAL_FORECAST_PATH}?"
                    f"{urlencode(temperature_params)}"
                )
                canonical_temperature_status, canonical_temperature_headers, canonical_temperature = request_json(
                    canonical_temperature_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Request-ID": "smoke-release-temperature-canonical",
                    },
                )
                canonical_temperature_receipt = header_value(
                    canonical_temperature_headers,
                    "X-OathCast-Receipt-SHA256",
                )
                checks.append(
                    {
                        "name": "temperature_path_parity",
                        "registered_path": REGISTERED_FORECAST_PATH,
                        "canonical_path": CANONICAL_FORECAST_PATH,
                        "status": canonical_temperature_status,
                        "ok": (
                            temperature_ok
                            and canonical_temperature_status == 200
                            and canonical_temperature == temperature
                            and bool(canonical_temperature_receipt)
                            and canonical_temperature_receipt == temperature_receipt
                            and valid_temperature_window_response(
                                canonical_temperature,
                                expected_reference_times={expected_reference_time},
                            )
                        ),
                        "receipt_sha256": canonical_temperature_receipt,
                        "public_response_sha256": (
                            json_sha256(canonical_temperature)
                            if isinstance(canonical_temperature, dict)
                            else None
                        ),
                    }
                )
    else:
        # Keep a green public-only run visibly partial; omitted checks must not
        # be mistaken for checks that passed.
        checks.extend(
            skipped_authenticated_checks(
                require_temperature_window=args.require_temperature_window,
            )
        )

    result = {
        "base_url": base_url,
        "release_id": release_id,
        "release": release,
        "question": question,
        "checks": checks,
        "authenticated_checks_skipped": args.skip_authenticated,
        "partial": args.skip_authenticated,
    }
    result["ok"] = all(bool(check.get("ok")) for check in checks)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
