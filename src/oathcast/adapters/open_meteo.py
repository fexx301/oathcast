"""Open-Meteo adapter.

Open-Meteo's precipitation probability is documented as the probability of
more than 0.1 mm of precipitation in the preceding hour. For a one-hour
OathCast event, the point at horizon_end represents that preceding hour.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from oathcast.adapters.base import (
    AdapterError,
    HourPoint,
    canonical_forecast,
    probability_from_percent,
    parse_provider_time,
    select_exact_point,
)
from oathcast.forecast import ForecastQuestion


class OpenMeteoAdapter:
    provider = "open_meteo"
    adapter_version = "open_meteo_v1"
    endpoint = "https://api.open-meteo.com/v1/forecast"

    def build_url(self, question: ForecastQuestion, api_key: str | None = None) -> str:
        del api_key
        params = {
            "latitude": f"{question.latitude:.6f}",
            "longitude": f"{question.longitude:.6f}",
            "hourly": "precipitation_probability",
            "timezone": "UTC",
            "forecast_days": "7",
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
        if not isinstance(hourly, dict):
            raise AdapterError("Open-Meteo response has no hourly object")
        times = hourly.get("time")
        probabilities = hourly.get("precipitation_probability")
        if not isinstance(times, list) or not isinstance(probabilities, list):
            raise AdapterError("Open-Meteo response is missing hourly time/probability arrays")
        if len(times) != len(probabilities):
            raise AdapterError("Open-Meteo hourly arrays have different lengths")

        points = [
            HourPoint(parse_provider_time(time_value), probability_from_percent(probability, self.provider))
            for time_value, probability in zip(times, probabilities)
        ]
        point = select_exact_point(points, question.horizon_end, self.provider)
        return canonical_forecast(
            provider=self.provider,
            adapter_version=self.adapter_version,
            question=question,
            probability=point.probability,
            issued_at=issued_at,
            retrieved_at=retrieved_at,
            native_event_definition=(
                "Probability of precipitation greater than 0.1 mm in the preceding hour."
            ),
            event_equivalence="documented_match",
            provider_model=payload.get("model"),
        )
