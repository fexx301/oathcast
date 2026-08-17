"""Open-Meteo adapter for Telegraph's next-N-hour temperature contract."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from oathcast.adapters.base import AdapterError, parse_provider_time
from oathcast.adapters.open_meteo_window import _finite_number
from oathcast.forecast import (
    CanonicalTemperatureWindowForecast,
    HourlyTemperatureForecast,
    TemperatureWindowRequest,
)


class OpenMeteoTemperatureWindowAdapter:
    """Map exact Open-Meteo UTC temperature points into Telegraph's 2t series."""

    provider = "open_meteo"
    adapter_version = "open_meteo_temperature_window_v1"
    endpoint = "https://api.open-meteo.com/v1/forecast"

    def build_url(
        self,
        request: TemperatureWindowRequest,
        api_key: str | None = None,
    ) -> str:
        del api_key
        params = {
            "latitude": f"{request.latitude:.6f}",
            "longitude": f"{request.longitude:.6f}",
            "hourly": "temperature_2m",
            "temperature_unit": "celsius",
            "timezone": "UTC",
            # Keep one full day of margin beyond the maximum requested horizon.
            # Open-Meteo's day boundary is provider-owned; an extra day avoids
            # turning a late-UTC 24-hour request into a coverage race.
            "forecast_days": "3",
        }
        return f"{self.endpoint}?{urlencode(params)}"

    def parse(
        self,
        payload: dict[str, Any],
        request: TemperatureWindowRequest,
        issued_at: datetime,
        retrieved_at: datetime | None = None,
    ) -> CanonicalTemperatureWindowForecast:
        response_timezone = payload.get("timezone")
        if not isinstance(response_timezone, str) or response_timezone.upper() not in {
            "UTC",
            "GMT",
        }:
            raise AdapterError("Open-Meteo temperature response must declare UTC or GMT")

        if "utc_offset_seconds" not in payload:
            raise AdapterError("Open-Meteo temperature response must declare utc_offset_seconds")
        if _finite_number(payload["utc_offset_seconds"], "utc_offset_seconds") != 0:
            raise AdapterError("Open-Meteo temperature response must use a zero UTC offset")

        hourly_units = payload.get("hourly_units")
        if not isinstance(hourly_units, dict):
            raise AdapterError("Open-Meteo temperature hourly_units must be an object")
        if hourly_units.get("time") != "iso8601":
            raise AdapterError("Open-Meteo temperature time unit must be iso8601")
        if str(hourly_units.get("temperature_2m", "")).lower() not in {
            "c",
            "celsius",
            "\N{DEGREE SIGN}c",
        }:
            raise AdapterError("Open-Meteo temperature response must use Celsius temperatures")

        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise AdapterError("Open-Meteo response has no hourly object")
        times = hourly.get("time")
        temperatures = hourly.get("temperature_2m")
        if not isinstance(times, list) or not isinstance(temperatures, list):
            raise AdapterError(
                "Open-Meteo response is missing hourly time or temperature arrays"
            )
        if len(times) != len(temperatures):
            raise AdapterError("Open-Meteo hourly arrays have different lengths")

        rows: dict[datetime, Any] = {}
        for time_value, temperature_value in zip(times, temperatures):
            timestamp = parse_provider_time(time_value)
            if timestamp in rows:
                raise AdapterError("Open-Meteo returned duplicate hourly timestamps")
            rows[timestamp] = temperature_value

        hours: list[HourlyTemperatureForecast] = []
        for index in range(request.forecast_hours):
            interval_start = request.horizon_start + timedelta(hours=index)
            if interval_start not in rows:
                raise AdapterError(
                    "Open-Meteo did not return complete temperature coverage for the requested window"
                )
            hours.append(
                HourlyTemperatureForecast(
                    interval_start=interval_start,
                    temperature_2m_c=_finite_number(
                        rows[interval_start],
                        "temperature_2m",
                    ),
                )
            )

        return CanonicalTemperatureWindowForecast(
            event_id=request.event_id,
            provider=self.provider,
            reference_time=request.reference_time,
            issued_at=issued_at,
            hours=tuple(hours),
            temperature_native_definition=(
                "Hourly 2 metre air temperature sampled at each requested UTC timestamp."
            ),
            adapter_version=self.adapter_version,
            provider_model=payload.get("model"),
            retrieved_at=retrieved_at,
        )
