"""Request and response adapters for discovered Telegraph weather Miners.

Telegraph forwards query parameters to a Miner, but the weather Miners do not
share one response schema.  The adapters in this module keep provider-specific
request construction and response interpretation out of the routing policy.

Known schemas are deliberately strict: a temperature series is useful context
but is not a precipitation probability.  Only an explicitly comparable
precipitation field can become a consensus input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from oathcast.forecast import ForecastQuestion, UTC, ensure_utc, format_timestamp, parse_timestamp
from oathcast.protocol import ProtocolResultEnvelope


PROBABILITY_PATTERN = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
PROBABILITY_KEYS = (
    "probability",
    "precipitation_probability",
    "probability_of_precipitation",
    "rain_probability",
    "probability_of_rain",
    "pop",
)
PERCENT_PROBABILITY_KEYS = ("chance_of_rain", "rain_chance", "precipitation_chance")


@dataclass(frozen=True)
class AdaptedMinerResponse:
    """The part of a Miner response that is safe for application consensus."""

    probability: float | None
    probability_comparable: bool
    validity_reason: str | None = None
    parser_version: str = "miner_response_adapter_v1"

    @property
    def has_comparable_probability(self) -> bool:
        """Whether ``probability`` is a comparable precipitation signal."""

        return self.probability_comparable and self.probability is not None

    @property
    def comparable_precipitation_probability(self) -> bool:
        """Descriptive alias for callers that need the domain meaning."""

        return self.has_comparable_probability


class MinerResponseAdapter(Protocol):
    """Interface implemented by request/response schema adapters."""

    name: str
    endpoint_name: str | None
    parser_version: str

    def build_params(self, question: ForecastQuestion) -> dict[str, Any]:
        ...

    def parse_response(
        self,
        raw_response: Any,
        question: ForecastQuestion,
    ) -> AdaptedMinerResponse:
        ...


def _body(raw_response: Any) -> Any:
    if isinstance(raw_response, ProtocolResultEnvelope):
        return raw_response.body
    return raw_response


def _content(raw_response: Any) -> str:
    raw_response = _body(raw_response)
    if isinstance(raw_response, str):
        return raw_response.strip()
    if isinstance(raw_response, dict):
        content = raw_response.get("content")
        if isinstance(content, str):
            return content.strip()
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
        return json.dumps(raw_response, sort_keys=True, separators=(",", ":"))
    return str(raw_response).strip()


def extract_generic_probability(raw_response: Any) -> float | None:
    """Retain the old schema-agnostic extractor for unknown Miners."""

    raw_response = _body(raw_response)
    if isinstance(raw_response, dict):
        for key in PROBABILITY_KEYS:
            probability = raw_response.get(key)
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probability = float(probability)
                if 0 <= probability <= 1:
                    return probability
                if 1 < probability <= 100 and key != "probability":
                    return probability / 100
        for key in PERCENT_PROBABILITY_KEYS:
            probability = raw_response.get(key)
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probability = float(probability)
                if 0 <= probability <= 100:
                    return probability / 100
        for key in ("data", "result", "forecast", "prediction", "output"):
            nested = raw_response.get(key)
            probability = extract_generic_probability(nested)
            if probability is not None:
                return probability
        choices = raw_response.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                probability = extract_generic_probability(choice)
                if probability is not None:
                    return probability
    match = PROBABILITY_PATTERN.search(_content(raw_response))
    if match is None:
        return None
    percentage = float(match.group(1))
    return percentage / 100 if 0 <= percentage <= 100 else None


def _generic_params(question: ForecastQuestion) -> dict[str, Any]:
    """Build the legacy query shape used when no schema is known."""

    return {
        "event_id": question.event_id,
        "location_name": question.location_name,
        "lat": f"{question.latitude:.6f}",
        "lon": f"{question.longitude:.6f}",
        "horizon_start": format_timestamp(question.horizon_start),
        "horizon_end": format_timestamp(question.horizon_end),
        "forecast_cutoff": format_timestamp(question.forecast_cutoff),
        "threshold_mm": f"{question.threshold_mm:g}",
    }


class GenericMinerAdapter:
    """Fallback adapter for Miners whose schema is not known to OathCast."""

    name = "generic"
    endpoint_name = None
    parser_version = "probability_extractor_v1"

    def build_params(self, question: ForecastQuestion) -> dict[str, Any]:
        return _generic_params(question)

    def parse_response(
        self,
        raw_response: Any,
        question: ForecastQuestion,
    ) -> AdaptedMinerResponse:
        del question
        probability = extract_generic_probability(raw_response)
        reason = None if probability is not None else "response has no valid probability"
        return AdaptedMinerResponse(
            probability=probability,
            probability_comparable=probability is not None,
            validity_reason=reason,
            parser_version=self.parser_version,
        )

    def build_url(
        self,
        base_url: str,
        endpoint: str,
        question: ForecastQuestion,
    ) -> str:
        return _build_url(base_url, endpoint, self.build_params(question))


def _build_url(base_url: str, endpoint: str, params: dict[str, Any]) -> str:
    query = urlencode(params)
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}?{query}"


class WeatherApiMinerAdapter:
    """Adapter for live Telegraph Miner 212's WeatherAPI forecast schema."""

    name = "weatherapi"
    miner_id = "212"
    endpoint_name = "forecast"
    parser_version = "weatherapi_miner_response_v1"

    def build_params(self, question: ForecastQuestion) -> dict[str, Any]:
        requested_days = (
            question.horizon_end.date() - question.forecast_cutoff.date()
        ).days + 1
        return {
            "q": f"{question.latitude:.6f},{question.longitude:.6f}",
            # WeatherAPI accepts 1-14 forecast days. A capped request remains
            # safe: parse_response still refuses a payload without the exact
            # requested hour instead of substituting a nearby forecast.
            "days": str(min(14, max(1, requested_days))),
        }

    def build_url(
        self,
        base_url: str,
        question: ForecastQuestion,
        endpoint: str | None = None,
    ) -> str:
        return _build_url(base_url, endpoint or self.endpoint_name, self.build_params(question))

    @staticmethod
    def _hour_timestamp(hour: dict[str, Any], timezone_name: str | None) -> datetime | None:
        if hour.get("time_epoch") is not None:
            try:
                return datetime.fromtimestamp(float(hour["time_epoch"]), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                return None

        raw_time = hour.get("time")
        if not isinstance(raw_time, str) or not timezone_name:
            return None
        try:
            local_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        try:
            return local_time.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(UTC)
        except ZoneInfoNotFoundError:
            return None

    @staticmethod
    def _probability(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric) or not 0 <= numeric <= 100:
            return None
        return numeric / 100

    def parse_response(
        self,
        raw_response: Any,
        question: ForecastQuestion,
    ) -> AdaptedMinerResponse:
        payload = _body(raw_response)
        if not isinstance(payload, dict):
            return AdaptedMinerResponse(
                probability=None,
                probability_comparable=False,
                validity_reason="WeatherAPI response is not a JSON object",
                parser_version=self.parser_version,
            )

        location = payload.get("location")
        forecast = payload.get("forecast")
        days = forecast.get("forecastday") if isinstance(forecast, dict) else None
        timezone_name = location.get("tz_id") if isinstance(location, dict) else None
        if not isinstance(days, list):
            return AdaptedMinerResponse(
                probability=None,
                probability_comparable=False,
                validity_reason="WeatherAPI response is missing forecastday",
                parser_version=self.parser_version,
            )
        if timezone_name is not None and not isinstance(timezone_name, str):
            timezone_name = None

        target = ensure_utc(question.horizon_start, "horizon_start")
        matches: list[dict[str, Any]] = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("hour"), list):
                continue
            for hour in day["hour"]:
                if not isinstance(hour, dict):
                    continue
                timestamp = self._hour_timestamp(hour, timezone_name)
                if timestamp is not None and ensure_utc(timestamp, "WeatherAPI hour") == target:
                    matches.append(hour)

        if len(matches) != 1:
            reason = (
                "WeatherAPI did not return exactly one forecast hour for the requested time"
            )
            return AdaptedMinerResponse(
                probability=None,
                probability_comparable=False,
                validity_reason=reason,
                parser_version=self.parser_version,
            )

        probability = self._probability(matches[0].get("chance_of_rain"))
        if probability is None:
            return AdaptedMinerResponse(
                probability=None,
                probability_comparable=False,
                validity_reason=(
                    "WeatherAPI requested hour has no comparable chance_of_rain probability"
                ),
                parser_version=self.parser_version,
            )
        return AdaptedMinerResponse(
            probability=probability,
            probability_comparable=True,
            parser_version=self.parser_version,
        )


_PRECIPITATION_PROBABILITY_KEYS = {
    "chance_of_rain",
    "rain_chance",
    "precipitation_chance",
    "rain_probability",
    "probability_of_rain",
    "precipitation_probability",
    "probability_of_precipitation",
    "precipitation_probability_percent",
    "rain_probability_percent",
    "pop",
}
_PRECIPITATION_CONTEXT_KEYS = {"precipitation", "precip", "rain", "rainfall"}
_PROBABILITY_IN_CONTEXT_KEYS = {"probability", "prob", "chance"}
_TIME_KEYS = {"time", "times", "timestamp", "timestamps", "valid_time", "valid_times"}


def _normalized_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        try:
            return ensure_utc(value, "Miner forecast time")
        except ValueError:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return parse_timestamp(value)
        except (TypeError, ValueError):
            return None
    return None


def _local_times(payload: dict[str, Any]) -> list[Any] | None:
    for key, value in payload.items():
        if _normalized_key(key) in _TIME_KEYS:
            if isinstance(value, list):
                return value
            return [value]
    return None


def _probability_from_value(value: Any, key: str) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    is_percent = key.endswith("_percent") or key in {
        "chance_of_rain",
        "rain_chance",
        "precipitation_chance",
    }
    if is_percent:
        return numeric / 100 if 0 <= numeric <= 100 else None
    return numeric if 0 <= numeric <= 1 else None


def _value_for_target(
    value: Any,
    key: str,
    times: list[Any] | None,
    target: datetime,
) -> float | None:
    if isinstance(value, (list, tuple)):
        values = list(value)
        if times is not None:
            if len(values) != len(times):
                return None
            indices = [
                index
                for index, timestamp in enumerate(times)
                if _parse_time(timestamp) == target
            ]
            if len(indices) != 1:
                return None
            return _probability_from_value(values[indices[0]], key)
        if len(values) == 1:
            return _probability_from_value(values[0], key)
        return None
    if times is not None:
        if len(times) != 1 or _parse_time(times[0]) != target:
            return None
    return _probability_from_value(value, key)


def _find_precipitation_probability(
    value: Any,
    target: datetime,
    *,
    inherited_times: list[Any] | None = None,
    precipitation_context: bool = False,
) -> float | None:
    if isinstance(value, dict):
        times = _local_times(value) or inherited_times
        for raw_key, child in value.items():
            key = _normalized_key(raw_key)
            if key in _PRECIPITATION_PROBABILITY_KEYS:
                probability = _value_for_target(child, key, times, target)
                if probability is not None:
                    return probability
            elif precipitation_context and key in _PROBABILITY_IN_CONTEXT_KEYS:
                probability = _value_for_target(child, key, times, target)
                if probability is not None:
                    return probability

        for raw_key, child in value.items():
            key = _normalized_key(raw_key)
            if isinstance(child, (dict, list, tuple)):
                probability = _find_precipitation_probability(
                    child,
                    target,
                    inherited_times=times,
                    precipitation_context=precipitation_context or key in _PRECIPITATION_CONTEXT_KEYS,
                )
                if probability is not None:
                    return probability
        return None

    if isinstance(value, (list, tuple)):
        for child in value:
            if isinstance(child, (dict, list, tuple)):
                probability = _find_precipitation_probability(
                    child,
                    target,
                    inherited_times=inherited_times,
                    precipitation_context=precipitation_context,
                )
                if probability is not None:
                    return probability
    return None


class ZeusMinerAdapter:
    """Adapter for live Telegraph Miner 18's Zeus ``predict`` schema."""

    name = "zeus"
    miner_id = "18"
    endpoint_name = "predict"
    parser_version = "zeus_miner_response_v1"

    def build_params(self, question: ForecastQuestion) -> dict[str, Any]:
        forecast_hours = math.ceil(
            (question.horizon_end - question.forecast_cutoff).total_seconds() / 3600
        )
        return {
            "lat": f"{question.latitude:.6f}",
            "lon": f"{question.longitude:.6f}",
            "hourly": "2t",
            # Telegraph documents this as hours ahead from the forecast run,
            # not a count of event-boundary points. Using the decision cutoff
            # as the issuance anchor ensures the requested window is covered.
            "forecast_hours": str(max(1, forecast_hours)),
        }

    def build_url(
        self,
        base_url: str,
        question: ForecastQuestion,
        endpoint: str | None = None,
    ) -> str:
        return _build_url(base_url, endpoint or self.endpoint_name, self.build_params(question))

    def parse_response(
        self,
        raw_response: Any,
        question: ForecastQuestion,
    ) -> AdaptedMinerResponse:
        target = ensure_utc(question.horizon_start, "horizon_start")
        probability = _find_precipitation_probability(_body(raw_response), target)
        if probability is None:
            return AdaptedMinerResponse(
                probability=None,
                probability_comparable=False,
                validity_reason=(
                    "Zeus response is supporting context only; it has no comparable "
                    "precipitation probability"
                ),
                parser_version=self.parser_version,
            )
        return AdaptedMinerResponse(
            probability=probability,
            probability_comparable=True,
            parser_version=self.parser_version,
        )


GENERIC_MINER_ADAPTER = GenericMinerAdapter()
WEATHERAPI_MINER_ADAPTER = WeatherApiMinerAdapter()
ZEUS_MINER_ADAPTER = ZeusMinerAdapter()
MINER_ADAPTERS: dict[str, MinerResponseAdapter] = {
    "212": WEATHERAPI_MINER_ADAPTER,
    "18": ZEUS_MINER_ADAPTER,
}


def adapter_for_miner(miner_id: str | int) -> MinerResponseAdapter:
    """Return a schema adapter, preserving the generic fallback for unknown IDs."""

    return MINER_ADAPTERS.get(str(miner_id), GENERIC_MINER_ADAPTER)


# Friendly aliases for callers that prefer the provider spelling.
WeatherAPIMinerAdapter = WeatherApiMinerAdapter
ZeusWeatherMinerAdapter = ZeusMinerAdapter
