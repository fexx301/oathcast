"""OpenWeather One Call 3.0 adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from oathcast.adapters.base import (
    AdapterError,
    HourPoint,
    canonical_forecast,
    probability_from_unit_interval,
    select_exact_point,
)
from oathcast.forecast import ForecastQuestion, UTC


class OpenWeatherAdapter:
    provider = "openweather_onecall"
    adapter_version = "openweather_onecall_v1"
    endpoint = "https://api.openweathermap.org/data/3.0/onecall"

    def build_url(self, question: ForecastQuestion, api_key: str | None = None) -> str:
        if not api_key:
            raise ValueError("OpenWeather requires an API key at the adapter service boundary")
        params = {
            "lat": f"{question.latitude:.6f}",
            "lon": f"{question.longitude:.6f}",
            "exclude": "current,minutely,daily,alerts",
            "units": "metric",
            "appid": api_key,
        }
        return f"{self.endpoint}?{urlencode(params)}"

    def parse(
        self,
        payload: dict[str, Any],
        question: ForecastQuestion,
        issued_at: datetime,
        retrieved_at: datetime | None = None,
    ):
        hourly = payload.get("hourly")
        if not isinstance(hourly, list):
            raise AdapterError("OpenWeather response has no hourly array")

        points: list[HourPoint] = []
        for hour in hourly:
            if not isinstance(hour, dict) or hour.get("dt") is None:
                continue
            try:
                timestamp = datetime.fromtimestamp(float(hour["dt"]), tz=UTC)
            except (TypeError, ValueError, OverflowError) as exc:
                raise AdapterError("OpenWeather returned an invalid Unix timestamp") from exc
            points.append(
                HourPoint(
                    timestamp=timestamp,
                    probability=probability_from_unit_interval(hour.get("pop"), self.provider),
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
                "OpenWeather One Call hourly probability of precipitation; exact measurable-"
                "precipitation threshold semantics are not asserted by this adapter."
            ),
            event_equivalence="unverified",
            provider_model=None,
        )
