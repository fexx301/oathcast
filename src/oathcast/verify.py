"""Forecast verification for Telegraph's WEATHER_FORECAST_VERIFY intent.

A verify request supplies a previously issued forecast and a past window and
asks whether the forecast verified against what was observed. This module
turns such a request into one observed value from the Open-Meteo historical
archive and compares it with the claim under a stated tolerance.

Like the rest of OathCast it refuses to guess: an unresolvable place, a window
that has not finished, or an archive day with no value is an explicit outcome,
never a silent zero. Network access lives with the caller; the adapters here
only build URLs and parse payloads so they can be tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import re
from typing import Any
from urllib.parse import urlencode


ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_SOURCE = "Open-Meteo historical weather archive (ERA5 reanalysis)"
MAX_WINDOW_DAYS = 31


class VerifyError(ValueError):
    """Raised when a verify request cannot be answered without guessing."""


@dataclass(frozen=True)
class Variable:
    key: str
    archive_field: str
    label: str
    unit: str
    aggregate: str  # how several days combine: sum, max, min or mean

    def tolerance(self, forecast_value: float) -> float:
        """OathCast's default verification tolerance, in this variable's unit.

        Callers may pass their own tolerance; these defaults are stated in
        every answer so a reader can see the rule that was applied.
        """

        if self.key in {"precipitation", "snowfall"}:
            floor = 1.0
            return max(floor, 0.25 * abs(forecast_value))
        if self.key.startswith("temperature"):
            return 2.0
        return max(5.0, 0.2 * abs(forecast_value))


VARIABLES: dict[str, Variable] = {
    variable.key: variable
    for variable in (
        Variable("precipitation", "precipitation_sum", "precipitation", "mm", "sum"),
        Variable("snowfall", "snowfall_sum", "snowfall", "cm", "sum"),
        Variable("temperature_max", "temperature_2m_max", "maximum temperature", "°C", "max"),
        Variable("temperature_min", "temperature_2m_min", "minimum temperature", "°C", "min"),
        Variable("temperature_mean", "temperature_2m_mean", "mean temperature", "°C", "mean"),
        Variable("wind_max", "wind_speed_10m_max", "maximum wind speed", "km/h", "max"),
    )
}

# Keyword order matters: the first match wins, so specific phrases come first.
VARIABLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"\bsnow", "snowfall"),
    (r"\b(low|minimum|min temp|overnight|coldest)\b", "temperature_min"),
    (r"\b(mean|average) temp", "temperature_mean"),
    (r"\b(wind|gust|breez)", "wind_max"),
    (r"\b(rain|precip|shower|drizzle|wet|dry)", "precipitation"),
    (r"°|\b(temp|high|maximum|max|hot|warm|degrees)", "temperature_max"),
)

UNIT_PATTERN = re.compile(
    r"(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>°\s*[CF]|degrees?\s*(?:celsius|fahrenheit|[CF])\b|mm\b|millimet(?:re|er)s?|"
    r"cm\b|centimet(?:re|er)s?|inch(?:es)?\b|in\.|km/h|kph\b|mph\b|m/s|knots?\b|kt\b)",
    re.IGNORECASE,
)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?,?\s+(\d{4})\b")
MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
COORDINATES = re.compile(
    r"(?:lat(?:itude)?\s*[:=]?\s*)?(?P<lat>-?\d{1,2}(?:\.\d+)?)\s*,\s*"
    r"(?:lon(?:gitude)?\s*[:=]?\s*)?(?P<lon>-?\d{1,3}(?:\.\d+)?)"
)
PLACE_IN_QUESTION = re.compile(
    r"\b(?:in|at|for|over)\s+((?:[A-Z][\w'’.-]*)(?:(?:\s+|,\s*)(?:de|da|do|la|le|of|[A-Z][\w'’.-]*))*)"
)


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    admin1: str | None = None

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.country and self.country != self.name:
            parts.append(self.country)
        return ", ".join(parts)


@dataclass(frozen=True)
class VerifyRequest:
    location: str
    start: date
    end: date
    variable: Variable
    forecast_value: float | None
    forecast_unit: str | None
    tolerance: float | None
    question: str | None = None

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Observation:
    place: Place
    grid_latitude: float
    grid_longitude: float
    timezone: str
    variable: Variable
    start: date
    end: date
    value: float
    daily_values: tuple[float, ...]
    source: str = ARCHIVE_SOURCE


@dataclass(frozen=True)
class VerifyResult:
    request: VerifyRequest
    observation: Observation
    forecast_value: float | None  # converted to the variable's unit
    error: float | None
    tolerance: float | None
    verdict: str  # verified, not_verified or no_forecast_supplied

    def to_dict(self) -> dict[str, Any]:
        obs = self.observation
        return {
            "verdict": self.verdict,
            "verified": None if self.verdict == "no_forecast_supplied" else self.verdict == "verified",
            "location": obs.place.label,
            "latitude": obs.place.latitude,
            "longitude": obs.place.longitude,
            "grid_latitude": obs.grid_latitude,
            "grid_longitude": obs.grid_longitude,
            "timezone": obs.timezone,
            "start_date": obs.start.isoformat(),
            "end_date": obs.end.isoformat(),
            "variable": obs.variable.key,
            "unit": obs.variable.unit,
            "observed_value": obs.value,
            "daily_values": list(obs.daily_values),
            "forecast_value": self.forecast_value,
            "error": self.error,
            "tolerance": self.tolerance,
            "source": obs.source,
        }


# --- request parsing -----------------------------------------------------------


def _parse_date_text(text: str, today: date) -> list[date]:
    found: list[tuple[int, date]] = []
    for match in ISO_DATE.finditer(text):
        found.append((match.start(), date(int(match[1]), int(match[2]), int(match[3]))))
    for match in DAY_MONTH_YEAR.finditer(text):
        month = MONTHS.get(match[2].lower())
        if month:
            found.append((match.start(), date(int(match[3]), month, int(match[1]))))
    for match in MONTH_DAY_YEAR.finditer(text):
        month = MONTHS.get(match[1].lower())
        if month:
            found.append((match.start(), date(int(match[3]), month, int(match[2]))))
    lowered = text.lower()
    if re.search(r"\byesterday\b", lowered):
        found.append((lowered.index("yesterday"), today - timedelta(days=1)))
    ago = re.search(r"\b(\d{1,2})\s+days?\s+ago\b", lowered)
    if ago:
        found.append((ago.start(), today - timedelta(days=int(ago[1]))))
    found.sort(key=lambda item: item[0])
    unique: list[date] = []
    for _, value in found:
        if value not in unique:
            unique.append(value)
    return unique


def _parse_date_param(value: str, today: date) -> date:
    dates = _parse_date_text(value, today)
    if not dates:
        raise VerifyError(f"could not read a date from {value!r}; use YYYY-MM-DD")
    return dates[0]


def _normalise_unit(unit: str) -> str:
    compact = unit.lower().replace(" ", "").replace("degrees", "°").replace("degree", "°")
    if compact in {"°c", "°celsius", "c"}:
        return "°C"
    if compact in {"°f", "°fahrenheit", "f"}:
        return "°F"
    if compact.startswith("millimet") or compact == "mm":
        return "mm"
    if compact.startswith("centimet") or compact == "cm":
        return "cm"
    if compact.startswith("inch") or compact in {"in", "in."}:
        return "in"
    if compact in {"km/h", "kph"}:
        return "km/h"
    if compact.startswith("knot") or compact == "kt":
        return "kn"
    return compact


def _extract_claim(text: str) -> tuple[float, str] | None:
    match = UNIT_PATTERN.search(text)
    if match:
        return float(match["value"]), _normalise_unit(match["unit"])
    if re.search(r"\b(no rain|dry|no precipitation)\b", text, re.IGNORECASE):
        return 0.0, "mm"
    return None


def _infer_variable(text: str) -> Variable:
    lowered = text.lower()
    for pattern, key in VARIABLE_KEYWORDS:
        if re.search(pattern, lowered):
            return VARIABLES[key]
    raise VerifyError("could not tell which weather variable the forecast was about")


def convert(value: float, unit: str, variable: Variable) -> float:
    """Convert a claimed value into the variable's archive unit."""

    unit = _normalise_unit(unit)
    target = variable.unit
    if unit == target:
        return value
    conversions = {
        ("°F", "°C"): lambda v: (v - 32) * 5 / 9,
        ("in", "mm"): lambda v: v * 25.4,
        ("mm", "cm"): lambda v: v / 10,
        ("cm", "mm"): lambda v: v * 10,
        ("in", "cm"): lambda v: v * 2.54,
        ("mph", "km/h"): lambda v: v * 1.609344,
        ("m/s", "km/h"): lambda v: v * 3.6,
        ("kn", "km/h"): lambda v: v * 1.852,
    }
    try:
        return conversions[(unit, target)](value)
    except KeyError as exc:
        raise VerifyError(f"a forecast in {unit} cannot be compared with {variable.label}") from exc


def parse_verify_request(params: dict[str, str], *, today: date) -> VerifyRequest:
    """Build a verify request from query parameters and/or the question text.

    Explicit parameters win over anything read from the question, because the
    Telegraph request builder fills parameters from the endpoint description.
    """

    question = (params.get("question") or params.get("q") or "").strip() or None
    text = question or ""

    location = (params.get("location") or "").strip()
    if not location and params.get("lat") and params.get("lon"):
        location = f"{params['lat']},{params['lon']}"
    if not location and text:
        match = PLACE_IN_QUESTION.search(text)
        if match:
            location = match[1].strip(" ,.")
    if not location:
        raise VerifyError("a location (place name or 'lat,lon') is required")

    if params.get("start") or params.get("date"):
        start = _parse_date_param(params.get("start") or params["date"], today)
        end = _parse_date_param(params["end"], today) if params.get("end") else start
    else:
        dates = _parse_date_text(text, today)
        if not dates:
            raise VerifyError("a past date or window (YYYY-MM-DD) is required")
        start, end = dates[0], dates[-1]
    if end < start:
        start, end = end, start
    if end >= today:
        raise VerifyError("the window has not finished yet; verification needs a past date")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise VerifyError(f"the window may cover at most {MAX_WINDOW_DAYS} days")

    variable_param = (params.get("variable") or "").strip().lower()
    if variable_param:
        variable = VARIABLES.get(variable_param) or _infer_variable(variable_param)
    else:
        variable = _infer_variable(text)

    forecast_value: float | None = None
    forecast_unit: str | None = None
    if params.get("forecast_value") not in (None, ""):
        try:
            forecast_value = float(params["forecast_value"])
        except ValueError as exc:
            raise VerifyError("forecast_value must be a number") from exc
        forecast_unit = _normalise_unit(params.get("unit") or variable.unit)
    elif text:
        claim = _extract_claim(text)
        if claim is not None:
            forecast_value, forecast_unit = claim

    tolerance: float | None = None
    if params.get("tolerance") not in (None, ""):
        try:
            tolerance = abs(float(params["tolerance"]))
        except ValueError as exc:
            raise VerifyError("tolerance must be a number") from exc

    return VerifyRequest(
        location=location,
        start=start,
        end=end,
        variable=variable,
        forecast_value=forecast_value,
        forecast_unit=forecast_unit,
        tolerance=tolerance,
        question=question,
    )


# --- adapters ------------------------------------------------------------------


def coordinates_from_location(location: str) -> tuple[float, float] | None:
    match = COORDINATES.fullmatch(location.strip()) or COORDINATES.search(location)
    if not match:
        return None
    latitude, longitude = float(match["lat"]), float(match["lon"])
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise VerifyError("coordinates are out of range")
    return latitude, longitude


def geocoding_url(location: str) -> str:
    name = location.split(",")[0].strip()
    return f"{GEOCODING_ENDPOINT}?{urlencode({'name': name, 'count': 10, 'language': 'en', 'format': 'json'})}"


def parse_geocoding(payload: dict[str, Any], location: str) -> Place:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise VerifyError(f"no place called {location!r} was found")
    qualifier = ",".join(location.split(",")[1:]).strip().lower()
    chosen = results[0]
    if qualifier:
        for candidate in results:
            names = " ".join(
                str(candidate.get(key) or "") for key in ("country", "country_code", "admin1", "admin2")
            ).lower()
            if qualifier in names:
                chosen = candidate
                break
    try:
        return Place(
            name=str(chosen["name"]),
            latitude=float(chosen["latitude"]),
            longitude=float(chosen["longitude"]),
            country=chosen.get("country"),
            admin1=chosen.get("admin1"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise VerifyError("the geocoding result was malformed") from exc


def archive_url(place: Place, request: VerifyRequest) -> str:
    params = {
        "latitude": f"{place.latitude:.4f}",
        "longitude": f"{place.longitude:.4f}",
        "start_date": request.start.isoformat(),
        "end_date": request.end.isoformat(),
        "daily": request.variable.archive_field,
        "timezone": "auto",
    }
    return f"{ARCHIVE_ENDPOINT}?{urlencode(params)}"


def parse_archive(payload: dict[str, Any], place: Place, request: VerifyRequest) -> Observation:
    variable = request.variable
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise VerifyError("the archive response has no daily block")
    times = daily.get("time")
    values = daily.get(variable.archive_field)
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        raise VerifyError("the archive response is missing or misaligned daily arrays")
    expected = [(request.start + timedelta(days=i)).isoformat() for i in range(request.days)]
    if times != expected:
        raise VerifyError("the archive did not return exactly the requested days")
    if any(value is None for value in values):
        raise VerifyError("the archive has no observation yet for part of this window")
    numbers = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in numbers):
        raise VerifyError("the archive returned a non-finite value")
    unit = (payload.get("daily_units") or {}).get(variable.archive_field)
    if unit and _normalise_unit(unit) != variable.unit:
        raise VerifyError(f"the archive reported {unit}, expected {variable.unit}")

    if variable.aggregate == "sum":
        value = sum(numbers)
    elif variable.aggregate == "max":
        value = max(numbers)
    elif variable.aggregate == "min":
        value = min(numbers)
    else:
        value = sum(numbers) / len(numbers)
    return Observation(
        place=place,
        grid_latitude=float(payload.get("latitude", place.latitude)),
        grid_longitude=float(payload.get("longitude", place.longitude)),
        timezone=str(payload.get("timezone") or "UTC"),
        variable=variable,
        start=request.start,
        end=request.end,
        value=round(value, 2),
        daily_values=numbers,
    )


def verify(request: VerifyRequest, observation: Observation) -> VerifyResult:
    if request.forecast_value is None:
        return VerifyResult(request, observation, None, None, None, "no_forecast_supplied")
    forecast = round(convert(request.forecast_value, request.forecast_unit or request.variable.unit, request.variable), 2)
    tolerance = request.tolerance if request.tolerance is not None else request.variable.tolerance(forecast)
    error = round(observation.value - forecast, 2)
    verdict = "verified" if abs(error) <= tolerance + 1e-9 else "not_verified"
    return VerifyResult(request, observation, forecast, error, round(tolerance, 2), verdict)


# --- answer text ---------------------------------------------------------------


def _number(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _quantity(value: float, variable: Variable) -> str:
    unit = variable.unit
    return f"{_number(value)}{unit}" if unit.startswith("°") else f"{_number(value)} {unit}"


def _window(observation: Observation) -> str:
    def spoken(day: date) -> str:
        return f"{day.day} {day.strftime('%B %Y')}"

    if observation.start == observation.end:
        return f"on {spoken(observation.start)}"
    return f"from {spoken(observation.start)} to {spoken(observation.end)}"


def _observed_sentence(result: VerifyResult) -> str:
    obs = result.observation
    quantity = _quantity(obs.value, obs.variable)
    if obs.variable.aggregate == "sum":
        total = "total " if obs.start != obs.end else ""
        measured = f"{quantity} of {total}{obs.variable.label}"
    else:
        measured = f"a {obs.variable.label} of {quantity}"
    return f"{obs.place.label} recorded {measured} {_window(obs)}, according to the {obs.source}."


def _verdict_sentence(result: VerifyResult) -> str:
    obs = result.observation
    assert result.forecast_value is not None and result.error is not None and result.tolerance is not None
    direction = "too high" if result.error < 0 else "too low" if result.error > 0 else "exact"
    miss = "" if direction == "exact" else f", {_quantity(abs(result.error), obs.variable)} {direction}"
    outcome = "verified" if result.verdict == "verified" else "did not verify"
    within = "within" if result.verdict == "verified" else "outside"
    return (
        f"The forecast of {_quantity(result.forecast_value, obs.variable)} {outcome}{miss}, "
        f"{within} the ±{_quantity(result.tolerance, obs.variable)} tolerance."
    )


def render_answer(result: VerifyResult, style: str = "observed_then_verdict") -> str:
    """Render the scored response text.

    Styles are kept side by side so the wording can be measured rather than
    assumed: observed_only, verdict_first and observed_then_verdict.
    """

    observed = _observed_sentence(result)
    if result.verdict == "no_forecast_supplied" or style == "observed_only":
        return observed
    verdict = _verdict_sentence(result)
    if style == "verdict_first":
        headline = "Verified." if result.verdict == "verified" else "Not verified."
        return f"{headline} {verdict} {observed}"
    if style == "observed_then_verdict":
        return f"{observed} {verdict}"
    raise ValueError(f"unknown answer style: {style}")
