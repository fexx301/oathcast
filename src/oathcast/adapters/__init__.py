"""External weather-provider adapters."""

from .open_meteo import OpenMeteoAdapter
from .open_meteo_temperature import OpenMeteoTemperatureWindowAdapter
from .open_meteo_window import OpenMeteoWindowAdapter
from .openweather import OpenWeatherAdapter
from .weatherapi import WeatherApiAdapter

__all__ = [
    "OpenMeteoAdapter",
    "OpenMeteoTemperatureWindowAdapter",
    "OpenMeteoWindowAdapter",
    "OpenWeatherAdapter",
    "WeatherApiAdapter",
]
