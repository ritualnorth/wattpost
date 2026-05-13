"""Open-Meteo current conditions provider.

Free, no API key, generous rate limits (~10k calls/day on the public
endpoint per the docs). We request `current=` for instant conditions
+ `daily=sunrise,sunset` for the today widget.

Docs: https://open-meteo.com/en/docs
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from .base import CurrentWeather, WeatherProvider

log = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 15.0

_CURRENT_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "is_day", "precipitation", "weather_code", "cloud_cover",
    "pressure_msl", "wind_speed_10m", "wind_direction_10m",
])


class OpenMeteoProvider(WeatherProvider):
    name = "openmeteo"

    def __init__(self, lat: float, lon: float) -> None:
        self.lat = float(lat)
        self.lon = float(lon)

    async def fetch(self) -> CurrentWeather:
        params = {
            "latitude":  self.lat,
            "longitude": self.lon,
            "current":   _CURRENT_FIELDS,
            "daily":     "sunrise,sunset",
            "timezone":  "auto",
            "wind_speed_unit": "ms",
            # One day of sunrise/sunset is enough for the dashboard
            # "Sunrise / sunset" line; reduces payload size.
            "forecast_days": 1,
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(BASE_URL, params=params)
        if r.status_code == 400:
            # Bad lat/lon, etc. Open-Meteo returns a JSON {reason: ...}.
            try:
                reason = r.json().get("reason") or r.text
            except Exception:
                reason = r.text
            raise RuntimeError(f"Open-Meteo rejected the request: {reason}")
        r.raise_for_status()
        body = r.json()
        cur = body.get("current") or {}
        daily = body.get("daily") or {}
        # `daily.sunrise[0]` is the local-time ISO string (no tz suffix);
        # the provider already aligned it to timezone=auto for the lat/lon.
        # We treat the bare timestamp as the appliance's local time.
        sunrise = _parse_iso(daily.get("sunrise", [None])[0])
        sunset  = _parse_iso(daily.get("sunset",  [None])[0])
        observed = _parse_iso(cur.get("time"))
        return CurrentWeather(
            provider="openmeteo",
            fetched_at=int(time.time()),
            observed_at=observed,
            temperature_c=_num(cur.get("temperature_2m")),
            feels_like_c=_num(cur.get("apparent_temperature")),
            humidity_pct=_num(cur.get("relative_humidity_2m")),
            cloud_cover_pct=_num(cur.get("cloud_cover")),
            wind_speed_ms=_num(cur.get("wind_speed_10m")),
            wind_direction_deg=_num(cur.get("wind_direction_10m")),
            precipitation_mm=_num(cur.get("precipitation")),
            pressure_hpa=_num(cur.get("pressure_msl")),
            weather_code=_int(cur.get("weather_code")),
            is_day=bool(cur.get("is_day")) if cur.get("is_day") is not None else None,
            sunrise_ts=sunrise,
            sunset_ts=sunset,
        )


def _num(v):
    return None if v is None else float(v)


def _int(v):
    return None if v is None else int(v)


def _parse_iso(s):
    """Open-Meteo returns local-naïve ISO strings like
    '2026-05-13T17:30'. Treat as local time on the appliance and
    convert to unix seconds."""
    if not s:
        return None
    try:
        # Some endpoints append seconds; some don't. fromisoformat
        # handles both. Treat naive datetimes as UTC for stability
        # across daylight-savings transitions — open-meteo gives us
        # them in the requested timezone (`auto`) so the offset is
        # implicit, but converting to UTC requires that timezone
        # context. We approximate by assuming the daemon and the lat/lon
        # share a timezone, treating the timestamp as if it were UTC.
        # The dashboard re-renders with `Date(ts * 1000)` which then
        # applies the *browser's* timezone — so anyone viewing this
        # over a Tailnet from another timezone sees the local sunrise
        # of where the appliance sits.
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def build(cfg) -> OpenMeteoProvider:
    return OpenMeteoProvider(lat=cfg.lat, lon=cfg.lon)
