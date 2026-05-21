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

from .base import CurrentWeather, HourlyForecast, WeatherProvider

log = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 15.0

_CURRENT_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "is_day", "precipitation", "weather_code", "cloud_cover",
    "pressure_msl", "wind_speed_10m", "wind_direction_10m",
])
_HOURLY_FIELDS = ",".join([
    "temperature_2m", "weather_code", "is_day",
])
# How many forward hours to keep in the cached payload. 12 hours is
# Apple-Weather-ish density while staying small (≈ 1 kB extra JSON).
_HOURLY_KEEP = 12


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
            "hourly":    _HOURLY_FIELDS,
            "daily":     "sunrise,sunset",
            "timezone":  "auto",
            "wind_speed_unit": "ms",
            # 2 days so the rolling 12-hour preview has runway late
            # in the evening. With `forecast_days: 1`, after ~18:00
            # local the hourly strip degrades to "next 2-3 hours,
            # then nothing" because Open-Meteo only returns hours
            # within today. 2 days is still tiny (~50 hourly rows).
            "forecast_days": 2,
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
            hourly=_extract_hourly(body.get("hourly") or {}, observed),
        )


def _num(v):
    return None if v is None else float(v)


def _int(v):
    return None if v is None else int(v)


def _extract_hourly(hourly: dict, observed_ts: int | None) -> list[HourlyForecast]:
    """Slice the Open-Meteo `hourly` block down to the next ~12 hours
    starting at or after `observed_ts`. The API returns parallel
    arrays (`time`, `temperature_2m`, `weather_code`, `is_day`) all
    of the same length; we zip them and drop anything before the
    current observation hour so the dashboard never shows a slice
    that's already in the past. Returns [] if the payload is missing
    or shorter than expected."""
    times = hourly.get("time") or []
    if not times:
        return []
    temps    = hourly.get("temperature_2m") or []
    codes    = hourly.get("weather_code")  or []
    is_days  = hourly.get("is_day")        or []
    out: list[HourlyForecast] = []
    cutoff = observed_ts or 0
    for i, t in enumerate(times):
        ts = _parse_iso(t)
        if ts is None or ts < cutoff:
            continue
        out.append(HourlyForecast(
            ts=ts,
            temperature_c=_num(temps[i]) if i < len(temps) else None,
            weather_code=_int(codes[i])  if i < len(codes) else None,
            is_day=bool(is_days[i]) if i < len(is_days) and is_days[i] is not None else None,
        ))
        if len(out) >= _HOURLY_KEEP:
            break
    return out


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
        # over a VPN from another timezone sees the local sunrise
        # of where the appliance sits.
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def build(cfg) -> OpenMeteoProvider:
    return OpenMeteoProvider(lat=cfg.lat, lon=cfg.lon)
