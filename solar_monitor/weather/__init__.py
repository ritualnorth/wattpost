"""Current weather conditions integration.

Pluggable in the same shape as `forecast/`, one provider class, a
`WeatherService` background poller, normalised `CurrentWeather`
output. Open-Meteo is the first (and only) provider for now;
no API key required.
"""
from .base import CurrentWeather, WeatherProvider  # noqa: F401
from .service import WeatherService  # noqa: F401
from . import openmeteo  # noqa: F401
