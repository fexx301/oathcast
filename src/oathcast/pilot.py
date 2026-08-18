"""Local OathCast planning-pilot intake surface.

The pilot deliberately records planning questions without calling a Miner,
Telegraph, or a payment endpoint.  It gives prospective users a small,
privacy-minimal intake form and leaves queued questions ready for a later
official Telegraph/payment adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import logging
import os
import sqlite3
import threading
from typing import Any, Callable
from urllib.parse import parse_qs

from oathcast.forecast import ForecastQuestion, format_timestamp, parse_timestamp


UTC = timezone.utc
PILOT_VERSION = "planning_pilot_intake_v1"
PILOT_STATUS = "local_intake_only_no_telegraph_calls"
LOGGER = logging.getLogger(__name__)


class PilotValidationError(ValueError):
    """Raised when a pilot request cannot become a valid ForecastQuestion."""


def _text(value: Any, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise PilotValidationError(f"{field} must be text")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise PilotValidationError(f"{field} is required")
    if len(cleaned) > max_chars:
        raise PilotValidationError(f"{field} exceeds {max_chars} characters")
    return cleaned


def _canonical_request_payload(
    *,
    location_name: str,
    latitude: float,
    longitude: float,
    forecast_cutoff: str,
    horizon_start: str,
    horizon_end: str,
    use_case: str,
) -> dict[str, Any]:
    return {
        "location_name": location_name,
        "latitude": latitude,
        "longitude": longitude,
        "forecast_cutoff": forecast_cutoff,
        "horizon_start": horizon_start,
        "horizon_end": horizon_end,
        "threshold_mm": 0.1,
        "use_case": use_case,
    }


@dataclass(frozen=True)
class PilotPlan:
    request_id: str
    question: ForecastQuestion
    use_case: str
    submitted_at: datetime
    status: str = "queued"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "question": self.question.to_dict(),
            "use_case": self.use_case,
            "submitted_at": format_timestamp(self.submitted_at),
            "status": self.status,
            "pilot_version": PILOT_VERSION,
            "pilot_status": PILOT_STATUS,
        }


def build_pilot_plan(
    payload: dict[str, Any],
    *,
    submitted_at: datetime | None = None,
) -> PilotPlan:
    """Validate an intake payload and derive a stable request/event identity."""

    try:
        location_name = _text(payload.get("location_name"), "location_name", max_chars=96)
        use_case = _text(payload.get("use_case"), "use_case", max_chars=240)
        latitude = float(payload["latitude"])
        longitude = float(payload["longitude"])
        forecast_cutoff = parse_timestamp(payload["forecast_cutoff"])
        horizon_start = parse_timestamp(payload["horizon_start"])
        horizon_end = parse_timestamp(payload["horizon_end"])
    except PilotValidationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PilotValidationError("location, use case, coordinates, and UTC times are required") from exc

    canonical = _canonical_request_payload(
        location_name=location_name,
        latitude=latitude,
        longitude=longitude,
        forecast_cutoff=format_timestamp(forecast_cutoff),
        horizon_start=format_timestamp(horizon_start),
        horizon_end=format_timestamp(horizon_end),
        use_case=use_case,
    )
    request_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    event_id = f"pilot-{request_hash[:24]}"
    try:
        question = ForecastQuestion(
            event_id=event_id,
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            forecast_cutoff=forecast_cutoff,
        )
    except ValueError as exc:
        raise PilotValidationError(str(exc)) from exc
    submitted = parse_timestamp(submitted_at or datetime.now(tz=UTC))
    return PilotPlan(
        request_id=f"pilot-request-{request_hash[:20]}",
        question=question,
        use_case=use_case,
        submitted_at=submitted,
    )


class PilotIntakeStore:
    """Durable, idempotent local queue for prospective pilot questions."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pilot_requests (
                    request_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'routed', 'closed'))
                )
                """
            )
            connection.commit()
        finally:
            if self._memory_connection is None:
                connection.close()

    def _connection(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        return sqlite3.connect(self.path, timeout=10)

    def close(self) -> None:
        with self._lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def save(self, plan: PilotPlan) -> dict[str, Any]:
        payload = plan.to_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._lock:
            connection = self._connection()
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO pilot_requests
                        (request_id, event_id, request_json, request_sha256, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.request_id,
                        plan.question.event_id,
                        encoded,
                        digest,
                        format_timestamp(plan.submitted_at),
                        plan.status,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT request_json, request_sha256, status FROM pilot_requests WHERE request_id = ?",
                    (plan.request_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("pilot request was not persisted")
                stored = json.loads(row[0])
                stored["status"] = row[2]
                stored["request_sha256"] = row[1]
                return stored
            finally:
                if self._memory_connection is None:
                    connection.close()

    def list_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connection()
            try:
                if status is None:
                    rows = connection.execute(
                        "SELECT request_json, status, request_sha256 FROM pilot_requests ORDER BY created_at, request_id"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT request_json, status, request_sha256 FROM pilot_requests WHERE status = ? ORDER BY created_at, request_id",
                        (status,),
                    ).fetchall()
                records = []
                for request_json, row_status, digest in rows:
                    record = json.loads(request_json)
                    record["status"] = row_status
                    record["request_sha256"] = digest
                    records.append(record)
                return records
            finally:
                if self._memory_connection is None:
                    connection.close()


def render_pilot_html() -> str:
    """Return the local pilot's privacy-minimal intake page."""

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OathCast Planning Desk</title>
  <style>
    :root { color-scheme: dark; --ink:#f3f4f6; --muted:#9ca3af; --line:#30343b; --gold:#f7bd2b; --panel:#111317; --green:#71e2a5; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:#08090b; color:var(--ink); font:16px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
    main { width:min(920px, calc(100% - 32px)); margin:0 auto; padding:56px 0 72px; }
    .eyebrow { color:var(--gold); letter-spacing:.16em; text-transform:uppercase; font-size:12px; }
    h1 { max-width:760px; font:700 clamp(34px, 7vw, 76px)/.98 Georgia, serif; letter-spacing:-.045em; margin:18px 0; }
    .lede { max-width:650px; color:var(--muted); margin-bottom:34px; }
    .notice { border:1px solid #75591b; background:#171207; color:#f2d487; padding:14px 16px; margin:22px 0 28px; }
    form { border:1px solid var(--line); background:var(--panel); padding:22px; display:grid; gap:18px; }
    label { display:grid; gap:8px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
    input, textarea { width:100%; border:1px solid var(--line); background:#090a0d; color:var(--ink); padding:12px; font:inherit; }
    textarea { min-height:80px; resize:vertical; }
    .grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px; }
    .grid.three { grid-template-columns:repeat(3, minmax(0, 1fr)); }
    button { border:0; background:var(--gold); color:#17120a; padding:14px 18px; font:700 15px inherit; cursor:pointer; }
    #result { min-height:28px; color:var(--green); white-space:pre-wrap; }
    .small { color:var(--muted); font-size:12px; }
    @media (max-width:680px) { .grid, .grid.three { grid-template-columns:1fr; } main { padding-top:32px; } }
  </style>
</head>
<body>
<main>
  <div class="eyebrow">OathCast / Planning Desk</div>
  <h1>Make the forecast answer a real planning question.</h1>
  <p class="lede">Submit one time-locked outdoor planning brief. This local pilot queues the question for review and future Telegraph routing; it does not contact Miners, spend funds, or count as hackathon traffic.</p>
  <div class="notice"><strong>Preparation mode.</strong> No personal contact details are collected. Use a concrete activity, location, and UTC window.</div>
  <form id="pilot-form">
    <label>What are you planning?
      <textarea name="use_case" required maxlength="240" placeholder="e.g. Decide whether to move a Saturday market setup indoors."></textarea>
    </label>
    <div class="grid">
      <label>Location name<input name="location_name" required maxlength="96" placeholder="Lagos"></label>
      <label>Latitude<input name="latitude" required inputmode="decimal" placeholder="6.5244"></label>
    </div>
    <div class="grid three">
      <label>Longitude<input name="longitude" required inputmode="decimal" placeholder="3.3792"></label>
      <label>Forecast cutoff (UTC)<input name="forecast_cutoff" required placeholder="2026-08-17T12:00:00Z"></label>
      <label>Window start (UTC)<input name="horizon_start" required placeholder="2026-08-17T15:00:00Z"></label>
    </div>
    <label>Window end (UTC)<input name="horizon_end" required placeholder="2026-08-17T16:00:00Z"></label>
    <button type="submit">Queue planning brief</button>
    <div id="result" role="status"></div>
    <div class="small">Supported event: measurable precipitation &gt; 0.1 mm over one exact UTC hour.</div>
  </form>
</main>
<script>
const form = document.querySelector('#pilot-form');
const result = document.querySelector('#result');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  result.textContent = 'Validating and queueing…';
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const response = await fetch('/api/pilot-requests', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'request rejected');
    result.textContent = `Queued: ${body.request_id}\nNo Telegraph call was made. The brief is ready for a later paid routing flow.`;
    form.reset();
  } catch (error) {
    result.textContent = `Not queued: ${error.message}`;
  }
});
</script>
</body>
</html>"""


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def make_pilot_handler(store: PilotIntakeStore) -> type[BaseHTTPRequestHandler]:
    """Build a handler bound to one intake store for the local server."""

    class PilotHandler(BaseHTTPRequestHandler):
        server_version = "OathCastPilot/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/":
                encoded = render_pilot_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if self.path == "/api/healthz":
                _json_response(
                    self,
                    200,
                    {
                        "status": "ok",
                        "pilot_version": PILOT_VERSION,
                        "mode": PILOT_STATUS,
                        "qualifying_traffic": False,
                    },
                )
                return
            if self.path == "/api/pilot-requests":
                _json_response(self, 200, {"requests": store.list_requests(status="queued")})
                return
            _json_response(self, 404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/api/pilot-requests":
                _json_response(self, 404, {"error": "not_found"})
                return
            try:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except (TypeError, ValueError) as exc:
                    raise PilotValidationError(
                        "Content-Length must be a base-10 integer"
                    ) from exc
                if length <= 0 or length > 16_384:
                    raise PilotValidationError("request body must be between 1 and 16384 bytes")
                raw = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    payload = json.loads(raw.decode("utf-8"))
                else:
                    form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                    duplicate_fields = sorted(
                        key for key, values in form.items() if len(values) != 1
                    )
                    if duplicate_fields:
                        raise PilotValidationError(
                            "form fields must not be repeated: "
                            + ", ".join(duplicate_fields)
                        )
                    payload = {key: values[0] for key, values in form.items()}
                if not isinstance(payload, dict):
                    raise PilotValidationError("request body must be an object")
                plan = build_pilot_plan(payload)
                record = store.save(plan)
                _json_response(
                    self,
                    201,
                    {
                        "request_id": record["request_id"],
                        "event_id": record["question"]["event_id"],
                        "status": record["status"],
                        "pilot_status": PILOT_STATUS,
                        "qualifying_traffic": False,
                        "next_step": "Review, then route through official Telegraph payment flow when available.",
                    },
                )
            except (PilotValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception:
                LOGGER.exception("pilot request storage failed")
                _json_response(self, 500, {"error": "pilot_store_error"})

    return PilotHandler


def serve_pilot(
    *,
    host: str = "127.0.0.1",
    port: int = 8788,
    database: str | os.PathLike[str] = "state/pilot.sqlite3",
    server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServer,
) -> None:
    store = PilotIntakeStore(database)
    server = server_factory((host, port), make_pilot_handler(store))
    try:
        print(f"OathCast Planning Desk listening at http://{host}:{port}/")
        server.serve_forever()
    finally:
        server.server_close()
        store.close()
