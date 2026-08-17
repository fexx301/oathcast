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
import ipaddress
import json
import logging
import math
import os
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from oathcast.adapters import (
    OpenMeteoAdapter,
    OpenMeteoTemperatureWindowAdapter,
    OpenMeteoWindowAdapter,
    OpenWeatherAdapter,
    WeatherApiAdapter,
)
from oathcast.protocol import outbound_headers
from oathcast.forecast import (
    SUPPORTED_EVENT_OPERATOR,
    CanonicalForecast,
    CanonicalTemperatureWindowForecast,
    CanonicalWindowForecast,
    ForecastQuestion,
    ForecastWindowRequest,
    TemperatureWindowRequest,
    ensure_utc,
    format_timestamp,
    parse_timestamp,
)
from oathcast.receipts import (
    DEFAULT_MAX_RECEIPT_BYTES,
    DEFAULT_MAX_RECEIPT_ROWS,
    ReceiptConflict,
    ReceiptStoreFull,
    ReceiptTampering,
    SqliteReceiptStore,
    receipt_digest,
)
from oathcast.render import (
    public_response,
    public_temperature_window_response,
    public_window_response,
)
from oathcast.release import ReleaseInfo, current_release


UTC = timezone.utc
LOGGER = logging.getLogger("oathcast.service")
JsonFetcher = Callable[[str], dict[str, Any]]
MAX_REQUEST_TARGET_LENGTH = 8192
MAX_QUERY_LENGTH = 4096
MAX_QUERY_PARAMETERS = 32
MAX_EVENT_ID_LENGTH = 128
MAX_LOCATION_NAME_LENGTH = 256
DEFAULT_TRUSTED_PROXY_NETWORKS = ("127.0.0.0/8", "::1/128")
REGISTERED_FORECAST_PATH = "/predict"
POINT_FORECAST_PATH = "/v1/forecast/point"
WINDOW_FORECAST_PATH = "/v1/forecast/window"
FORECAST_PATHS = frozenset(
    {REGISTERED_FORECAST_PATH, POINT_FORECAST_PATH}
)


def _log_request_failure(
    event: str,
    *,
    request_id: str,
    path: str,
    error: BaseException,
) -> None:
    """Emit a machine-readable failure record without sensitive error text."""

    cause = error.__cause__ or error
    LOGGER.error(
        json.dumps(
            {
                "event": event,
                "request_id": request_id,
                "path": path,
                "error_type": type(error).__name__,
                "cause_type": type(cause).__name__,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


class ProviderUnavailable(RuntimeError):
    """Raised when every configured provider failed for the same request."""


class ReceiptStoreUnavailable(RuntimeError):
    """Raised when receipt evidence cannot be read or persisted safely."""


class ForecastCutoffPassed(ValueError):
    """Raised when a new forecast arrives at or after its declared cutoff."""


MAX_PROVIDER_BODY_BYTES = 2 * 1024 * 1024


def fetch_json(
    url: str,
    timeout_seconds: float = 12.0,
    *,
    max_body_bytes: int = MAX_PROVIDER_BODY_BYTES,
) -> dict[str, Any]:
    """Fetch and parse a provider JSON body under a hard byte cap.

    An unbounded ``read()`` lets a hostile or malfunctioning upstream exhaust
    memory, so the body is capped. One extra byte is requested so that a
    response sitting exactly on the limit is accepted while a larger one is
    detected rather than silently truncated into a parse error. A declared
    ``Content-Length`` over the cap is rejected before any body is read.
    """

    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")

    request = Request(url, headers=outbound_headers())
    with urlopen(request, timeout=timeout_seconds) as response:
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > max_body_bytes:
                raise ValueError(
                    f"provider response exceeds {max_body_bytes} byte cap"
                )
        body = response.read(max_body_bytes + 1)

    if len(body) > max_body_bytes:
        raise ValueError(f"provider response exceeds {max_body_bytes} byte cap")

    payload = json.loads(body.decode("utf-8"))
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
    stored_public_response: dict[str, Any] | None = None

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
        if self.stored_public_response is not None:
            return dict(self.stored_public_response)
        return public_response(self.question, self.forecast)


@dataclass(frozen=True)
class ServiceWindowForecast:
    request: ForecastWindowRequest
    forecast: CanonicalWindowForecast
    raw_payload: dict[str, Any]
    request_id: str
    receipt_sha256: str | None = None
    stored_public_response: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.request.event_id != self.forecast.event_id:
            raise ValueError("request and forecast event_id do not match")
        if (
            self.request.horizon_start != self.forecast.horizon_start
            or self.request.horizon_end != self.forecast.horizon_end
        ):
            raise ValueError("request and forecast horizon do not match")

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
            "temperature_native_definition": (
                self.forecast.temperature_native_definition
            ),
            "precipitation_native_definition": (
                self.forecast.precipitation_native_definition
            ),
            "event_equivalence": self.forecast.event_equivalence,
        }

    def to_public_response(self) -> dict[str, Any]:
        if self.stored_public_response is not None:
            return dict(self.stored_public_response)
        return public_window_response(self.request, self.forecast)


@dataclass(frozen=True)
class ServiceTemperatureWindowForecast:
    request: TemperatureWindowRequest
    forecast: CanonicalTemperatureWindowForecast
    raw_payload: dict[str, Any]
    request_id: str
    receipt_sha256: str | None = None
    stored_public_response: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.request.event_id != self.forecast.event_id:
            raise ValueError("request and forecast event_id do not match")
        if self.request.reference_time != self.forecast.reference_time:
            raise ValueError("request and forecast reference_time do not match")
        if self.request.forecast_hours != len(self.forecast.hours):
            raise ValueError("request and forecast hour counts do not match")

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
            "temperature_native_definition": (
                self.forecast.temperature_native_definition
            ),
        }

    def to_public_response(self) -> dict[str, Any]:
        if self.stored_public_response is not None:
            return dict(self.stored_public_response)
        return public_temperature_window_response(self.request, self.forecast)


class ForecastService:
    """One public Miner service with provider failover behind it."""

    adapters = {
        "open_meteo": OpenMeteoAdapter(),
        "weatherapi": WeatherApiAdapter(),
        "openweather_onecall": OpenWeatherAdapter(),
    }
    window_adapters = {
        "open_meteo": OpenMeteoWindowAdapter(),
    }
    temperature_window_adapters = {
        "open_meteo": OpenMeteoTemperatureWindowAdapter(),
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
        trusted_proxy_networks: Iterable[str] | None = None,
        release: ReleaseInfo | None = None,
        receipt_write_probe_interval_seconds: float | None = None,
        temperature_window_enabled: bool = False,
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
        # The 2t compatibility envelope is deliberately opt-in at the HTTP
        # boundary. The registered YAML remains the one-hour precipitation
        # contract; production must enable this additive path explicitly.
        self.temperature_window_enabled = temperature_window_enabled
        self.receipt_store = receipt_store
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        if receipt_write_probe_interval_seconds is None:
            try:
                receipt_write_probe_interval_seconds = float(
                    os.getenv("OATHCAST_RECEIPT_WRITE_PROBE_INTERVAL_SECONDS", "30")
                )
            except ValueError as exc:
                raise ValueError(
                    "OATHCAST_RECEIPT_WRITE_PROBE_INTERVAL_SECONDS must be numeric"
                ) from exc
        if (
            not math.isfinite(receipt_write_probe_interval_seconds)
            or receipt_write_probe_interval_seconds < 0
        ):
            raise ValueError(
                "receipt write probe interval must be finite and not negative"
            )
        self.receipt_write_probe_interval_seconds = receipt_write_probe_interval_seconds
        self._receipt_write_probe_lock = threading.Lock()
        self._receipt_write_probe_checked_at: float | None = None
        self._receipt_write_probe_status: dict[str, Any] | None = None
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
        if trusted_proxy_networks is None:
            configured_proxies = [
                item.strip()
                for item in os.getenv("OATHCAST_TRUSTED_PROXIES", "").split(",")
                if item.strip()
            ]
        elif isinstance(trusted_proxy_networks, str):
            configured_proxies = [trusted_proxy_networks]
        else:
            configured_proxies = list(trusted_proxy_networks)
        proxy_networks = [*DEFAULT_TRUSTED_PROXY_NETWORKS, *configured_proxies]
        try:
            self.trusted_proxy_networks = tuple(
                ipaddress.ip_network(network, strict=False) for network in proxy_networks
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "trusted proxy networks must be valid IP addresses or CIDR ranges"
            ) from exc
        self.release = release or current_release()

    def receipt_store_write_readiness(self, *, force: bool = False) -> dict[str, Any]:
        """Return a sanitized, briefly cached transactional-write status."""

        if self.receipt_store is None:
            return {
                "ready": False,
                "probe": "sqlite_transactional_write",
                "error": "receipt_store_unconfigured",
                "cached": False,
            }
        probe = getattr(self.receipt_store, "write_readiness", None)
        if not callable(probe):
            return {
                "ready": False,
                "probe": "sqlite_transactional_write",
                "error": "write_probe_unavailable",
                "cached": False,
            }

        with self._receipt_write_probe_lock:
            now = time.monotonic()
            if (
                not force
                and self._receipt_write_probe_status is not None
                and self._receipt_write_probe_checked_at is not None
                and now - self._receipt_write_probe_checked_at
                < self.receipt_write_probe_interval_seconds
            ):
                return {**self._receipt_write_probe_status, "cached": True}

            try:
                raw_status = probe()
            except Exception:
                raw_status = {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "error": "write_probe_failed",
                }
            if not isinstance(raw_status, dict):
                raw_status = {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "error": "invalid_write_probe_result",
                }
            rolled_back = raw_status.get("rolled_back") is True
            status: dict[str, Any] = {
                "ready": raw_status.get("ready") is True and rolled_back,
                "probe": "sqlite_transactional_write",
            }
            if isinstance(raw_status.get("rolled_back"), bool):
                status["rolled_back"] = raw_status["rolled_back"]
            if not status["ready"]:
                error = raw_status.get("error")
                if isinstance(error, str):
                    status["error"] = error
                elif raw_status.get("ready") is True and not rolled_back:
                    status["error"] = "rollback_unverified"
                else:
                    status["error"] = "write_unavailable"
            self._receipt_write_probe_status = status
            self._receipt_write_probe_checked_at = now
            return {**status, "cached": False}

    def _is_trusted_proxy(self, remote_address: str) -> bool:
        if not isinstance(remote_address, str):
            return False
        try:
            address = ipaddress.ip_address(remote_address)
        except (TypeError, ValueError):
            return False
        return address.is_loopback or any(address in network for network in self.trusted_proxy_networks)

    @staticmethod
    def _validated_forwarded_address(forwarded_for: str | None) -> str | None:
        """Return the first address from a syntactically valid X-Forwarded-For."""

        if not isinstance(forwarded_for, str) or not forwarded_for or len(forwarded_for) > 1024:
            return None
        values = [value.strip() for value in forwarded_for.split(",")]
        if not values or any(not value for value in values):
            return None
        try:
            addresses = [ipaddress.ip_address(value) for value in values]
        except ValueError:
            return None
        return str(addresses[0])

    def rate_limit_identity(
        self,
        *,
        remote_address: str,
        forwarded_for: str | None = None,
    ) -> str:
        """Return the client address used for rate limiting.

        Forwarded addresses are accepted only from a configured trusted proxy
        (including loopback). Direct callers cannot select their bucket by
        spoofing X-Forwarded-For.
        """

        identity = remote_address or "unknown"
        if self._is_trusted_proxy(identity):
            identity = self._validated_forwarded_address(forwarded_for) or identity
        return identity

    def rate_limit_key(
        self,
        *,
        remote_address: str,
        forwarded_for: str | None = None,
    ) -> str:
        """Return a non-sensitive limiter key derived from the client address."""

        identity = self.rate_limit_identity(
            remote_address=remote_address,
            forwarded_for=forwarded_for,
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

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

    def _fetch_window_one(
        self,
        request: ForecastWindowRequest,
        provider: str,
        request_id: str,
    ) -> ServiceWindowForecast:
        if provider not in self.verified_providers and not self.allow_unverified_providers:
            raise ValueError(
                f"{provider} is disabled until its window forecast semantics are validated"
            )
        adapter = self.window_adapters.get(provider)
        if adapter is None:
            raise ValueError(
                f"{provider} does not support complete 1-to-24-hour forecast windows"
            )
        retrieved_at = self.clock().astimezone(UTC)
        url = adapter.build_url(request, self._api_key_for(provider))
        payload = self.fetcher(url)
        forecast = adapter.parse(
            payload,
            request,
            issued_at=retrieved_at,
            retrieved_at=retrieved_at,
        )
        if (
            forecast.event_equivalence != "documented_hourly_window"
            and not self.allow_unverified_providers
        ):
            raise ValueError(
                f"{provider} is disabled until its window forecast semantics are validated"
            )
        forecast = replace(forecast, raw_payload_sha256=payload_hash(payload))
        return ServiceWindowForecast(
            request=request,
            forecast=forecast,
            raw_payload=payload,
            request_id=request_id,
        )

    def _fetch_temperature_window_one(
        self,
        request: TemperatureWindowRequest,
        provider: str,
        request_id: str,
        *,
        accepted_at: datetime | None = None,
    ) -> ServiceTemperatureWindowForecast:
        if provider not in self.verified_providers and not self.allow_unverified_providers:
            raise ValueError(
                f"{provider} is disabled until its temperature forecast semantics are validated"
            )
        adapter = self.temperature_window_adapters.get(provider)
        if adapter is None:
            raise ValueError(
                f"{provider} does not support 1-to-24-hour temperature forecasts"
            )
        issued_at = (
            self.clock().astimezone(UTC)
            if accepted_at is None
            else ensure_utc(accepted_at, "accepted_at")
        )
        url = adapter.build_url(request, self._api_key_for(provider))
        payload = self.fetcher(url)
        retrieved_at = self.clock().astimezone(UTC)
        forecast = adapter.parse(
            payload,
            request,
            issued_at=issued_at,
            retrieved_at=retrieved_at,
        )
        forecast = replace(forecast, raw_payload_sha256=payload_hash(payload))
        return ServiceTemperatureWindowForecast(
            request=request,
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
            try:
                stored = self.receipt_store.get(question.event_id)
            except ReceiptTampering:
                raise
            except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
            if stored is not None:
                try:
                    return self._service_forecast_from_receipt(stored, question)
                except (ReceiptConflict, ReceiptTampering):
                    raise
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    raise ReceiptStoreUnavailable("stored receipt evidence is malformed") from exc

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
            except ForecastCutoffPassed:
                raise
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
                continue
            try:
                return self._persist_receipt(result)
            except (ReceiptConflict, ReceiptStoreFull, ReceiptTampering):
                raise
            except (sqlite3.Error, ValueError, KeyError, TypeError, RuntimeError) as exc:
                # A receipt is the durable product contract. Its failure is
                # neither a retryable upstream-provider failure nor a reason to
                # fetch another provider response that also cannot be saved.
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
        raise ProviderUnavailable("; ".join(failures))

    def forecast_window(
        self,
        request: ForecastWindowRequest,
        *,
        request_id: str,
        requested_provider: str | None = None,
    ) -> ServiceWindowForecast:
        if requested_provider is not None and requested_provider not in self.window_adapters:
            raise ValueError(
                f"{requested_provider} does not support complete 1-to-24-hour forecast windows"
            )
        if self.receipt_store is not None:
            try:
                stored = self.receipt_store.get(request.event_id)
            except ReceiptTampering:
                raise
            except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
            if stored is not None:
                try:
                    return self._service_window_forecast_from_receipt(stored, request)
                except (ReceiptConflict, ReceiptTampering):
                    raise
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    raise ReceiptStoreUnavailable("stored receipt evidence is malformed") from exc

        now = self.clock().astimezone(UTC)
        if now >= request.forecast_cutoff:
            raise ForecastCutoffPassed(
                "forecast_cutoff_passed: new forecasts must be issued before "
                f"{format_timestamp(request.forecast_cutoff)}"
            )

        order = (
            [requested_provider]
            if requested_provider is not None
            else [provider for provider in self.provider_order if provider in self.window_adapters]
        )
        if not order:
            raise ProviderUnavailable(
                "no configured provider supports complete 1-to-24-hour forecast windows"
            )
        failures: list[str] = []
        for provider in order:
            try:
                result = self._fetch_window_one(request, provider, request_id)
                completed_at = self.clock().astimezone(UTC)
                if completed_at >= request.forecast_cutoff:
                    raise ForecastCutoffPassed(
                        "forecast_cutoff_passed: upstream response completed at or after "
                        f"{format_timestamp(request.forecast_cutoff)}"
                    )
            except ForecastCutoffPassed:
                raise
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
                continue
            try:
                return self._persist_window_receipt(result)
            except (ReceiptConflict, ReceiptStoreFull, ReceiptTampering):
                raise
            except (sqlite3.Error, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
        raise ProviderUnavailable("; ".join(failures))

    def forecast_temperature_window(
        self,
        request: TemperatureWindowRequest,
        *,
        request_id: str,
        requested_provider: str | None = None,
        accepted_at: datetime | None = None,
    ) -> ServiceTemperatureWindowForecast:
        if (
            requested_provider is not None
            and requested_provider not in self.temperature_window_adapters
        ):
            raise ValueError(
                f"{requested_provider} does not support 1-to-24-hour temperature forecasts"
            )
        if self.receipt_store is not None:
            try:
                stored = self.receipt_store.get(request.event_id)
            except ReceiptTampering:
                raise
            except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
            if stored is not None:
                try:
                    return self._service_temperature_window_forecast_from_receipt(
                        stored,
                        request,
                    )
                except (ReceiptConflict, ReceiptTampering):
                    raise
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    raise ReceiptStoreUnavailable(
                        "stored receipt evidence is malformed"
                    ) from exc

        request_accepted_at = (
            self.clock().astimezone(UTC)
            if accepted_at is None
            else ensure_utc(accepted_at, "accepted_at")
        )
        if request_accepted_at >= request.horizon_start:
            raise ForecastCutoffPassed(
                "forecast_cutoff_passed: new temperature forecasts must be issued before "
                f"{format_timestamp(request.horizon_start)}"
            )

        order = (
            [requested_provider]
            if requested_provider is not None
            else [
                provider
                for provider in self.provider_order
                if provider in self.temperature_window_adapters
            ]
        )
        if not order:
            raise ProviderUnavailable(
                "no configured provider supports 1-to-24-hour temperature forecasts"
            )
        failures: list[str] = []
        for provider in order:
            try:
                result = self._fetch_temperature_window_one(
                    request,
                    provider,
                    request_id,
                    accepted_at=request_accepted_at,
                )
            except ForecastCutoffPassed:
                raise
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
                continue
            try:
                return self._persist_temperature_window_receipt(result)
            except (ReceiptConflict, ReceiptStoreFull, ReceiptTampering):
                raise
            except (sqlite3.Error, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise ReceiptStoreUnavailable("receipt evidence is unavailable") from exc
        raise ProviderUnavailable("; ".join(failures))

    def _service_forecast_from_receipt(
        self,
        receipt: dict[str, Any],
        requested_question: ForecastQuestion,
    ) -> ServiceForecast:
        expected_digest = receipt.get("receipt_sha256")
        if expected_digest is not None and expected_digest != receipt_digest(receipt):
            raise ReceiptTampering("stored forecast receipt failed its integrity check")
        if receipt.get("schema_version") != 1:
            raise ReceiptConflict(
                f"event_id {requested_question.event_id!r} is already bound to a different forecast contract"
            )
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
        stored_public_response = receipt.get("public_response")
        if not isinstance(stored_public_response, dict):
            raise RuntimeError("stored forecast receipt has no public response object")
        return ServiceForecast(
            question=stored_question,
            forecast=forecast,
            raw_payload=receipt.get("raw_payload", {}),
            request_id=str(receipt["request_id"]),
            receipt_sha256=expected_digest,
            stored_public_response=dict(stored_public_response),
        )

    def _service_window_forecast_from_receipt(
        self,
        receipt: dict[str, Any],
        requested_window: ForecastWindowRequest,
    ) -> ServiceWindowForecast:
        expected_digest = receipt.get("receipt_sha256")
        if expected_digest is not None and expected_digest != receipt_digest(receipt):
            raise ReceiptTampering("stored forecast receipt failed its integrity check")
        if receipt.get("schema_version") != 2:
            raise ReceiptConflict(
                f"event_id {requested_window.event_id!r} is already bound to a different forecast contract"
            )
        stored_request = ForecastWindowRequest.from_dict(receipt["question"])
        if stored_request.to_dict() != requested_window.to_dict():
            raise ReceiptConflict(
                f"event_id {requested_window.event_id!r} is already bound to a different question"
            )
        forecast = CanonicalWindowForecast.from_dict(receipt["forecast"])
        stored_public_response = receipt.get("public_response")
        if not isinstance(stored_public_response, dict):
            raise RuntimeError("stored forecast receipt has no public response object")
        return ServiceWindowForecast(
            request=stored_request,
            forecast=forecast,
            raw_payload=receipt.get("raw_payload", {}),
            request_id=str(receipt["request_id"]),
            receipt_sha256=expected_digest,
            stored_public_response=dict(stored_public_response),
        )

    def _service_temperature_window_forecast_from_receipt(
        self,
        receipt: dict[str, Any],
        requested_window: TemperatureWindowRequest,
    ) -> ServiceTemperatureWindowForecast:
        expected_digest = receipt.get("receipt_sha256")
        if expected_digest is not None and expected_digest != receipt_digest(receipt):
            raise ReceiptTampering("stored forecast receipt failed its integrity check")
        if receipt.get("schema_version") != 3:
            raise ReceiptConflict(
                f"event_id {requested_window.event_id!r} is already bound to a different forecast contract"
            )
        stored_question = receipt["question"]
        if not isinstance(stored_question, dict):
            raise RuntimeError("stored temperature receipt question is not an object")
        if stored_question != _temperature_window_receipt_question(requested_window):
            raise ReceiptConflict(
                f"event_id {requested_window.event_id!r} is already bound to a different question"
            )
        resolved_request = receipt.get("resolved_request")
        if not isinstance(resolved_request, dict):
            raise RuntimeError("stored temperature receipt has no resolved request object")
        stored_request = TemperatureWindowRequest.from_dict(resolved_request)
        if _temperature_window_receipt_question(stored_request) != stored_question:
            raise RuntimeError(
                "stored temperature receipt question does not match its resolved request"
            )
        forecast = CanonicalTemperatureWindowForecast.from_dict(receipt["forecast"])
        stored_public_response = receipt.get("public_response")
        if not isinstance(stored_public_response, dict):
            raise RuntimeError("stored forecast receipt has no public response object")
        return ServiceTemperatureWindowForecast(
            request=stored_request,
            forecast=forecast,
            raw_payload=receipt.get("raw_payload", {}),
            request_id=str(receipt["request_id"]),
            receipt_sha256=expected_digest,
            stored_public_response=dict(stored_public_response),
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

    def _persist_window_receipt(
        self,
        result: ServiceWindowForecast,
    ) -> ServiceWindowForecast:
        if self.receipt_store is None:
            return result
        receipt = {
            "schema_version": 2,
            "created_at": format_timestamp(self.clock()),
            "request_id": result.request_id,
            "question": result.request.to_dict(),
            "forecast": result.forecast.to_dict(),
            "raw_payload": result.raw_payload,
            "public_response": result.to_public_response(),
        }
        receipt["receipt_sha256"] = receipt_digest(receipt)
        stored = self.receipt_store.save(receipt)
        return self._service_window_forecast_from_receipt(stored, result.request)

    def _persist_temperature_window_receipt(
        self,
        result: ServiceTemperatureWindowForecast,
    ) -> ServiceTemperatureWindowForecast:
        if self.receipt_store is None:
            return result
        receipt = {
            "schema_version": 3,
            "created_at": format_timestamp(self.clock()),
            "request_id": result.request_id,
            "question": _temperature_window_receipt_question(result.request),
            "resolved_request": result.request.to_dict(),
            "forecast": result.forecast.to_dict(),
            "raw_payload": result.raw_payload,
            "public_response": result.to_public_response(),
        }
        receipt["receipt_sha256"] = receipt_digest(receipt)
        stored = self.receipt_store.save(receipt)
        return self._service_temperature_window_forecast_from_receipt(
            stored,
            result.request,
        )


_MISSING = object()
KNOWN_FORECAST_QUERY_PARAMETERS = frozenset(
    {
        "event_id",
        "location_name",
        "lat",
        "latitude",
        "lon",
        "longitude",
        "start",
        "horizon_start",
        "end",
        "horizon_end",
        "cutoff",
        "forecast_cutoff",
        "threshold_mm",
        "operator",
        "provider",
        "forecast_hours",
        "hourly",
    }
)
TELEGRAPH_2T_QUERY_PARAMETERS = frozenset(
    {
        "event_id",
        "location_name",
        "lat",
        "latitude",
        "lon",
        "longitude",
        "provider",
        "forecast_hours",
        "hourly",
    }
)


def _validate_query_params(params: dict[str, list[str]]) -> None:
    for name, values in params.items():
        if not isinstance(name, str) or not name:
            raise ValueError("query parameter names must not be empty")
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError(f"query parameter {name!r} contains control characters")
        if name not in KNOWN_FORECAST_QUERY_PARAMETERS:
            raise ValueError(f"unknown query parameter: {name}")
        if not isinstance(values, (list, tuple)) or len(values) != 1:
            raise ValueError(f"query parameter {name!r} must appear exactly once")
        value = values[0]
        if not isinstance(value, str):
            raise ValueError(f"query parameter {name!r} must be text")
        if value == "":
            raise ValueError(f"query parameter {name!r} must not be empty")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"query parameter {name!r} contains control characters")


def _first_query_value(
    params: dict[str, list[str]],
    names: tuple[str, ...],
    default: str | None | object = _MISSING,
) -> str | None:
    present = [name for name in names if name in params]
    if len(present) > 1:
        raise ValueError(f"provide only one of the query parameters: {', '.join(present)}")
    if present:
        return params[present[0]][0]
    if default is not _MISSING:
        return default  # type: ignore[return-value]
    raise ValueError(f"missing query parameter: {' or '.join(names)}")


def _parse_query_timestamp(name: str, value: str) -> datetime:
    try:
        return parse_timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc


def _parse_finite_number(name: str, value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be a finite number")
    return parsed


def _parse_coordinate(name: str, value: str, minimum: float, maximum: float) -> float:
    parsed = _parse_finite_number(name, value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be a finite number between {minimum:g} and {maximum:g}")
    return parsed


def _bounded_query_text(name: str, value: str, maximum: int) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return value


def default_event_id(question: ForecastQuestion) -> str:
    """Return a deterministic ID bound to every canonical question field.

    The generated identity is the only field omitted from the digest; including
    it would make deriving the identity circular.
    """

    canonical = question.to_dict()
    canonical.pop("event_id", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"request-{hashlib.sha256(encoded).hexdigest()}"


def default_window_event_id(request: ForecastWindowRequest) -> str:
    """Return a versioned identity that cannot collide with schema-v1 point receipts."""

    canonical = request.to_dict()
    canonical.pop("event_id", None)
    canonical["request_contract"] = "forecast_window_v2"
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"window-request-{hashlib.sha256(encoded).hexdigest()}"


def default_temperature_window_event_id(request: TemperatureWindowRequest) -> str:
    """Return a schema-v3 identity distinct from point and precipitation windows."""

    canonical = _temperature_window_receipt_question(request)
    canonical.pop("event_id", None)
    canonical["reference_time"] = format_timestamp(request.reference_time)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"temperature-request-{hashlib.sha256(encoded).hexdigest()}"


def _temperature_window_receipt_question(
    request: TemperatureWindowRequest,
) -> dict[str, Any]:
    """Return the stable client-authored portion of a schema-v3 request."""

    return {
        "request_contract": "temperature_window_v3",
        "event_id": request.event_id,
        "location_name": request.location_name,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "forecast_hours": request.forecast_hours,
        "hourly": "2t",
        "timezone": request.timezone,
        "spatial_semantics": request.spatial_semantics,
    }


def _uses_telegraph_2t_query(params: dict[str, list[str]]) -> bool:
    return "forecast_hours" in params or "hourly" in params


def telegraph_2t_window_request_from_query(
    params: dict[str, list[str]],
    *,
    reference_time: datetime,
) -> TemperatureWindowRequest:
    """Normalize Telegraph's lat/lon + forecast_hours + hourly=2t request."""

    _validate_query_params(params)
    unexpected = sorted(set(params).difference(TELEGRAPH_2T_QUERY_PARAMETERS))
    if unexpected:
        raise ValueError(
            "forecast_hours/hourly requests cannot be mixed with query parameter: "
            f"{unexpected[0]}"
        )
    hourly = _first_query_value(params, ("hourly",))
    if hourly != "2t":
        raise ValueError("hourly must be exactly 2t")
    forecast_hours_value = _first_query_value(params, ("forecast_hours",))
    if not forecast_hours_value.isascii() or not forecast_hours_value.isdigit():
        raise ValueError("forecast_hours must be a whole number between 1 and 24")
    forecast_hours = int(forecast_hours_value)
    if not 1 <= forecast_hours <= 24:
        raise ValueError("forecast_hours must be a whole number between 1 and 24")

    observed_at = ensure_utc(reference_time, "reference_time").replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    event_id_value = _first_query_value(params, ("event_id",), default=None)
    event_id = (
        None
        if event_id_value is None
        else _bounded_query_text("event_id", event_id_value, MAX_EVENT_ID_LENGTH)
    )
    request = TemperatureWindowRequest(
        event_id=event_id or "__generated_telegraph_2t_event_id__",
        location_name=_bounded_query_text(
            "location_name",
            _first_query_value(
                params,
                ("location_name",),
                default="requested location",
            ),
            MAX_LOCATION_NAME_LENGTH,
        ),
        latitude=_parse_coordinate(
            "latitude",
            _first_query_value(params, ("latitude", "lat")),
            -90,
            90,
        ),
        longitude=_parse_coordinate(
            "longitude",
            _first_query_value(params, ("longitude", "lon")),
            -180,
            180,
        ),
        forecast_hours=forecast_hours,
        reference_time=observed_at,
    )
    return (
        request
        if event_id is not None
        else replace(request, event_id=default_temperature_window_event_id(request))
    )


def question_from_query(params: dict[str, list[str]]) -> ForecastQuestion:
    _validate_query_params(params)

    start = _parse_query_timestamp(
        "horizon_start",
        _first_query_value(params, ("horizon_start", "start")),
    )
    end = _parse_query_timestamp(
        "horizon_end",
        _first_query_value(params, ("horizon_end", "end")),
    )
    cutoff_value = _first_query_value(
        params,
        ("forecast_cutoff", "cutoff"),
        default=None,
    )
    cutoff = (
        _parse_query_timestamp("forecast_cutoff", cutoff_value)
        if cutoff_value is not None
        else start - timedelta(hours=1)
    )
    event_id_value = _first_query_value(params, ("event_id",), default=None)
    event_id = (
        None
        if event_id_value is None
        else _bounded_query_text("event_id", event_id_value, MAX_EVENT_ID_LENGTH)
    )
    question = ForecastQuestion(
        event_id=event_id or "__generated_event_id__",
        location_name=_bounded_query_text(
            "location_name",
            _first_query_value(params, ("location_name",), default="requested location"),
            MAX_LOCATION_NAME_LENGTH,
        ),
        latitude=_parse_coordinate(
            "latitude",
            _first_query_value(params, ("latitude", "lat")),
            -90,
            90,
        ),
        longitude=_parse_coordinate(
            "longitude",
            _first_query_value(params, ("longitude", "lon")),
            -180,
            180,
        ),
        horizon_start=start,
        horizon_end=end,
        forecast_cutoff=cutoff,
        threshold_mm=_parse_finite_number(
            "threshold_mm",
            _first_query_value(params, ("threshold_mm",), default="0.1"),
        ),
        operator=_first_query_value(
            params,
            ("operator",),
            default=SUPPORTED_EVENT_OPERATOR,
        ),
    )
    return question if event_id is not None else replace(question, event_id=default_event_id(question))


def window_request_from_query(
    params: dict[str, list[str]],
) -> ForecastWindowRequest:
    _validate_query_params(params)

    start = _parse_query_timestamp(
        "horizon_start",
        _first_query_value(params, ("horizon_start", "start")),
    )
    end = _parse_query_timestamp(
        "horizon_end",
        _first_query_value(params, ("horizon_end", "end")),
    )
    cutoff_value = _first_query_value(
        params,
        ("forecast_cutoff", "cutoff"),
        default=None,
    )
    cutoff = (
        _parse_query_timestamp("forecast_cutoff", cutoff_value)
        if cutoff_value is not None
        else start - timedelta(hours=1)
    )
    event_id_value = _first_query_value(params, ("event_id",), default=None)
    event_id = (
        None
        if event_id_value is None
        else _bounded_query_text("event_id", event_id_value, MAX_EVENT_ID_LENGTH)
    )
    request = ForecastWindowRequest(
        event_id=event_id or "__generated_window_event_id__",
        location_name=_bounded_query_text(
            "location_name",
            _first_query_value(
                params,
                ("location_name",),
                default="requested location",
            ),
            MAX_LOCATION_NAME_LENGTH,
        ),
        latitude=_parse_coordinate(
            "latitude",
            _first_query_value(params, ("latitude", "lat")),
            -90,
            90,
        ),
        longitude=_parse_coordinate(
            "longitude",
            _first_query_value(params, ("longitude", "lon")),
            -180,
            180,
        ),
        horizon_start=start,
        horizon_end=end,
        forecast_cutoff=cutoff,
        threshold_mm=_parse_finite_number(
            "threshold_mm",
            _first_query_value(params, ("threshold_mm",), default="0.1"),
        ),
        operator=_first_query_value(
            params,
            ("operator",),
            default=SUPPORTED_EVENT_OPERATOR,
        ),
    )
    return (
        request
        if event_id is not None
        else replace(request, event_id=default_window_event_id(request))
    )


def forecast_request_from_query(
    params: dict[str, list[str]],
    *,
    reference_time: datetime | None = None,
) -> ForecastQuestion | ForecastWindowRequest | TemperatureWindowRequest:
    """Dispatch registered point, legacy window, and Telegraph 2t contracts."""

    if _uses_telegraph_2t_query(params):
        if reference_time is None:
            raise ValueError("reference_time is required for forecast_hours/hourly requests")
        return telegraph_2t_window_request_from_query(
            params,
            reference_time=reference_time,
        )
    _validate_query_params(params)
    start = _parse_query_timestamp(
        "horizon_start",
        _first_query_value(params, ("horizon_start", "start")),
    )
    end = _parse_query_timestamp(
        "horizon_end",
        _first_query_value(params, ("horizon_end", "end")),
    )
    if end - start == timedelta(hours=1):
        return question_from_query(params)
    return window_request_from_query(params)


def _parse_request_query(query: str) -> dict[str, list[str]]:
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query string must be at most {MAX_QUERY_LENGTH} characters")
    try:
        return parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=MAX_QUERY_PARAMETERS,
        )
    except ValueError as exc:
        if "Max number of fields" in str(exc):
            raise ValueError(
                f"query must contain at most {MAX_QUERY_PARAMETERS} parameters"
            ) from exc
        raise ValueError("query contains malformed parameters") from exc


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
        response_headers = headers or {}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-OathCast-Release-ID", self.service.release.release_id)
        for name, value in response_headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)
        record: dict[str, Any] = {
            "event": "http_response",
            "method": getattr(self, "command", "GET"),
            "path": urlparse(getattr(self, "path", "")).path,
            "response_bytes": len(encoded),
            "status": status,
        }
        request_id = response_headers.get("X-OathCast-Request-ID")
        if request_id is None and isinstance(payload.get("request_id"), str):
            request_id = payload["request_id"]
        if (
            request_id
            and len(request_id) <= 128
            and all(ord(char) >= 32 for char in request_id)
        ):
            record["request_id"] = request_id
        LOGGER.info(json.dumps(record, sort_keys=True, separators=(",", ":")))

    def _forwarded_for_header(self) -> str | None:
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            values = get_all("X-Forwarded-For") or []
            if len(values) != 1:
                return None
            return values[0]
        return self.headers.get("X-Forwarded-For")

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
                    "temperature_window_enabled": self.service.temperature_window_enabled,
                    "release": self.service.release.to_dict(),
                },
            )
            return
        if parsed.path == "/readyz":
            ready = bool(self.service.receipt_store is not None or not self.service.require_auth)
            payload: dict[str, Any] = {
                "ready": ready,
                "auth_configured": bool(self.service.auth_tokens),
                "receipt_store_configured": self.service.receipt_store is not None,
                "release": self.service.release.to_dict(),
            }
            if self.service.receipt_store is not None:
                # Capacity is surfaced so an operator (and the canary) sees the
                # store filling up before every new forecast starts returning
                # 507. A full store is genuinely not-ready: the service will
                # refuse new work. This does not restart the container --
                # Docker's HEALTHCHECK probes /healthz, not /readyz -- so a
                # capacity stall stays visible instead of turning into a
                # restart loop.
                try:
                    capacity = self.service.receipt_store.capacity()
                except Exception:
                    ready = False
                    payload["receipt_store"] = {"error": "capacity_unavailable"}
                else:
                    payload["receipt_store"] = capacity
                    if not capacity.get("accepting_new_receipts", True):
                        ready = False
                    write_status = self.service.receipt_store_write_readiness()
                    payload["receipt_store_write"] = write_status
                    if not write_status.get("ready", False):
                        ready = False
                payload["ready"] = ready
            self._send_json(200 if ready else 503, payload)
            return
        if parsed.path not in FORECAST_PATHS:
            self._send_json(404, {"error": "not_found"})
            return

        request_id = self.headers.get("X-Request-ID", "").strip()
        if not request_id or len(request_id) > 128 or any(ord(char) < 32 for char in request_id):
            request_id = f"http-{uuid.uuid4().hex}"
        remote_address = self.client_address[0]
        rate_key = self.service.rate_limit_key(
            remote_address=remote_address,
            forwarded_for=self._forwarded_for_header(),
        )
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
            if len(self.path) > MAX_REQUEST_TARGET_LENGTH:
                raise ValueError(
                    f"request target must be at most {MAX_REQUEST_TARGET_LENGTH} characters"
                )
            params = _parse_request_query(parsed.query)
            requested_provider = params.get("provider", [None])[0]
            accepted_at: datetime | None = None
            if _uses_telegraph_2t_query(params):
                if not self.service.temperature_window_enabled:
                    raise ValueError("temperature compatibility window is disabled")
                accepted_at = self.service.clock().astimezone(UTC)
                request = forecast_request_from_query(
                    params,
                    reference_time=accepted_at,
                )
            else:
                request = question_from_query(params)
            if isinstance(request, TemperatureWindowRequest):
                result = self.service.forecast_temperature_window(
                    request,
                    request_id=request_id,
                    requested_provider=requested_provider,
                    accepted_at=accepted_at,
                )
            else:
                result = self.service.forecast(
                    request,
                    request_id=request_id,
                    requested_provider=requested_provider,
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
        except ReceiptTampering as exc:
            _log_request_failure(
                "receipt_integrity_failure",
                request_id=request_id,
                path=parsed.path,
                error=exc,
            )
            self._send_json(
                500,
                {"error": "receipt_integrity_failure", "request_id": request_id},
                headers={"X-OathCast-Request-ID": request_id},
            )
        except ReceiptStoreFull:
            # Fail closed rather than serve an unrecorded forecast. A forecast
            # without a receipt is not a product this service offers, so
            # returning it silently would break the evidence chain.
            self._send_json(
                507,
                {"error": "receipt_store_full", "request_id": request_id},
                headers={"X-OathCast-Request-ID": request_id},
            )
        except ReceiptStoreUnavailable as exc:
            _log_request_failure(
                "receipt_store_unavailable",
                request_id=request_id,
                path=parsed.path,
                error=exc,
            )
            self._send_json(
                503,
                {"error": "receipt_store_unavailable", "request_id": request_id},
                headers={"X-OathCast-Request-ID": request_id},
            )
        except ProviderUnavailable:
            self._send_json(502, {"error": "provider_unavailable", "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc), "request_id": request_id}, headers={"X-OathCast-Request-ID": request_id})
        except Exception as exc:  # noqa: BLE001 - final HTTP safety boundary
            _log_request_failure(
                "forecast_request_failed",
                request_id=request_id,
                path=parsed.path,
                error=exc,
            )
            self._send_json(
                500,
                {"error": "internal_error", "request_id": request_id},
                headers={"X-OathCast-Request-ID": request_id},
            )

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def env_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def env_cap(name: str, default: int | None) -> int | None:
        """Read a receipt-store capacity cap from the environment.

        An unset variable keeps the built-in default; ``none``/``off``/``0``
        disables the cap explicitly. An unparseable value is a startup error
        rather than a silent fallback -- a typo must not quietly remove a
        capacity bound.
        """

        raw = os.getenv(name)
        if raw is None:
            return default
        cleaned = raw.strip().lower()
        if cleaned in {"none", "off", "0", ""}:
            return None
        return int(cleaned)

    receipt_path = os.getenv("OATHCAST_RECEIPT_DB", "data/oathcast/receipts.sqlite3")
    service = ForecastService(
        require_auth=env_flag("OATHCAST_REQUIRE_AUTH", True),
        temperature_window_enabled=env_flag(
            "OATHCAST_ENABLE_TEMPERATURE_WINDOW", False
        ),
        receipt_store=SqliteReceiptStore(
            receipt_path,
            max_rows=env_cap("OATHCAST_RECEIPT_MAX_ROWS", DEFAULT_MAX_RECEIPT_ROWS),
            max_bytes=env_cap("OATHCAST_RECEIPT_MAX_BYTES", DEFAULT_MAX_RECEIPT_BYTES),
        ),
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
