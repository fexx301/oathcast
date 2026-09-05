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
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from oathcast.forecast import ForecastQuestion, format_timestamp, parse_timestamp


UTC = timezone.utc
PILOT_VERSION = "planning_pilot_intake_v1"
PILOT_STATUS = "local_intake_only_no_telegraph_calls"
LOGGER = logging.getLogger(__name__)
PILOT_LOGO_PATH = "/assets/oathcast-mark.webp"
PILOT_LOGO_VERSION = "16fae356"
PILOT_LOGO_FILE = Path(__file__).with_name("assets") / "oathcast-mark.webp"
try:
    _PILOT_LOGO_BYTES = PILOT_LOGO_FILE.read_bytes()
except OSError:
    _PILOT_LOGO_BYTES = None


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

    html = """<!doctype html>
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

    /* OathCast Planning Desk system: one clear path, with the safety boundary visible. */
    :root {
      color-scheme: dark;
      --page: #080a0d;
      --surface: #101419;
      --surface-raised: #151b22;
      --surface-soft: #0c1014;
      --ink: #f4f7fa;
      --muted: #aab5c1;
      --muted-strong: #d1d9e1;
      --line: #29333d;
      --line-strong: #43515e;
      --accent: #f0525f;
      --accent-bright: #ff707c;
      --accent-soft: #2b0e13;
      --positive: #85e7b5;
      --positive-soft: #0d2a20;
      --caution: #f6cb69;
      --caution-soft: #2b210d;
      --danger: #ff9aa2;
      --danger-soft: #321219;
      --focus: #9ad8ff;
      --shadow: rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; scroll-padding-top: 1.5rem; }
    body { min-height: 100dvh; background: var(--page); color: var(--ink); font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body::before { content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none; background-image: linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px); background-size: 48px 48px; mask-image: linear-gradient(to bottom, black 0, transparent 52rem); }
    a { color: var(--ink); text-decoration-color: var(--accent); text-underline-offset: .24em; }
    button, input, textarea { font: inherit; }
    button, a { -webkit-tap-highlight-color: transparent; touch-action: manipulation; }
    main.page-shell { width: min(100% - 2rem, 1180px); margin: 0 auto; padding: 1rem 0 4rem; }
    .skip-link { position: fixed; top: .75rem; left: .75rem; z-index: 10; transform: translateY(-5rem); border: 1px solid var(--accent); border-radius: .35rem; background: var(--surface); padding: .7rem .9rem; font-weight: 800; }
    .skip-link:focus { transform: translateY(0); }
    .site-header { min-height: 4.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; border-bottom: 1px solid var(--line); }
    .brand { display: inline-flex; align-items: center; gap: .7rem; color: var(--ink); text-decoration: none; }
    .brand-mark { width: 2.75rem; height: 2.75rem; object-fit: contain; filter: drop-shadow(0 0 .6rem rgba(240, 82, 95, .16)); }
    .brand-copy { display: grid; gap: .05rem; }
    .brand-name { line-height: 1; font-weight: 850; }
    .brand-subtitle { color: var(--muted); font-size: .7rem; font-weight: 650; letter-spacing: .12em; text-transform: uppercase; }
    .site-nav { display: flex; align-items: center; gap: .35rem; }
    .site-nav a { display: inline-flex; min-height: 2.75rem; align-items: center; border-radius: .35rem; padding: .55rem .75rem; color: var(--muted-strong); font-size: .88rem; font-weight: 720; text-decoration: none; }
    .site-nav a:hover { background: var(--surface); color: var(--ink); }
    .site-nav .nav-status { border: 1px solid var(--line-strong); color: var(--ink); }
    .eyebrow { display: flex; align-items: center; gap: .55rem; margin: 0 0 .65rem; color: var(--muted-strong); font-size: .76rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
    .status-dot { display: inline-block; width: .56rem; height: .56rem; flex: 0 0 auto; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 .28rem rgba(240, 82, 95, .12); }
    .status-dot.positive { background: var(--positive); box-shadow: 0 0 0 .28rem rgba(133, 231, 181, .12); }
    .status-pill { display: inline-flex; align-items: center; gap: .5rem; min-height: 2.1rem; border: 1px solid #826322; border-radius: 999px; background: var(--caution-soft); color: var(--caution); padding: .35rem .72rem; font-size: .78rem; font-weight: 800; }
    .hero { display: grid; gap: clamp(2rem, 6vw, 5rem); grid-template-columns: minmax(0, 1.2fr) minmax(18rem, .8fr); align-items: center; padding: clamp(3rem, 7vw, 6rem) 0 clamp(2.5rem, 6vw, 5rem); border-bottom: 1px solid var(--line); }
    h1, h2, h3 { line-height: 1.12; letter-spacing: -.025em; }
    h1 { max-width: 15ch; margin: 1rem 0 1.35rem; font: clamp(3.15rem, 8vw, 6.2rem)/.98 Georgia, "Times New Roman", serif; text-wrap: balance; }
    h2 { margin: 0 0 .75rem; font-size: clamp(1.8rem, 4vw, 3rem); text-wrap: balance; }
    h3 { margin: 0; font-size: 1.15rem; }
    p { max-width: 70ch; }
    .lede { margin: 0; color: var(--muted); font-size: clamp(1.05rem, 1.7vw, 1.24rem); }
    .hero-actions { display: flex; align-items: center; flex-wrap: wrap; gap: .75rem; margin-top: 1.65rem; }
    .button, button { display: inline-flex; min-height: 3rem; width: auto; align-items: center; justify-content: center; border: 1px solid var(--accent); border-radius: .35rem; background: var(--accent); color: #17070a; cursor: pointer; padding: .72rem 1rem; font-weight: 800; text-decoration: none; transition: background-color .16s ease, border-color .16s ease, color .16s ease, transform .16s ease; }
    .button:hover, button:hover { border-color: var(--accent-bright); background: var(--accent-bright); }
    .button:active, button:active { transform: translateY(1px); }
    .text-link { display: inline-flex; min-height: 3rem; align-items: center; padding: .72rem .35rem; color: var(--muted-strong); font-weight: 760; }
    .hero-note { display: flex; align-items: center; gap: .65rem; margin: 1.35rem 0 0; color: var(--muted); font-size: .88rem; }
    .pilot-status { border: 1px solid var(--line-strong); border-top: .25rem solid var(--positive); border-radius: .5rem; background: var(--surface); padding: clamp(1.25rem, 3vw, 2rem); box-shadow: 0 1.75rem 4rem var(--shadow); }
    .pilot-status h2 { margin-top: .85rem; font-size: clamp(1.65rem, 3vw, 2.25rem); }
    .pilot-status p { color: var(--muted-strong); }
    .status-list { display: grid; gap: .7rem; margin: 1.45rem 0 0; padding: 0; list-style: none; }
    .status-list li { display: grid; gap: .1rem; border-top: 1px solid var(--line); padding-top: .7rem; }
    .status-list strong { font-size: .86rem; }
    .status-list span { color: var(--muted); font-size: .88rem; }
    .section { scroll-margin-top: 1.5rem; }
    .section-heading { display: grid; gap: .2rem; max-width: 760px; }
    .section-heading > p:last-child { margin-top: .3rem; color: var(--muted); }
    .progress { display: grid; gap: 1rem; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 1.5rem 0 0; padding: 0; list-style: none; }
    .progress-item { min-height: 8.5rem; border-top: 2px solid var(--line-strong); padding-top: .9rem; }
    .progress-number, .step-number { display: inline-grid; width: 2rem; height: 2rem; place-items: center; border: 1px solid var(--accent); border-radius: 50%; color: var(--accent-bright); font-weight: 850; font-variant-numeric: tabular-nums; }
    .progress-item strong { display: block; margin-top: .8rem; }
    .progress-item p { margin: .35rem 0 0; color: var(--muted); font-size: .9rem; }
    .brief-layout { display: grid; gap: 1.5rem; grid-template-columns: minmax(0, 1.25fr) minmax(17rem, .75fr); align-items: start; }
    form { display: grid; gap: 1rem; border: 1px solid var(--line); border-radius: .5rem; background: var(--surface); padding: clamp(1.25rem, 4vw, 2.5rem); box-shadow: 0 1.5rem 4rem var(--shadow); }
    fieldset { min-inline-size: 0; margin: 0; border: 1px solid var(--line); border-radius: .4rem; padding: 1rem; }
    legend { max-inline-size: 100%; padding: 0 .45rem; color: var(--ink); font-size: 1.05rem; font-weight: 800; }
    legend .step-number { width: 1.75rem; height: 1.75rem; margin-right: .45rem; vertical-align: middle; font-size: .86rem; }
    .fieldset-help { margin: .15rem 0 1rem; color: var(--muted); font-size: .92rem; }
    .field-grid { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .field-grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .spaced-grid { margin-top: 1rem; }
    .field { display: grid; gap: .4rem; }
    label { color: var(--muted-strong); font-weight: 760; }
    .required { color: var(--accent-bright); }
    input, textarea { width: 100%; min-height: 3rem; border: 1px solid var(--line-strong); border-radius: .35rem; background: #080b0e; color: var(--ink); padding: .72rem .8rem; }
    textarea { min-height: 7rem; resize: vertical; }
    input:focus-visible, textarea:focus-visible, button:focus-visible, a:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; border-color: var(--focus); }
    input[aria-invalid="true"], textarea[aria-invalid="true"] { border-color: var(--danger); }
    input::placeholder, textarea::placeholder { color: #82909d; opacity: 1; }
    .help, .small { margin: 0; color: var(--muted); font-size: .88rem; }
    .field-error { min-height: 0; margin: 0; color: var(--danger); font-size: .88rem; }
    .field-error[hidden], .error-summary[hidden] { display: none; }
    .rule-note { margin: 1rem 0 0; border-left: .22rem solid var(--caution); border-radius: .2rem; background: var(--caution-soft); color: var(--muted-strong); padding: .85rem 1rem; font-size: .92rem; }
    details { border-top: 1px solid var(--line); margin-top: 1rem; padding-top: .8rem; }
    summary { min-height: 2.75rem; cursor: pointer; color: var(--muted-strong); font-weight: 760; }
    details p { color: var(--muted); font-size: .92rem; }
    .review-list { display: grid; gap: .6rem; margin: 0; padding: 0; list-style: none; }
    .review-list li { display: flex; gap: .65rem; align-items: flex-start; color: var(--muted-strong); }
    .review-list li::before { content: ""; width: .55rem; height: .55rem; flex: 0 0 auto; margin-top: .55rem; border-radius: 50%; background: var(--accent); }
    form > button { justify-self: start; }
    .error-summary { border: 1px solid var(--danger); border-radius: .4rem; background: var(--danger-soft); color: var(--danger); padding: .85rem 1rem; }
    .error-summary strong { display: block; margin-bottom: .35rem; }
    .error-summary ul { margin: 0; padding-left: 1.1rem; }
    .error-summary a { color: var(--danger); }
    .result-message { min-height: 3.5rem; border: 1px solid var(--line-strong); border-radius: .4rem; background: var(--surface-soft); color: var(--muted-strong); padding: .85rem 1rem; }
    .result-message.success { border-color: var(--positive); background: var(--positive-soft); color: var(--positive); }
    .result-message.error { border-color: var(--danger); background: var(--danger-soft); color: var(--danger); }
    .side-rail { display: grid; gap: 1rem; }
    .side-card { border: 1px solid var(--line); border-radius: .45rem; background: var(--surface); padding: 1.25rem; }
    .side-card h3 { margin-bottom: .65rem; }
    .side-card p { margin-bottom: 0; color: var(--muted); font-size: .94rem; }
    .side-card ol { display: grid; gap: .75rem; margin: .9rem 0 0; padding-left: 1.25rem; color: var(--muted-strong); }
    .privacy-card { border-color: #70404a; background: #180e12; }
    footer { margin-top: clamp(2.5rem, 6vw, 5rem); border-top: 1px solid var(--line); padding-top: 1.35rem; color: var(--muted); font-size: .88rem; }
    @media (max-width: 980px) { .hero { grid-template-columns: minmax(0, 1fr) minmax(17rem, .75fr); } .brief-layout { grid-template-columns: 1fr; } .side-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 760px) { .site-header { align-items: flex-start; flex-wrap: wrap; padding: .85rem 0; } .site-nav { width: 100%; overflow-x: auto; } .site-nav a { flex: 0 0 auto; } .hero, .progress, .field-grid, .field-grid.three, .side-rail { grid-template-columns: 1fr; } h1 { max-width: 14ch; font-size: clamp(3rem, 13vw, 4.6rem); } .hero-actions { align-items: stretch; } .hero-actions .button, form > button { width: 100%; } .text-link { justify-content: center; } }
    @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } *, *::before, *::after { transition: none !important; } }
  </style>
</head>
<body>
<a class="skip-link" href="#brief">Skip to planning brief</a>
<main class="page-shell">
  <header class="site-header">
    <a class="brand" href="#top" aria-label="OathCast Planning Desk home"><img class="brand-mark" src="__PILOT_LOGO_SRC__" width="192" height="192" alt="" aria-hidden="true"><span class="brand-copy"><span class="brand-name">OathCast</span><span class="brand-subtitle">Planning Desk</span></span></a>
    <nav class="site-nav" aria-label="Page sections">
      <a href="#how-it-works">How it works</a>
      <a href="#brief">Planning brief</a>
      <a class="nav-status" href="#privacy">Privacy and limits</a>
    </nav>
  </header>

  <section class="hero section" id="top" aria-labelledby="page-heading">
    <div>
      <span class="status-pill"><span class="status-dot" aria-hidden="true"></span>Preparation mode</span>
      <h1 id="page-heading">Turn one weather question into a clear planning brief.</h1>
      <p class="lede">Tell OathCast what you are deciding, where it matters, and the exact UTC hour you care about. This local pilot checks the details and saves a review-ready brief. It does not contact Miners.</p>
      <div class="hero-actions">
        <a class="button" href="#brief">Start a planning brief</a>
        <a class="text-link" href="#how-it-works">See the four steps</a>
      </div>
      <p class="hero-note"><span class="status-dot positive" aria-hidden="true"></span>No account, contact details, payment, or Miner call is needed.</p>
    </div>
    <aside class="pilot-status" aria-labelledby="mode-heading">
      <p class="eyebrow"><span class="status-dot positive" aria-hidden="true"></span>What happens here</p>
      <h2 id="mode-heading">A local preparation tool</h2>
      <p>Your brief is checked and placed in a local review queue. It does not become a paid Telegraph request yet.</p>
      <ul class="status-list">
        <li><strong>Stored locally</strong><span>Only the planning question and forecast window are saved.</span></li>
        <li><strong>Ready for review</strong><span>A stable reference is created so the same brief is not duplicated.</span></li>
        <li><strong>Not live traffic</strong><span>No weather provider, Miner, wallet, or payment endpoint is contacted.</span></li>
      </ul>
    </aside>
  </section>

  <section class="section" id="how-it-works" aria-labelledby="steps-heading">
    <div class="section-heading">
      <p class="eyebrow">Start here</p>
      <h2 id="steps-heading">Four simple steps</h2>
      <p>You only need one decision, one place, and one exact hour. The examples under each field show the format to use.</p>
    </div>
    <ol class="progress">
      <li class="progress-item"><span class="progress-number" aria-hidden="true">1</span><strong>Describe the decision</strong><p>Say what action could change if rain is likely.</p></li>
      <li class="progress-item"><span class="progress-number" aria-hidden="true">2</span><strong>Choose the place</strong><p>Use a name and the map coordinates for that point.</p></li>
      <li class="progress-item"><span class="progress-number" aria-hidden="true">3</span><strong>Set the time</strong><p>Use UTC and keep the cutoff before the forecast starts.</p></li>
      <li class="progress-item"><span class="progress-number" aria-hidden="true">4</span><strong>Review and queue</strong><p>Check the summary, then save the brief for review.</p></li>
    </ol>
  </section>

  <section class="section" id="brief" aria-labelledby="brief-heading">
    <div class="section-heading">
      <p class="eyebrow">Planning brief</p>
      <h2 id="brief-heading">Give the forecast a decision to answer</h2>
      <p>Use ordinary language. For example: “Move the Saturday market indoors if measurable rain is likely.”</p>
    </div>
    <div class="brief-layout">
      <form id="pilot-form" novalidate aria-describedby="form-intro">
        <p class="small" id="form-intro"><span class="required" aria-hidden="true">*</span> Required fields. The form accepts one exact one-hour precipitation question.</p>
        <div class="error-summary" id="error-summary" role="alert" tabindex="-1" hidden>
          <strong>There is a problem with this brief.</strong>
          <ul id="error-list"></ul>
        </div>

        <fieldset>
          <legend><span class="step-number" aria-hidden="true">1</span>What are you deciding?</legend>
          <p class="fieldset-help">Describe the real-world action that depends on the weather.</p>
          <div class="field">
            <label for="use-case">Your planning question <span class="required" aria-hidden="true">*</span></label>
            <textarea id="use-case" name="use_case" required maxlength="240" aria-describedby="use-case-help use-case-error" placeholder="Move the Saturday market indoors if measurable rain is likely."></textarea>
            <p class="help" id="use-case-help">Keep it specific: say what you may do, not only “check the weather.”</p>
            <p class="field-error" id="use-case-error" role="alert" hidden></p>
          </div>
        </fieldset>

        <fieldset>
          <legend><span class="step-number" aria-hidden="true">2</span>Where does it matter?</legend>
          <p class="fieldset-help">The name helps people read the brief. The coordinates tell the forecast exactly which point to use.</p>
          <div class="field-grid">
            <div class="field">
              <label for="location-name">Place name <span class="required" aria-hidden="true">*</span></label>
              <input id="location-name" name="location_name" required maxlength="96" autocomplete="address-level2" aria-describedby="location-name-help location-name-error" placeholder="Lagos market square">
              <p class="help" id="location-name-help">A city, venue, street, or other name people will recognize.</p>
              <p class="field-error" id="location-name-error" role="alert" hidden></p>
            </div>
            <div class="field">
              <label for="latitude">Latitude <span class="required" aria-hidden="true">*</span></label>
              <input id="latitude" name="latitude" required inputmode="decimal" aria-describedby="coordinates-help latitude-error" placeholder="6.5244">
              <p class="field-error" id="latitude-error" role="alert" hidden></p>
            </div>
          </div>
          <div class="field-grid spaced-grid">
            <div class="field">
              <label for="longitude">Longitude <span class="required" aria-hidden="true">*</span></label>
              <input id="longitude" name="longitude" required inputmode="decimal" aria-describedby="coordinates-help longitude-error" placeholder="3.3792">
              <p class="field-error" id="longitude-error" role="alert" hidden></p>
            </div>
            <p class="help" id="coordinates-help">Coordinates are the latitude and longitude of the exact point. You can copy them from a map pin.</p>
          </div>
          <details>
            <summary>Why are coordinates needed?</summary>
            <p>Weather can vary across a city. Coordinates reduce ambiguity by telling the forecast service the exact point you mean. A latitude is north or south; a longitude is east or west.</p>
          </details>
        </fieldset>

        <fieldset>
          <legend><span class="step-number" aria-hidden="true">3</span>Which UTC hour matters?</legend>
          <p class="fieldset-help">Use the format <code>YYYY-MM-DDTHH:MM:SSZ</code>. The final <code>Z</code> means UTC, the shared reference time used by the forecast service.</p>
          <div class="field-grid three">
            <div class="field">
              <label for="forecast-cutoff">Forecast cutoff <span class="required" aria-hidden="true">*</span></label>
              <input id="forecast-cutoff" name="forecast_cutoff" required spellcheck="false" autocomplete="off" aria-describedby="time-help forecast-cutoff-error" placeholder="2026-08-17T12:00:00Z">
              <p class="field-error" id="forecast-cutoff-error" role="alert" hidden></p>
            </div>
            <div class="field">
              <label for="horizon-start">Window starts <span class="required" aria-hidden="true">*</span></label>
              <input id="horizon-start" name="horizon_start" required spellcheck="false" autocomplete="off" aria-describedby="time-help horizon-start-error" placeholder="2026-08-17T15:00:00Z">
              <p class="field-error" id="horizon-start-error" role="alert" hidden></p>
            </div>
            <div class="field">
              <label for="horizon-end">Window ends <span class="required" aria-hidden="true">*</span></label>
              <input id="horizon-end" name="horizon_end" required spellcheck="false" autocomplete="off" aria-describedby="time-help horizon-end-error" placeholder="2026-08-17T16:00:00Z">
              <p class="field-error" id="horizon-end-error" role="alert" hidden></p>
            </div>
          </div>
          <p class="rule-note" id="time-help"><strong>Important time rule.</strong> The cutoff must be earlier than the window start. The window must last exactly one hour, so the end is one hour after the start.</p>
          <details>
            <summary>Example of a valid time window</summary>
            <p>Cutoff <code>2026-08-17T12:00:00Z</code>, start <code>2026-08-17T15:00:00Z</code>, and end <code>2026-08-17T16:00:00Z</code>. The cutoff is before the start, and the window is one hour long.</p>
          </details>
        </fieldset>

        <fieldset>
          <legend><span class="step-number" aria-hidden="true">4</span>Check before saving</legend>
          <p class="fieldset-help">This is the fixed event the pilot can prepare today.</p>
          <ul class="review-list">
            <li>Measurable precipitation greater than 0.1 mm.</li>
            <li>One exact UTC hour at the coordinates above.</li>
            <li>Saved locally for review only. No paid request is sent.</li>
          </ul>
        </fieldset>

        <button id="submit-button" type="submit">Queue this planning brief</button>
        <div id="result" class="result-message" role="status" aria-live="polite" tabindex="-1">Nothing is queued yet. Submit the brief when the details look right.</div>
      </form>

      <aside class="side-rail" aria-label="Planning brief guidance">
        <section class="side-card">
          <h3>What happens after you submit?</h3>
          <ol>
            <li>OathCast checks the location and UTC times.</li>
            <li>The brief receives a stable reference.</li>
            <li>It waits in a local queue for a later review step.</li>
          </ol>
          <p>No live forecast answer is returned by this pilot yet.</p>
        </section>
        <section class="side-card privacy-card" id="privacy">
          <h3>Privacy and limits</h3>
          <p>Do not enter names, phone numbers, email addresses, wallet details, or secrets. This tool needs only the planning question, place, coordinates, and time window.</p>
          <p>This is preparation, not a safety guarantee and not qualifying Telegraph traffic.</p>
        </section>
      </aside>
    </div>
  </section>

  <footer>OathCast Planning Desk / local intake only. No Miner, Telegraph, weather provider, wallet, or payment endpoint is contacted from this page.</footer>
</main>
<script>
const form = document.querySelector('#pilot-form');
const submitButton = document.querySelector('#submit-button');
const result = document.querySelector('#result');
const errorSummary = document.querySelector('#error-summary');
const errorList = document.querySelector('#error-list');
const fields = {
  use_case: {id: 'use-case', label: 'your planning question'},
  location_name: {id: 'location-name', label: 'the place name'},
  latitude: {id: 'latitude', label: 'the latitude'},
  longitude: {id: 'longitude', label: 'the longitude'},
  forecast_cutoff: {id: 'forecast-cutoff', label: 'the forecast cutoff'},
  horizon_start: {id: 'horizon-start', label: 'the window start'},
  horizon_end: {id: 'horizon-end', label: 'the window end'}
};

function setFieldError(fieldName, message) {
  const field = fields[fieldName];
  const input = document.getElementById(field.id);
  const error = document.getElementById(`${field.id}-error`);
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
  error.textContent = message;
  error.hidden = !message;
}

function clearErrors() {
  Object.keys(fields).forEach((fieldName) => setFieldError(fieldName, ''));
  errorList.replaceChildren();
  errorSummary.hidden = true;
}

function showErrors(errors) {
  errorList.replaceChildren();
  errors.forEach(({fieldName, message}) => {
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = `#${fields[fieldName].id}`;
    link.textContent = message;
    item.append(link);
    errorList.append(item);
    setFieldError(fieldName, message);
  });
  errorSummary.hidden = false;
  errorSummary.focus();
}

function validatePayload(payload) {
  const errors = [];
  const addError = (fieldName, message) => errors.push({fieldName, message});
  Object.entries(fields).forEach(([fieldName, field]) => {
    if (!String(payload[fieldName] || '').trim()) {
      addError(fieldName, `Enter ${field.label}.`);
    }
  });
  if (errors.length) return errors;

  const latitude = Number(payload.latitude);
  const longitude = Number(payload.longitude);
  if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
    addError('latitude', 'Latitude must be a number from -90 to 90.');
  }
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
    addError('longitude', 'Longitude must be a number from -180 to 180.');
  }

  const dates = {};
  ['forecast_cutoff', 'horizon_start', 'horizon_end'].forEach((fieldName) => {
    const value = payload[fieldName].trim();
    if (!/(Z|[+-]00:00)$/.test(value)) {
      addError(fieldName, 'Use a UTC time ending in Z, for example 2026-08-17T15:00:00Z.');
      return;
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      addError(fieldName, 'Use the format YYYY-MM-DDTHH:MM:SSZ.');
      return;
    }
    dates[fieldName] = parsed;
  });
  if (dates.forecast_cutoff && dates.horizon_start && dates.forecast_cutoff >= dates.horizon_start) {
    addError('forecast_cutoff', 'The cutoff must be earlier than the window start.');
  }
  if (dates.horizon_start && dates.horizon_end && dates.horizon_end - dates.horizon_start !== 3600000) {
    addError('horizon_end', 'The window must end exactly one hour after it starts.');
  }
  return errors;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearErrors();
  const payload = Object.fromEntries(new FormData(form).entries());
  const errors = validatePayload(payload);
  if (errors.length) {
    showErrors(errors);
    result.className = 'result-message error';
    result.textContent = 'Fix the highlighted fields above, then submit again.';
    return;
  }

  submitButton.disabled = true;
  submitButton.setAttribute('aria-busy', 'true');
  submitButton.textContent = 'Queueing brief...';
  result.className = 'result-message';
  result.textContent = 'Checking the details and saving the local brief...';
  try {
    const response = await fetch('/api/pilot-requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'The brief was rejected.');
    result.className = 'result-message success';
    result.textContent = `Planning brief queued: ${body.request_id}\nNo Telegraph call was made. The brief is ready for a later review step.`;
    form.reset();
    clearErrors();
    result.focus();
  } catch (error) {
    result.className = 'result-message error';
    result.textContent = `The brief was not queued: ${error.message || 'Please check the fields and try again.'}`;
    result.focus();
  } finally {
    submitButton.disabled = false;
    submitButton.removeAttribute('aria-busy');
    submitButton.textContent = 'Queue this planning brief';
  }
});
</script>
</body>
</html>"""
    return html.replace(
        "__PILOT_LOGO_SRC__",
        f"{PILOT_LOGO_PATH}?v={PILOT_LOGO_VERSION}",
        1,
    )


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
            path = urlsplit(self.path).path
            if path == PILOT_LOGO_PATH:
                if _PILOT_LOGO_BYTES is None:
                    _json_response(self, 404, {"error": "not_found"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/webp")
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(_PILOT_LOGO_BYTES)))
                self.end_headers()
                self.wfile.write(_PILOT_LOGO_BYTES)
                return
            if path == "/":
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
