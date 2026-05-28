"""Open-Meteo PV forecast provider, physical estimate from irradiance.

Solcast is the gold standard for fixed-roof installs (it trains an ML
model on years of site-specific data) BUT it's site-based, not lat/lon
based: free tier = 10 calls/day, max 2 sites, no API for site CRUD.
That's a non-starter for moving vans + a real barrier-to-entry for
casual users who'd rather not register a separate account.

Open-Meteo's /v1/forecast endpoint exposes solar irradiance hourly,
free, unlimited, lat/lon-based, no auth. We can derive a "good enough"
PV forecast from that + the user's array geometry. Quality is a step
down from Solcast (no site-specific historical calibration) but easily
in the right ballpark for "should I drive south tomorrow / will I have
power tomorrow" decisions.

## How the estimate works

Given hourly `shortwave_radiation` (W/m², Global Horizontal Irradiance)
from Open-Meteo, plus the array config (capacity_kW, tilt, azimuth):

  PV_W ≈ GHI × tilt_factor × capacity_kW × 1000 × system_efficiency
                                          ─────
                                          1000 (STC reference)

`tilt_factor` is a cheap geometric correction for the panel not being
horizontal, we compute the cosine of the angle between the panel
normal and the sun. Below a small floor we clamp to the floor rather
than going negative (sub-horizon = no direct beam, but real panels
get some diffuse).

This is intentionally simpler than the full Hay-Davies / Perez sky-
diffuse transposition models that NREL's PVlib implements. Our use
case is dashboard guidance, not power-flow simulation; the 5-10%
extra accuracy isn't worth bringing in numpy + pvlib as deps.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

import httpx

from .base import ForecastProvider, PvForecast, PvForecastPoint

log = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 30.0

# How many days of forecast to request. Solcast caps at 7; matching
# their window means the dashboard's 5-day outlook renders the same
# regardless of provider.
FORECAST_DAYS = 7

# Minimum tilt-factor floor: even when the geometric calculation goes
# negative (sun behind the panel), real panels pick up some diffuse
# light. 5% of GHI is a reasonable lower bound.
TILT_FLOOR = 0.05


class OpenMeteoForecastProvider(ForecastProvider):
    name = "openmeteo"

    def __init__(
        self,
        lat: float,
        lon: float,
        array_kw: float,
        tilt_deg: float = 30.0,
        azimuth_deg: float = 0.0,
        system_efficiency: float = 0.80,
    ) -> None:
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError(
                f"openmeteo forecast: lat/lon out of range "
                f"({lat}, {lon})"
            )
        if array_kw <= 0:
            raise ValueError(
                f"openmeteo forecast: array_kw must be > 0 (got {array_kw})"
            )
        if not (0 <= tilt_deg <= 90):
            raise ValueError(
                f"openmeteo forecast: tilt_deg must be in [0, 90] (got {tilt_deg})"
            )
        if not (0 <= system_efficiency <= 1):
            raise ValueError(
                f"openmeteo forecast: system_efficiency must be in [0, 1] "
                f"(got {system_efficiency})"
            )
        self.lat = float(lat)
        self.lon = float(lon)
        self.array_w = float(array_kw) * 1000.0
        self.tilt = math.radians(float(tilt_deg))
        # Azimuth conversion: our config follows the convention 0=south,
        # 90=west, 180=north, 270=east (Northern-hemisphere PV standard).
        # Solar-position formulae use 0=south too, so no further
        # conversion needed.
        self.azimuth = math.radians(float(azimuth_deg))
        self.system_efficiency = float(system_efficiency)

    async def fetch(self) -> PvForecast:
        params = {
            "latitude":         self.lat,
            "longitude":        self.lon,
            "hourly":           "shortwave_radiation,cloud_cover",
            "timezone":         "UTC",   # easier than localising on our side
            "forecast_days":    FORECAST_DAYS,
            # We don't need current weather here, the weather service
            # owns that. Requesting only `hourly` keeps the payload tight.
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(BASE_URL, params=params)
        if r.status_code == 400:
            try:
                reason = r.json().get("reason") or r.text
            except Exception:
                reason = r.text
            raise RuntimeError(f"Open-Meteo rejected the request: {reason}")
        r.raise_for_status()

        body = r.json()
        hourly = body.get("hourly") or {}
        times = hourly.get("time") or []
        ghi   = hourly.get("shortwave_radiation") or []
        if not times or not ghi:
            raise RuntimeError(
                "Open-Meteo returned no shortwave_radiation array, "
                "the location might be malformed or the API changed shape"
            )

        points: list[PvForecastPoint] = []
        for i, iso in enumerate(times):
            ts = _parse_iso_utc(iso)
            if ts is None or i >= len(ghi):
                continue
            ghi_w = float(ghi[i] or 0)
            if ghi_w < 0:
                ghi_w = 0
            pv_w = self._irradiance_to_pv(ts, ghi_w)
            # Match Solcast's `period_end` convention: timestamp at the
            # END of the bucket. Open-Meteo's hourly array is keyed at
            # the START of each hour, so we add 3600 s.
            points.append(PvForecastPoint(
                ts=ts + 3600,
                pv_w=round(pv_w, 1),
            ))
        return PvForecast(
            provider=self.name,
            fetched_at=int(time.time()),
            points=points,
        )

    def _irradiance_to_pv(self, ts: int, ghi_w: float) -> float:
        """Map GHI (W/m²) at unix-second `ts` to estimated PV output (W).

        Cheap solar-geometry model: compute the sun's elevation +
        azimuth from time + location, derive the cosine of the angle
        between the panel normal and the sun vector, multiply by GHI.
        Doesn't separate direct / diffuse components, that's the
        biggest simplification vs proper POA transposition, and the
        biggest accuracy hit on overcast days (when diffuse dominates
        and the tilt factor matters less than this model implies).
        """
        if ghi_w <= 0:
            return 0.0
        # Solar position from NOAA approximation. Good enough for hourly
        # forecasts, we're not navigating spacecraft.
        elev, sun_az = _solar_position(ts, self.lat, self.lon)
        if elev <= 0:
            # Sun below horizon, no direct beam. Some diffuse might
            # still be in the GHI value (post-sunset twilight) but the
            # contribution is tiny; floor to zero rather than feed
            # negative cos values into the panel-angle calc.
            return 0.0
        # Angle of incidence between sun direction and panel normal.
        # Standard formula for a fixed tilted panel:
        #   cos(θ_i) = sin(elev)·cos(β) + cos(elev)·sin(β)·cos(γ_s − γ)
        # where β = panel tilt, γ = panel azimuth, γ_s = sun azimuth.
        cos_i = (
            math.sin(elev) * math.cos(self.tilt) +
            math.cos(elev) * math.sin(self.tilt) * math.cos(sun_az - self.azimuth)
        )
        tilt_factor = max(cos_i, TILT_FLOOR)
        # Scale GHI to STC equivalent, then to nameplate watts, then
        # apply system efficiency (inverter + wiring + soiling + temp).
        pv_w = (ghi_w / 1000.0) * tilt_factor * self.array_w * self.system_efficiency
        return max(pv_w, 0.0)


def _parse_iso_utc(s: str | None) -> int | None:
    """Open-Meteo's hourly.time entries are local ISO strings like
    '2026-05-16T14:00'. We requested timezone=UTC, so treat the bare
    timestamp as UTC. Returns unix seconds."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _solar_position(ts: int, lat: float, lon: float) -> tuple[float, float]:
    """Return (sun_elevation_rad, sun_azimuth_rad) at a given unix-
    second timestamp + location.

    Simplified NOAA solar-position algorithm, accurate to within
    ~1° of arc over a +/-100-year window, which is way more than
    enough for an hourly PV forecast estimate. Azimuth convention:
    0 = south, +west, mirror image to our panel azimuth convention.
    """
    # Day-of-year + fractional hour in UTC.
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    day_of_year = dt.timetuple().tm_yday
    hour_frac = dt.hour + dt.minute / 60 + dt.second / 3600

    lat_r = math.radians(lat)

    # Solar declination (Spencer approximation, in radians).
    gamma = 2 * math.pi * (day_of_year - 1 + (hour_frac - 12) / 24) / 365.0
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148  * math.sin(3 * gamma)
    )
    # Equation of time (minutes).
    eqt = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    # True solar time (minutes), accounting for longitude offset.
    tst = hour_frac * 60 + eqt + 4 * lon
    # Solar hour angle (radians, 0 at solar noon, +west).
    hour_angle = math.radians(tst / 4 - 180)

    sin_elev = (
        math.sin(lat_r) * math.sin(decl)
        + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle)
    )
    sin_elev = max(-1.0, min(1.0, sin_elev))
    elev = math.asin(sin_elev)

    # Azimuth: 0 = south, positive going west.
    cos_az = (
        (math.sin(decl) - math.sin(elev) * math.sin(lat_r))
        / (math.cos(elev) * math.cos(lat_r) + 1e-12)
    )
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.acos(cos_az)
    if hour_angle > 0:
        az = -az  # afternoon → west-of-south
    return elev, az


def build(cfg) -> OpenMeteoForecastProvider:
    """Factory used by the forecast service. Accepts a ForecastCfg-
    shaped object; lat/lon fall back to the WeatherCfg location if
    the forecast block hasn't been given its own."""
    return OpenMeteoForecastProvider(
        lat=cfg.lat,
        lon=cfg.lon,
        array_kw=cfg.array_kw,
        tilt_deg=cfg.tilt_deg,
        azimuth_deg=cfg.azimuth_deg,
        system_efficiency=cfg.system_efficiency,
    )
