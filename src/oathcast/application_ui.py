"""Safe public-UI adapter for the private OathCast Application gateway.

The browser never receives the gateway token. This adapter is the only bridge
between the public decision UI and the loopback gateway, and it accepts only a
fixed local URL, a bounded JSON request, and the allow-listed result contract.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from oathcast.decision_ui import DecisionInput, DecisionUnavailable


GATEWAY_PATH = "/v1/application/forecast"
HEALTH_PATH = "/healthz"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8790" + GATEWAY_PATH
MAX_TIMEOUT_SECONDS = 45.0
PRINCIPAL = "judge-public"


class _RejectRedirects(HTTPRedirectHandler):
    """Keep a loopback-only integration from silently following a redirect."""

    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _gateway_health_url(gateway_url: str) -> str:
    parsed = urlsplit(gateway_url)
    return urlunsplit((parsed.scheme, parsed.netloc, HEALTH_PATH, "", ""))


def _validate_gateway_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("gateway URL is invalid")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("gateway URL must be plain HTTP on loopback")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("gateway URL must target loopback")
    if parsed.path != GATEWAY_PATH or parsed.query or parsed.fragment:
        raise ValueError("gateway URL must use the fixed Application path")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("gateway URL has an invalid port") from exc
    return value


class LoopbackApplicationRunner:
    """Call the authenticated gateway without exposing its credentials."""

    public_mode = "live"

    def __init__(
        self,
        gateway_url: str,
        app_token: str,
        *,
        timeout_seconds: float = 25.0,
        principal: str = PRINCIPAL,
    ) -> None:
        self.gateway_url = _validate_gateway_url(gateway_url)
        if not isinstance(app_token, str) or not 32 <= len(app_token.encode("utf-8")) <= 512:
            raise ValueError("application UI token must be 32-512 bytes")
        if any(ord(character) < 32 or ord(character) == 127 for character in app_token):
            raise ValueError("application UI token contains a control character")
        if not isinstance(principal, str) or not principal or len(principal) > 128:
            raise ValueError("principal is invalid")
        if any(ord(character) < 32 or ord(character) == 127 for character in principal):
            raise ValueError("principal contains a control character")
        self._app_token = app_token
        self._principal = principal
        self._timeout = min(max(float(timeout_seconds), 1.0), MAX_TIMEOUT_SECONDS)
        self._opener = build_opener(_RejectRedirects())
        self._ready = self._probe()

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "LoopbackApplicationRunner | None":
        env = os.environ if environment is None else environment
        token = env.get("OATHCAST_APPLICATION_UI_TOKEN", "")
        if not token:
            return None
        try:
            return cls(
                env.get("OATHCAST_APPLICATION_UI_GATEWAY_URL", DEFAULT_GATEWAY_URL),
                token,
                timeout_seconds=float(env.get("OATHCAST_APPLICATION_UI_TIMEOUT", "25")),
                principal=env.get("OATHCAST_APPLICATION_UI_PRINCIPAL", PRINCIPAL),
            )
        except (TypeError, ValueError, OverflowError):
            return None

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def telegraph_configured(self) -> bool:
        return self._ready

    def _open(self, request: Request):
        try:
            return self._opener.open(request, timeout=self._timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise DecisionUnavailable("the private Application gateway is unavailable") from exc

    def _probe(self) -> bool:
        request = Request(_gateway_health_url(self.gateway_url), method="GET")
        try:
            with self._opener.open(request, timeout=min(self._timeout, 3.0)) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read(4096).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, ValueError, TypeError):
            return False
        return bool(
            isinstance(payload, Mapping)
            and payload.get("ready") is True
            and payload.get("public_ui_enabled") is False
            and payload.get("payment_boundary") == "private_unix_socket"
        )

    @staticmethod
    def _payload(request: DecisionInput) -> dict[str, object]:
        return {
            "activity": request.activity,
            "location": request.location,
            "latitude": request.latitude,
            "longitude": request.longitude,
            "local_datetime": request.local_datetime.isoformat(),
            "risk_threshold_percent": request.risk_threshold_percent,
            "consent": request.consent,
        }

    def __call__(self, request: DecisionInput) -> Mapping[str, object]:
        payload = self._payload(request)
        idempotency_key = "public-" + hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()[:60]
        body = _canonical_json(payload).encode("utf-8")
        gateway_request = Request(
            self.gateway_url,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._app_token,
                "X-OathCast-Principal": self._principal,
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        try:
            with self._open(gateway_request) as response:
                response_body = response.read(16 * 1024 + 1)
                if len(response_body) > 16 * 1024:
                    raise DecisionUnavailable("the private Application gateway returned an oversized result")
                result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise DecisionUnavailable("the private Application gateway returned an invalid result") from exc
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise DecisionUnavailable("the private Application gateway could not complete the decision")
        return result


__all__ = [
    "DEFAULT_GATEWAY_URL",
    "GATEWAY_PATH",
    "LoopbackApplicationRunner",
]
