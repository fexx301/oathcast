"""External weather-provider adapters."""

from .open_meteo import OpenMeteoAdapter
from .openweather import OpenWeatherAdapter
from .weatherapi import WeatherApiAdapter

__all__ = ["OpenMeteoAdapter", "OpenWeatherAdapter", "WeatherApiAdapter"]
