"""Thin HTTP Miner service around the provider adapters.

The service is intentionally standard-library-only so it can run as a small
container now and be replaced by the final deployment stack later. It exposes
one public OathCast Miner endpoint while keeping provider credentials and raw
payload provenance behind the service boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from collections import OrderedDict, deque
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from oathcast.adapters import OpenMeteoAdapter, OpenWeatherAdapter, WeatherApiAdapter
from oathcast.forecast import (
    SUPPORTED_EVENT_OPERATOR,
    CanonicalForecast,
    ForecastQuestion,
    format_timestamp,
    parse_timestamp,
)
from oathcast.receipts import ReceiptConflict, SqliteReceiptStore
from oathcast.render import public_response
from oathcast.release import ReleaseInfo, current_release


UTC = timezone.utc
JsonFetcher = Callable[[str], dict[str, Any]]


class ProviderUnavailable(RuntimeError):
    """Raised when every configured provider failed for the same request."""


class ForecastCutoffPassed(ValueError):
    """Raised when a new forecast arrives at or after its declared cutoff."""


def receipt_digest(receipt: dict[str, Any]) -> str:
    """Hash canonical receipt bytes, excluding the digest field itself."""

    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fetch_json(url: str, timeout_seconds: float = 12.0) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "OathCast/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response must be a JSON object")
    return payload


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def authorization_valid(
    authorization: str | None,
    token: str | Iterable[str] | None,
    *,
    require_auth: bool = False,
) -> bool:
    """Validate a YAML-compatible Bearer token without timing leaks.

    Local unit tests may explicitly opt out. Production services must set
    ``require_auth`` and provision a non-empty token; a missing token then
    fails closed rather than silently exposing the Miner.
    """

    if isinstance(token, str):
        candidates = (token,)
    elif token is None:
        candidates = ()
    else:
        candidates = tuple(item for item in token if isinstance(item, str) and item)
    if not candidates:
        return not require_auth
    supplied = authorization or ""
    valid = False
    for candidate in candidates:
        valid = hmac.compare_digest(supplied, f"Bearer {candidate}") or valid
    return valid


class RequestRateLimiter:
    """Small in-process sliding-window limiter for a single Miner instance."""

    def __init__(
        self,
        limit_per_minute: int = 120,
        *,
        window_seconds: float = 60.0,
        max_keys: int = 4096,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if limit_per_minute < 0:
            raise ValueError("limit_per_minute must be non-negative")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self.limit_per_minute = limit_per_minute
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.clock = clock or time.monotonic
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for one request."""

        if self.limit_per_minute == 0:
            return True, 0
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            # Expire idle buckets before admitting a new identity. This keeps
            # invalid-header/IP churn from growing the in-memory map forever.
            for existing_key, existing_events in list(self._events.items()):
                while existing_events and existing_events[0] <= cutoff:
                    existing_events.popleft()
                if not existing_events:
                    del self._events[existing_key]
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self.max_keys:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            if len(events) >= self.limit_per_minute:
                retry_after = max(1, int(events[0] + self.window_seconds - now) + 1)
                return False, retry_after
            events.append(now)
            return True, 0

    @property
    def tracked_key_count(self) -> int:
        """Return the bounded number of identities currently tracked."""

        with self._lock:
            return len(self._events)


@dataclass(frozen=True)
class ServiceForecast:
    question: ForecastQuestion
    forecast: CanonicalForecast
    raw_payload: dict[str, Any]
    request_id: str
    receipt_sha256: str | None = None

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider": self.forecast.provider,
            "adapter_version": self.forecast.adapter_version,
            "provider_model": self.forecast.provider_model,
            "raw_payload_sha256": self.forecast.raw_payload_sha256,
            "issued_at": format_timestamp(self.forecast.issued_at),
            "retrieved_at": (
                None
                if self.forecast.retrieved_at is None
                else format_timestamp(self.forecast.retrieved_at)
            ),
            "native_event_definition": self.forecast.native_event_definition,
            "event_equivalence": self.forecast.event_equivalence,
        }

    def to_public_response(self) -> dict[str, Any]:
        return public_response(self.question, self.forecast)


class ForecastService:
    """One public Miner service with provider failover behind it."""

    adapters = {
        "open_meteo": OpenMeteoAdapter(),
        "weatherapi": WeatherApiAdapter(),
        "openweather_onecall": OpenWeatherAdapter(),
    }
    api_key_env = {
        "open_meteo": None,
        "weatherapi": "WEATHERAPI_KEY",
        "openweather_onecall": "OPENWEATHER_API_KEY",
    }
    verified_providers = frozenset({"open_meteo"})

    def __init__(
        self,
        *,
        fetcher: JsonFetcher = fetch_json,
        provider_order: list[str] | None = None,
        api_keys: dict[str, str] | None = None,
        auth_token: str | None = None,
        require_auth: bool = False,
        allow_unverified_providers: bool = False,
        receipt_store: SqliteReceiptStore | None = None,
        clock: Callable[[], datetime] | None = None,
        auth_tokens: Iterable[str] | None = None,
        rate_limit_per_minute: int | None = None,
        auth_failure_limit_per_minute: int | None = None,
        release: ReleaseInfo | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.provider_order = provider_order or self._provider_order_from_env()
        unknown = set(self.provider_order) - set(self.adapters)
        if unknown:
            raise ValueError(f"unknown providers in order: {sorted(unknown)}")
        self.api_keys = api_keys or {}
        configured_tokens = [
            item.strip()
            for item in os.getenv("OATHCAST_MINER_API_KEYS", "").split(",")
            if item.strip()
        ]
        if auth_tokens is not None:
            configured_tokens = [item.strip() for item in auth_tokens if item and item.strip()]
        else:
            legacy_token = auth_token if auth_token is not None else os.getenv("OATHCAST_MINER_API_KEY")
            if legacy_token and legacy_token.strip():
                configured_tokens.insert(0, legacy_token.strip())
        self.auth_tokens = tuple(dict.fromkeys(configured_tokens))
        self.auth_token = self.auth_tokens[0] if self.auth_tokens else None
        self.require_auth = require_auth
        if self.require_auth and not self.auth_tokens:
            raise ValueError(
                "OATHCAST_MINER_API_KEY or OATHCAST_MINER_API_KEYS must be provisioned when authentication is required"
            )
        self.allow_unverified_providers = allow_unverified_providers
        self.receipt_store = receipt_store
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        if rate_limit_per_minute is None:
            try:
                rate_limit_per_minute = int(os.getenv("OATHCAST_RATE_LIMIT_PER_MINUTE", "120"))
            except ValueError as exc:
                raise ValueError("OATHCAST_RATE_LIMIT_PER_MINUTE must be an integer") from exc
        self.rate_limiter = RequestRateLimiter(rate_limit_per_minute)
        if auth_failure_limit_per_minute is None:
            try:
                auth_failure_limit_per_minute = int(
                    os.getenv("OATHCAST_AUTH_FAILURE_LIMIT_PER_MINUTE", "20")
                )
            except ValueError as exc:
                raise ValueError("OATHCAST_AUTH_FAILURE_LIMIT_PER_MINUTE must be an integer") from exc
        self.auth_failure_limiter = RequestRateLimiter(auth_failure_limit_per_minute)
        self.release = release or current_release()

    def rate_limit_key(self, *, remote_address: str) -> str:
        """Return a non-sensitive limiter key derived only from client address."""

        identity = remote_address or "unknown"
        return hashlib.sha256(identity.encode()).hexdigest()

    @classmethod
    def _provider_order_from_env(cls) -> list[str]:
        raw = os.getenv("OATHCAST_PROVIDER_ORDER", "open_meteo")
        return [name.strip() for name in raw.split(",") if name.strip()]

    def _api_key_for(self, provider: str) -> str | None:
        if provider in self.api_keys:
            return self.api_keys[provider]
        env_name = self.api_key_env[provider]
        return None if env_name is None else os.getenv(env_name)

    def _fetch_one(self, question: ForecastQuestion, provider: str, request_id: str) -> ServiceForecast:
        if provider not in self.verified_providers and not self.allow_unverified_providers:
            raise ValueError(
                f"{provider} is disabled until its precipitation event semantics are validated"
            )
        adapter = self.adapters[provider]
        retrieved_at = self.clock().astimezone(UTC)
        url = adapter.build_url(question, self._api_key_for(provider))
        payload = self.fetcher(url)
        forecast = adapter.parse(
            payload,
            question,
            issued_at=retrieved_at,
            retrieved_at=retrieved_at,
        )
        if forecast.event_equivalence != "documented_match" and not self.allow_unverified_providers:
            raise ValueError(
                f"{provider} is disabled until its precipitation event semantics are validated"
            )
        forecast = replace(forecast, raw_payload_sha256=payload_hash(payload))
        return ServiceForecast(
            question=question,
            forecast=forecast,
            raw_payload=payload,
            request_id=request_id,
        )

    def forecast(
        self,
        question: ForecastQuestion,
        *,
        request_id: str,
        requested_provider: str | None = None,
    ) -> ServiceForecast:
        if self.receipt_store is not None:
            stored = self.receipt_store.get(question.event_id)
            if stored is not None:
                return self._service_forecast_from_receipt(stored, question)

        now = self.clock().astimezone(UTC)
        if now >= question.forecast_cutoff:
            raise ForecastCutoffPassed(
                "forecast_cutoff_passed: new forecasts must be issued before "
                f"{format_timestamp(question.forecast_cutoff)}"
            )

        order = [requested_provider] if requested_provider else self.provider_order
        failures: list[str] = []
        for provider in order:
            if provider not in self.adapters:
                failures.append(f"{provider}: unknown provider")
                continue
            try:
                result = self._fetch_one(question, provider, request_id)
                completed_at = self.clock().astimezone(UTC)
                if completed_at >= question.forecast_cutoff:
                    raise ForecastCutoffPassed(
                        "forecast_cutoff_passed: upstream response completed at or after "
                        f"{format_timestamp(question.forecast_cutoff)}"
                    )
                return self._persist_receipt(result)
            except ForecastCutoffPassed:
                raise
            except ReceiptConflict:
                raise
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
        raise ProviderUnavailable("; ".join(failures))

    def _service_forecast_from_receipt(
        self,
        receipt: dict[str, Any],
        requested_question: ForecastQuestion,
    ) -> ServiceForecast:
        expected_digest = receipt.get("receipt_sha256")
        if expected_digest is not None and expected_digest != receipt_digest(receipt):
            raise RuntimeError("stored forecast receipt failed its integrity check")
        stored_question = ForecastQuestion.from_dict(receipt["question"])
        if stored_question.to_dict() != requested_question.to_dict():
            raise ReceiptConflict(
                f"event_id {requested_question.event_id!r} is already bound to a different question"
            )
        forecast_data = receipt["forecast"]
        forecast = CanonicalForecast(
            event_id=str(forecast_data["event_id"]),
            provider=str(forecast_data["provider"]),
            probability=float(forecast_data["probability"]),
            horizon_start=parse_timestamp(forecast_data["horizon_start"]),
            horizon_end=parse_timestamp(forecast_data["horizon_end"]),
            threshold_mm=float(forecast_data["threshold_mm"]),
            issued_at=parse_timestamp(forecast_data["issued_at"]),
            native_event_definition=str(forecast_data["native_event_definition"]),
            event_equivalence=str(forecast_data["event_equivalence"]),
            adapter_version=str(forecast_data["adapter_version"]),
            provider_model=forecast_data.get("provider_model"),
            retrieved_at=(
                None
                if forecast_data.get("retrieved_at") is None
                else parse_timestamp(forecast_data["retrieved_at"])
            ),
            raw_payload_sha256=forecast_data.get("raw_payload_sha256"),
        )
        return ServiceForecast(
            question=stored_question,
            forecast=forecast,
            raw_payload=receipt.get("raw_payload", {}),
            request_id=str(receipt["request_id"]),
            receipt_sha256=expected_digest,
        )

    def _persist_receipt(self, result: ServiceForecast) -> ServiceForecast:
        if self.receipt_store is None:
            return result
        receipt = {
            "schema_version": 1,
            "created_at": format_timestamp(self.clock()),
            "request_id": result.request_id,
            "question": result.question.to_dict(),
            "forecast": result.forecast.to_dict(),
            "raw_payload": result.raw_payload,
            "public_response": result.to_public_response(),
        }
        receipt["receipt_sha256"] = receipt_digest(receipt)
        stored = self.receipt_store.save(receipt)
        return self._service_forecast_from_receipt(stored, result.question)


def question_from_query(params: dict[str, list[str]]) -> ForecastQuestion:
    def first(names: tuple[str, ...], default: str | None = None) -> str:
        for name in names:
            values = params.get(name)
            if values and values[0] != "":
                return values[0]
        if default is not None:
            return default
        raise ValueError(f"missing query parameter: {' or '.join(names)}")

    start = parse_timestamp(first(("horizon_start", "start")))
    end = parse_timestamp(first(("horizon_end", "end")))
    cutoff_value = params.get("forecast_cutoff", params.get("cutoff", [None]))[0]
    cutoff = parse_timestamp(cutoff_value) if cutoff_value else start - timedelta(hours=1)
    return ForecastQuestion(
        event_id=first(("event_id",), f"request-{start.timestamp():.0f}"),
        location_name=first(("location_name",), "requested location"),
        latitude=float(first(("latitude", "lat"))),
        longitude=float(first(("longitude", "lon"))),
        horizon_start=start,
        horizon_end=end,
        forecast_cutoff=cutoff,
        threshold_mm=float(first(("threshold_mm",), "0.1")),
        operator=first(("operator",), SUPPORTED_EVENT_OPERATOR),
    )


class ForecastRequestHandler(BaseHTTPRequestHandler):
    """Development HTTP handler for the canonical Miner endpoint."""

    service: ForecastService

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-OathCast-Release-ID", self.service.release.release_id)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "oathcast-miner",
                    "providers": self.service.provider_order,
                    "auth_required": self.service.require_auth,
                    "rate_limit_per_minute": self.service.rate_limiter.limit_per_minute,
                    "auth_failure_limit_per_minute": self.service.auth_failure_limiter.limit_per_minute,
                    "release": self.service.release.to_dict(),
                },
            )
            return
        if parsed.path == "/readyz":
            ready = bool(self.service.receipt_store is not None or not self.service.require_auth)
            self._send_json(
                200 if ready else 503,
                {
                    "ready": ready,
                    "auth_configured": bool(self.service.auth_tokens),
                    "receipt_store_configured": self.service.receipt_store is not None,
                    "release": self.service.release.to_dict(),
                },
            )
            return
        if parsed.path != "/v1/forecast/point":
            self._send_json(404, {"error": "not_found"})
            return

        request_id = self.headers.get("X-Request-ID", "").strip()
        if not request_id or len(request_id) > 128 or any(ord(char) < 32 for char in request_id):
            request_id = f"http-{uuid.uuid4().hex}"
        # Do not trust a client-supplied forwarding header here. Caddy keeps
        # the Miner on loopback, so public callers share the proxy's bounded
        # bucket; direct deployments still receive the socket peer address.
        remote_address = self.client_address[0]
        rate_key = self.service.rate_limit_key(remote_address=remote_address)
        authorized = authorization_valid(
            self.headers.get("Authorization"),
            self.service.auth_tokens,
            require_auth=self.service.require_auth,
        )
        if not authorized:
            allowed, retry_after = self.service.auth_failure_limiter.check(rate_key)
            if not allowed:
                self._send_json(
                    429,
                    {"error": "rate_limited", "request_id": request_id},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-OathCast-Request-ID": request_id,
                    },
                )
                return
            self._send_json(
                401,
                {"error": "unauthorized"},
                headers={"WWW-Authenticate": "Bearer", "X-OathCast-Request-ID": request_id},
            )
            return

        allowed, retry_after = self.service.rate_limiter.check(
            rate_key
        )
        if not allowed:
            self._send_json(
                429,
                {"error": "rate_limited", "request_id": request_id},
                headers={
                    "Retry-After": str(retry_after),
                    "X-OathCast-Request-ID": request_id,
                },
            )
            return
        try:
            params = parse_qs(parsed.query, keep_blank_values=False)
            question = question_from_query(params)
            result = self.service.forecast(
                question,
                request_id=request_id,
                requested_provider=params.get("provider", [None])[0],
            )
            headers = (
                {"X-OathCast-Request-ID": request_id}
                if result.receipt_sha256 is None
                else {
                    "X-OathCast-Request-ID": request_id,
                    "X-OathCast-Receipt-SHA256": result.receipt_sha256,
                }
            )
            self._send_json(200, result.to_public_response(), headers=headers)
        except ForecastCutoffPassed as exc:
            self._send_json(410, {"error": str(exc), "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})
        except ReceiptConflict as exc:
            self._send_json(409, {"error": str(exc), "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})
        except ProviderUnavailable:
            self._send_json(502, {"error": "provider_unavailable", "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc), "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    def env_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    receipt_path = os.getenv("OATHCAST_RECEIPT_DB", "data/oathcast/receipts.sqlite3")
    service = ForecastService(
        require_auth=env_flag("OATHCAST_REQUIRE_AUTH", True),
        receipt_store=SqliteReceiptStore(receipt_path),
    )

    class BoundHandler(ForecastRequestHandler):
        pass

    BoundHandler.service = service
    server = ThreadingHTTPServer((host, port), BoundHandler)
    print(f"OathCast Miner listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server(
        host=os.getenv("OATHCAST_HOST", "127.0.0.1"),
        port=int(os.getenv("OATHCAST_PORT", os.getenv("PORT", "8080"))),
    )
