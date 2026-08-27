"""Open-Meteo adapter for complete 1-to-168-hour UTC forecast windows."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any
from urllib.parse import urlencode

from oathcast.adapters.base import AdapterError, parse_provider_time
from oathcast.forecast import (
    CanonicalWindowForecast,
    ForecastWindowRequest,
    HourlyWindowForecast,
)


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise AdapterError(f"Open-Meteo returned a non-numeric {field_name}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AdapterError(f"Open-Meteo returned a non-numeric {field_name}") from exc
    if not math.isfinite(number):
        raise AdapterError(f"Open-Meteo returned a non-finite {field_name}")
    return number


class OpenMeteoWindowAdapter:
    """Map exact Open-Meteo hourly points into complete interval records."""

    provider = "open_meteo"
    adapter_version = "open_meteo_window_v1"
    endpoint = "https://api.open-meteo.com/v1/forecast"

    def build_url(
        self,
        request: ForecastWindowRequest,
        api_key: str | None = None,
    ) -> str:
        del api_key
        params = {
            "latitude": f"{request.latitude:.6f}",
            "longitude": f"{request.longitude:.6f}",
            "hourly": "temperature_2m,precipitation_probability",
            "temperature_unit": "celsius",
            "timezone": "UTC",
            # Ask for the exact UTC bounds plus the final endpoint used by the
            # preceding-hour precipitation semantics.  ``forecast_days=7``
            # starts at the provider's current day and fails for a request
            # whose horizon begins later in the available forecast.  Open-Meteo
            # returns both endpoints for start/end_hour, so an N-hour request
            # is validated against N+1 rows by parse().
            "start_hour": request.horizon_start.strftime("%Y-%m-%dT%H:%M"),
            "end_hour": request.horizon_end.strftime("%Y-%m-%dT%H:%M"),
        }
        return f"{self.endpoint}?{urlencode(params)}"

    def parse(
        self,
        payload: dict[str, Any],
        request: ForecastWindowRequest,
        issued_at: datetime,
        retrieved_at: datetime | None = None,
    ) -> CanonicalWindowForecast:
        response_timezone = payload.get("timezone")
        if response_timezone is not None and str(response_timezone).upper() not in {
            "UTC",
            "GMT",
        }:
            raise AdapterError("Open-Meteo window response must use UTC")
        response_offset = payload.get("utc_offset_seconds")
        if response_offset is not None and _finite_number(
            response_offset,
            "utc_offset_seconds",
        ) != 0:
            raise AdapterError("Open-Meteo window response must use a zero UTC offset")
        response_temperature_unit = payload.get("temperature_unit")
        if (
            response_temperature_unit is not None
            and str(response_temperature_unit).lower()
            not in {"celsius", "\N{DEGREE SIGN}c"}
        ):
            raise AdapterError("Open-Meteo window response must use Celsius temperatures")
        hourly_units = payload.get("hourly_units")
        if hourly_units is not None:
            if not isinstance(hourly_units, dict):
                raise AdapterError("Open-Meteo window hourly_units must be an object")
            temperature_unit = hourly_units.get("temperature_2m")
            if temperature_unit is not None and str(temperature_unit).lower() not in {
                "c",
                "celsius",
                "\N{DEGREE SIGN}c",
            }:
                raise AdapterError(
                    "Open-Meteo window response must use Celsius temperatures"
                )
            probability_unit = hourly_units.get("precipitation_probability")
            if probability_unit is not None and str(probability_unit) not in {"%", "percent"}:
                raise AdapterError(
                    "Open-Meteo window precipitation probability must use percent units"
                )
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise AdapterError("Open-Meteo response has no hourly object")
        times = hourly.get("time")
        temperatures = hourly.get("temperature_2m")
        probabilities = hourly.get("precipitation_probability")
        if not all(isinstance(values, list) for values in (times, temperatures, probabilities)):
            raise AdapterError(
                "Open-Meteo response is missing hourly time, temperature, or probability arrays"
            )
        if len(times) != len(temperatures) or len(times) != len(probabilities):
            raise AdapterError("Open-Meteo hourly arrays have different lengths")

        rows: dict[datetime, tuple[Any, Any]] = {}
        for time_value, temperature_value, probability_value in zip(
            times,
            temperatures,
            probabilities,
        ):
            timestamp = parse_provider_time(time_value)
            if timestamp in rows:
                raise AdapterError("Open-Meteo returned duplicate hourly timestamps")
            rows[timestamp] = (temperature_value, probability_value)

        hours: list[HourlyWindowForecast] = []
        for index in range(request.duration_hours):
            interval_start = request.horizon_start + timedelta(hours=index)
            interval_end = interval_start + timedelta(hours=1)
            if interval_start not in rows:
                raise AdapterError(
                    "Open-Meteo did not return complete temperature coverage for the requested window"
                )
            if interval_end not in rows:
                raise AdapterError(
                    "Open-Meteo did not return complete precipitation coverage for the requested window"
                )
            temperature = _finite_number(
                rows[interval_start][0],
                "temperature_2m",
            )
            probability_percent = _finite_number(
                rows[interval_end][1],
                "precipitation_probability",
            )
            if not 0 <= probability_percent <= 100:
                raise AdapterError(
                    "Open-Meteo returned a precipitation probability outside [0, 100]"
                )
            hours.append(
                HourlyWindowForecast(
                    interval_start=interval_start,
                    interval_end=interval_end,
                    temperature_2m_c=temperature,
                    precipitation_probability=probability_percent / 100,
                )
            )

        return CanonicalWindowForecast(
            event_id=request.event_id,
            provider=self.provider,
            horizon_start=request.horizon_start,
            horizon_end=request.horizon_end,
            issued_at=issued_at,
            hours=tuple(hours),
            temperature_native_definition=(
                "Hourly 2 metre air temperature sampled at the beginning of each UTC hour."
            ),
            precipitation_native_definition=(
                "Probability of precipitation greater than 0.1 mm in the preceding hour."
            ),
            event_equivalence="documented_hourly_window",
            adapter_version=self.adapter_version,
            provider_model=payload.get("model"),
            retrieved_at=retrieved_at,
        )
