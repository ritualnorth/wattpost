"""Location resolution for the appliance + cloud transmission gate.

Two distinct concerns lumped into one module:

1. ``current_location(scheduler, config)``, what IS the appliance's
   location right now? Used by the appliance's own dashboard map
   tile, weather queries, forecast estimators. Returns the best
   available source: live GPS fix if present, else the static
   ForecastCfg.lat/lon, else None.

2. ``location_for_cloud(scheduler, config)``, what should we ship
   in the heartbeat extras? Honours the LocationCfg.share_with_cloud
   privacy gate: returns None when share mode is "off", coordinates
   rounded to the approx grid when "approx", or full precision when
   "precise". The gate is customer-controlled and authoritative,
   see [[location-opt-in]] memory.

Splitting these two prevents the easy-to-miss bug where the cloud
ends up with location data the user thought they'd disabled. The
heartbeat path MUST call location_for_cloud, never current_location.
"""
from __future__ import annotations

import math
from typing import Any


def _gps_fix_latlon(scheduler) -> tuple[float, float, int | None] | None:
    """Most-recent GPS fix as (lat, lon, age_seconds) when available."""
    gps = getattr(scheduler, "gps", None)
    if gps is None:
        return None
    try:
        status = gps.get_status()
    except Exception:
        return None
    fix = (status or {}).get("latest_fix")
    if not fix:
        return None
    lat = fix.get("lat")
    lon = fix.get("lon")
    if lat is None or lon is None:
        return None
    age = (status or {}).get("latest_fix_age_s")
    return float(lat), float(lon), (int(age) if age is not None else None)


def _static_latlon(config) -> tuple[float, float] | None:
    """Fallback static coordinates from the ForecastCfg block."""
    fc = getattr(config, "forecast", None)
    if fc is None:
        return None
    lat = getattr(fc, "lat", None)
    lon = getattr(fc, "lon", None)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def current_location(scheduler, config) -> dict[str, Any] | None:
    """Best available location for LOCAL use (dashboard map tile,
    weather queries). Ignores the cloud-share gate, local UI
    always knows where it is even when the cloud doesn't.

    Returns ``{lat, lon, source, fix_age_s}`` or None when no
    coordinates are configured at all.
    """
    fix = _gps_fix_latlon(scheduler)
    if fix is not None:
        lat, lon, age = fix
        return {"lat": lat, "lon": lon, "source": "gps", "fix_age_s": age}
    static = _static_latlon(config)
    if static is not None:
        lat, lon = static
        return {"lat": lat, "lon": lon, "source": "forecast", "fix_age_s": None}
    return None


def _snap_to_grid(lat: float, lon: float, grid_km: float) -> tuple[float, float]:
    """Round (lat, lon) to a ~grid_km cell. Uses a simple equirectangular
    approximation: 1° lat ≈ 111 km always, 1° lon ≈ 111 km × cos(lat).
    Good enough for "the Lake District" granularity; we're not building
    a navigation system here."""
    if grid_km <= 0:
        return lat, lon
    lat_step = grid_km / 111.0
    lon_step = grid_km / max(1.0, 111.0 * math.cos(math.radians(lat)))
    snapped_lat = round(lat / lat_step) * lat_step
    snapped_lon = round(lon / lon_step) * lon_step
    # 4dp ≈ 11m, well below any sensible grid_km, but caps the wire
    # representation so we don't ship 17 digits of float.
    return round(snapped_lat, 4), round(snapped_lon, 4)


def location_for_cloud(scheduler, config) -> dict[str, Any] | None:
    """The location payload to include in heartbeat extras, AFTER
    applying the customer's share-with-cloud preference. Returns None
    when the customer has opted out, heartbeat should then omit the
    location key entirely.

    Payload shape:
      {
        "lat": float,
        "lon": float,
        "source": "gps" | "forecast",
        "precision": "approx" | "precise",
        "fix_age_s": int | null,        # only for GPS source
      }
    """
    loc_cfg = getattr(config, "location", None)
    mode = (getattr(loc_cfg, "share_with_cloud", "off") or "off").lower()
    if mode not in ("approx", "precise"):
        return None  # off (or any unrecognised value, fail closed)
    loc = current_location(scheduler, config)
    if loc is None:
        return None
    lat, lon = float(loc["lat"]), float(loc["lon"])
    if mode == "approx":
        grid_km = float(getattr(loc_cfg, "approx_grid_km", 10.0) or 10.0)
        lat, lon = _snap_to_grid(lat, lon, grid_km)
    payload: dict[str, Any] = {
        "lat":       lat,
        "lon":       lon,
        "source":    loc["source"],
        "precision": "precise" if mode == "precise" else "approx",
    }
    if loc.get("fix_age_s") is not None:
        payload["fix_age_s"] = loc["fix_age_s"]
    return payload
