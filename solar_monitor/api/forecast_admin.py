"""HTTP endpoints for the PV forecast integration.

Splits into three concerns:
  GET  /api/forecast/pv          → cached forecast for the History overlay
  GET  /api/forecast/config      → masked credentials for the Settings UI
  PUT  /api/forecast/config      → write new credentials, restart_required
  POST /api/forecast/test        → one-shot fetch with the *posted* creds
                                    (so the Test button works before save)
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from ..config import Config, ForecastCfg
from ..forecast.service import CACHE_KEY
from ..forecast.solcast import SolcastProvider
from ..forecast.openmeteo import OpenMeteoForecastProvider
from ..scheduler import PollScheduler
from ..storage.sqlite import Store
from .setup import _hot_reload_bg

log = logging.getLogger(__name__)


# ---------- payloads ----------

class ForecastConfigPayload(msgspec.Struct, kw_only=True):
    """Each provider uses a subset of these, Solcast needs api_key +
    resource_id, Open-Meteo needs lat/lon + array geometry, synthetic
    ignores everything. Routing happens in the PUT/POST handlers."""
    provider: str = "solcast"
    # Solcast credentials. Null = clear / disable.
    api_key: str | None = None
    resource_id: str | None = None
    # Open-Meteo PV estimator inputs.
    lat: float | None = None
    lon: float | None = None
    array_kw: float = 1.0
    tilt_deg: float = 30.0
    azimuth_deg: float = 0.0
    system_efficiency: float = 0.80
    poll_hours: int = 3


# ---------- helpers ----------

def _save_config(config_path: str, mutator) -> None:
    """Same atomic .bak + .tmp rename pattern alerts_admin uses."""
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw = mutator(raw)
    if raw is None:
        raise RuntimeError("config mutator returned None")
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)


# ---------- routes ----------

@get("/api/forecast/accuracy")
async def get_forecast_accuracy(
    state: State,
    day_offset: int = 1,
) -> dict[str, Any]:
    """Compare a past day's PV forecast against the actual energy the
    charge_controller(s) recorded.

    `day_offset=1` (default) = yesterday; 2 = day-before-yesterday, etc.
    Capped at 30 to match the forecast_history retention window.

    The endpoint never errors when there's no data, it returns a
    `{ok: false}` shape that the UI treats as "hide this widget."
    """
    from ..storage.sqlite import Store
    config: Config = state["config"]
    store: Store = state["store"]

    if day_offset < 1 or day_offset > 30:
        raise HTTPException(
            status_code=400,
            detail="day_offset must be between 1 and 30",
        )

    # Local-midnight of the target day. Done server-side so timezone
    # interpretation matches the SQL we wrote against the same
    # timestamps in the maintenance + archive paths.
    import datetime as _dt
    now = _dt.datetime.now()
    today_mid = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target_mid = today_mid - _dt.timedelta(days=day_offset)
    target_mid_ts = int(target_mid.timestamp())

    controller_labels = [
        d.label for d in config.devices if d.kind == "charge_controller"
    ]
    if not controller_labels:
        return {"ok": False, "reason": "no charge controllers configured"}

    result = await store.forecast_accuracy_for_day(
        target_mid_ts, controller_labels,
    )
    if result is None:
        return {"ok": False, "reason": "not enough data yet"}
    return {"ok": True, **result}


@get("/api/forecast/pv")
async def get_pv_forecast(state: State) -> dict[str, Any]:
    """Return the latest cached forecast or a 204-shaped empty payload.
    The UI treats `points: []` as "not configured / not fetched yet"
    so the History overlay code can render cleanly without a forecast."""
    store: Store = state["store"]
    cached = await store.kv_get(CACHE_KEY)
    if cached is None:
        return {"points": [], "fetched_at": None, "provider": None}
    body, updated_at = cached
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # Cache corrupt, return empty rather than 500. Next poll
        # overwrites it.
        return {"points": [], "fetched_at": None, "provider": None}
    payload.setdefault("fetched_at", updated_at)
    return payload


@get("/api/forecast/config")
async def get_forecast_config(state: State) -> dict[str, Any]:
    """Masked view of the config for the Settings UI. Never returns
    the raw api_key, the field comes back as `****` when set, "" when
    unset, and the UI handles "leave blank to keep existing" the same
    way the alert transport editor does. Open-Meteo fields ride along
    so the form can show the array geometry without a second fetch."""
    config: Config = state["config"]
    fc = config.forecast
    if fc is None:
        return {
            "configured":        False,
            "provider":          "solcast",
            "api_key":           "",
            "resource_id":       "",
            "lat":               None,
            "lon":               None,
            "array_kw":          1.0,
            "tilt_deg":          30.0,
            "azimuth_deg":       0.0,
            "system_efficiency": 0.80,
            "poll_hours":        3,
        }
    return {
        "configured":        True,
        "provider":          fc.provider,
        "api_key":           "****" if fc.api_key else "",
        "resource_id":       fc.resource_id,
        "lat":               fc.lat,
        "lon":               fc.lon,
        "array_kw":          fc.array_kw,
        "tilt_deg":          fc.tilt_deg,
        "azimuth_deg":       fc.azimuth_deg,
        "system_efficiency": fc.system_efficiency,
        "poll_hours":        fc.poll_hours,
    }


@put("/api/forecast/config")
async def update_forecast_config(
    data: ForecastConfigPayload, state: State,
) -> dict[str, Any]:
    """Write or clear the `forecast:` block. Each provider has its
    own "required fields are present" check; if anything's missing
    the block gets cleared rather than half-configured.

    Engine hot-reloads via the same path as the wizard."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if data.poll_hours < 1 or data.poll_hours > 24:
        raise HTTPException(
            status_code=400,
            detail="poll_hours must be in [1, 24]; Solcast hobbyist "
                   "limits make 3-6 the practical range.",
        )

    # Branch per provider, each has different required fields, so
    # "clearing" semantics differ too. Solcast clears when api_key or
    # resource_id is missing; Open-Meteo clears when lat/lon is missing.
    existing = config.forecast
    new_fc: ForecastCfg | None = None

    if data.provider == "solcast":
        api_key = data.api_key
        # Preserve existing api_key when the UI sends "****" sentinel
        # (Settings form leaves the password input blank to keep the
        # current value).
        if api_key == "****" and existing is not None:
            api_key = existing.api_key
        if api_key and data.resource_id:
            new_fc = ForecastCfg(
                provider="solcast",
                api_key=api_key,
                resource_id=data.resource_id,
                poll_hours=data.poll_hours,
            )
    elif data.provider == "openmeteo":
        # Lat/lon falls back to WeatherCfg if the user hasn't given the
        # forecast block its own, typical case: van builder with a
        # single static lat/lon for both current weather + PV forecast.
        lat = data.lat
        lon = data.lon
        if lat is None and config.weather is not None:
            lat = config.weather.lat
        if lon is None and config.weather is not None:
            lon = config.weather.lon
        if lat is not None and lon is not None and data.array_kw > 0:
            new_fc = ForecastCfg(
                provider="openmeteo",
                lat=lat,
                lon=lon,
                array_kw=data.array_kw,
                tilt_deg=data.tilt_deg,
                azimuth_deg=data.azimuth_deg,
                system_efficiency=data.system_efficiency,
                poll_hours=data.poll_hours,
            )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unknown forecast provider {data.provider!r}; "
                   f"expected 'solcast' or 'openmeteo'",
        )

    if new_fc is None:
        # Required fields missing → clear the integration.
        config.forecast = None

        def _mutate(raw):
            raw.pop("forecast", None)
            return raw
        _save_config(config_path, _mutate)
        log.info("forecast integration cleared")
        asyncio.create_task(_hot_reload_bg(state))
        return {"ok": True, "configured": False, "restart_required": False}

    config.forecast = new_fc

    def _mutate(raw):
        # Write only the fields relevant to the chosen provider, keeps
        # the yaml clean for the inspecting user. Common fields always
        # written; provider-specific fields conditioned on provider.
        block: dict[str, Any] = {
            "provider":   new_fc.provider,
            "poll_hours": new_fc.poll_hours,
        }
        if new_fc.provider == "solcast":
            block["api_key"]     = new_fc.api_key
            block["resource_id"] = new_fc.resource_id
        else:  # openmeteo
            block["lat"]               = new_fc.lat
            block["lon"]               = new_fc.lon
            block["array_kw"]          = new_fc.array_kw
            block["tilt_deg"]          = new_fc.tilt_deg
            block["azimuth_deg"]       = new_fc.azimuth_deg
            block["system_efficiency"] = new_fc.system_efficiency
        raw["forecast"] = block
        return raw
    _save_config(config_path, _mutate)
    log.info("forecast integration configured (%s, every %dh)",
             new_fc.provider, new_fc.poll_hours)
    asyncio.create_task(_hot_reload_bg(state))
    return {"ok": True, "configured": True, "restart_required": False}


@post("/api/forecast/test")
async def test_forecast_fetch(
    data: ForecastConfigPayload, state: State,
) -> dict[str, Any]:
    """One-shot fetch with the supplied creds/config. Used by the
    Settings UI's "Test" button so the user can validate before
    saving to config.yaml. Doesn't touch the live service or cache."""
    config: Config = state["config"]

    if data.provider == "solcast":
        api_key = data.api_key
        # Same "****" → keep-existing semantic as the PUT.
        if api_key == "****" and config.forecast is not None:
            api_key = config.forecast.api_key
        if not api_key or not data.resource_id:
            raise HTTPException(
                status_code=400,
                detail="Both api_key and resource_id are required.",
            )
        provider = SolcastProvider(api_key=api_key, resource_id=data.resource_id)
    elif data.provider == "openmeteo":
        lat = data.lat if data.lat is not None else (config.weather.lat if config.weather else None)
        lon = data.lon if data.lon is not None else (config.weather.lon if config.weather else None)
        if lat is None or lon is None:
            raise HTTPException(
                status_code=400,
                detail="Lat/lon required (either on the forecast form or "
                       "via the weather integration).",
            )
        if data.array_kw <= 0:
            raise HTTPException(
                status_code=400, detail="Array capacity must be > 0 kW.",
            )
        provider = OpenMeteoForecastProvider(
            lat=lat, lon=lon,
            array_kw=data.array_kw,
            tilt_deg=data.tilt_deg,
            azimuth_deg=data.azimuth_deg,
            system_efficiency=data.system_efficiency,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"unknown provider {data.provider!r}",
        )

    try:
        fc = await provider.fetch()
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"{data.provider} unreachable: {e}",
        )

    # Headline: time + power of the next forecast peak, so the UI can
    # show "✓ next peak 4.2 kW at 14:00 tomorrow".
    peak_ts: int | None = None
    peak_w: float = 0.0
    import time as _time
    now = int(_time.time())
    for p in fc.points:
        if p.ts < now:
            continue
        if p.pv_w > peak_w:
            peak_w = p.pv_w
            peak_ts = p.ts

    return {
        "ok":       True,
        "provider": data.provider,
        "points":   len(fc.points),
        "peak_ts":  peak_ts,
        "peak_w":   round(peak_w, 1) if peak_ts is not None else None,
    }