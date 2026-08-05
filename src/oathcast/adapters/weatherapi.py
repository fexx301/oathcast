"""WeatherAPI.com adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from oathcast.adapters.base import (
    AdapterError,
    HourPoint,
    canonical_forecast,
    probability_from_percent,
    select_exact_point,
)
from oathcast.forecast import ForecastQuestion, UTC


class WeatherApiAdapter:
    provider = "weatherapi"
    adapter_version = "weatherapi_v1"
    endpoint = "https://api.weatherapi.com/v1/forecast.json"

    def build_url(self, question: ForecastQuestion, api_key: str | None = None) -> str:
        if not api_key:
            raise ValueError("WeatherAPI requires an API key at the adapter service boundary")
        params = {
            "key": api_key,
            "q": f"{question.latitude:.6f},{question.longitude:.6f}",
            "days": "2",
            "aqi": "no",
            "alerts": "no",
        }
        return f"{self.endpoint}?{urlencode(params)}"

    @staticmethod
    def _hour_timestamp(hour: dict[str, Any], timezone_name: str) -> datetime:
        if hour.get("time_epoch") is not None:
            try:
                return datetime.fromtimestamp(float(hour["time_epoch"]), tz=UTC)
            except (TypeError, ValueError, OverflowError) as exc:
                raise AdapterError("WeatherAPI returned an invalid time_epoch") from exc

        raw_time = hour.get("time")
        if not isinstance(raw_time, str):
            raise AdapterError("WeatherAPI hour has no time or time_epoch")
        try:
            local_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise AdapterError(f"WeatherAPI returned an unsupported time: {raw_time}") from exc
        try:
            return local_time.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(UTC)
        except ZoneInfoNotFoundError as exc:
            raise AdapterError(f"Unknown WeatherAPI timezone: {timezone_name}") from exc

    def parse(
        self,
        payload: dict[str, Any],
        question: ForecastQuestion,
        issued_at: datetime,
        retrieved_at: datetime | None = None,
    ):
        location = payload.get("location")
        forecast = payload.get("forecast", {})
        days = forecast.get("forecastday") if isinstance(forecast, dict) else None
        if not isinstance(location, dict) or not isinstance(days, list):
            raise AdapterError("WeatherAPI response is missing location or forecastday")

        timezone_name = str(location.get("tz_id", ""))
        points: list[HourPoint] = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("hour"), list):
                continue
            for hour in day["hour"]:
                if not isinstance(hour, dict):
                    continue
                timestamp = self._hour_timestamp(hour, timezone_name)
                points.append(
                    HourPoint(
                        timestamp=timestamp,
                        probability=probability_from_percent(
                            hour.get("chance_of_rain"), self.provider
                        ),
                    )
                )

        point = select_exact_point(points, question.horizon_start, self.provider)
        return canonical_forecast(
            provider=self.provider,
            adapter_version=self.adapter_version,
            question=question,
            probability=point.probability,
            issued_at=issued_at,
            retrieved_at=retrieved_at,
            native_event_definition=(
                "WeatherAPI chance_of_rain percentage; exact measurable-precipitation "
                "threshold semantics are not asserted by this adapter."
            ),
            event_equivalence="unverified",
            provider_model=None,
        )
