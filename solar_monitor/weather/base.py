"""Current-weather provider interface + normalised output shape."""
from __future__ import annotations

import abc
import msgspec


class HourlyForecast(msgspec.Struct):
    """One hour-ahead slice. Used to draw the "next few hours" strip
    in the Right-now tile (a-la Apple Weather's hourly preview)."""
    ts: int                           # unix seconds at the start of the hour
    temperature_c: float | None = None
    weather_code: int | None = None
    is_day: bool | None = None


class CurrentWeather(msgspec.Struct):
    """Snapshot of current conditions. Optional fields are None when
    the provider doesn't report them — keeps the schema additive as
    more providers get wired up."""
    provider: str
    fetched_at: int        # unix seconds when the daemon completed the fetch

    # Observation timestamp from the provider (typically the rounded
    # current hour). Sometimes differs from fetched_at by minutes.
    observed_at: int | None = None

    # Core conditions
    temperature_c: float | None = None
    feels_like_c: float | None = None
    humidity_pct: float | None = None
    cloud_cover_pct: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    precipitation_mm: float | None = None
    pressure_hpa: float | None = None

    # WMO weather code (https://open-meteo.com/en/docs#weathervariables)
    # 0 = clear, 1-3 = mainly clear -> overcast, 45/48 = fog, 51+ = drizzle,
    # 61+ = rain, 71+ = snow, 80+ = showers, 95+ = thunder.
    weather_code: int | None = None
    is_day: bool | None = None

    # Sun events for today, as unix seconds in the user's local tz.
    sunrise_ts: int | None = None
    sunset_ts: int | None = None

    # Next ~8 hours, starting from the upcoming hour. None when the
    # provider doesn't surface hourly data or we're between fetches
    # and the cache still holds an older schema.
    hourly: list[HourlyForecast] | None = None


class WeatherProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def fetch(self) -> CurrentWeather:
        ...
