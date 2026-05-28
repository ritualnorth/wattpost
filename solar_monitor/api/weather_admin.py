"""HTTP endpoints for the current-weather (Open-Meteo) integration.

Same surface shape as forecast_admin: GET /current for the cached
blob, GET/PUT /config for the masked credentials view, POST /test
for the one-shot fetch behind the Settings UI's Test button.

Open-Meteo doesn't require an API key, so the "credentials" are
just (lat, lon, poll_minutes). The masking dance is therefore a
no-op here, both fields are public coordinates.
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
from litestar.exceptions import HTTPException

from ..config import Config, WeatherCfg
from .setup import _hot_reload_bg
from ..weather.service import CACHE_KEY
from ..weather.openmeteo import OpenMeteoProvider

log = logging.getLogger(__name__)


class WeatherConfigPayload(msgspec.Struct, kw_only=True):
    provider: str = "openmeteo"
    lat: float | None = None
    lon: float | None = None
    poll_minutes: int = 15


def _save_config(config_path: str, mutator) -> None:
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


@get("/api/weather/current")
async def get_current_weather(state: State) -> dict[str, Any]:
    from ..storage.sqlite import Store
    store: Store = state["store"]
    cached = await store.kv_get(CACHE_KEY)
    if cached is None:
        return {"provider": None, "fetched_at": None}
    body, updated_at = cached
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"provider": None, "fetched_at": None}
    payload.setdefault("fetched_at", updated_at)
    return payload


@get("/api/weather/config")
async def get_weather_config(state: State) -> dict[str, Any]:
    config: Config = state["config"]
    w = config.weather
    if w is None:
        return {"configured": False, "provider": "openmeteo",
                "lat": None, "lon": None, "poll_minutes": 15}
    return {
        "configured":   True,
        "provider":     w.provider,
        "lat":          w.lat,
        "lon":          w.lon,
        "poll_minutes": w.poll_minutes,
    }


@put("/api/weather/config")
async def update_weather_config(
    data: WeatherConfigPayload, state: State,
) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    clearing = data.lat is None or data.lon is None
    if clearing:
        config.weather = None
        def _mutate(raw):
            raw.pop("weather", None)
            return raw
        _save_config(config_path, _mutate)
        log.info("weather integration cleared")
        # Background hot-reload, the running scheduler picks up the
        # cleared config within a few seconds, no user-visible restart.
        asyncio.create_task(_hot_reload_bg(state))
        return {"ok": True, "configured": False, "restart_required": False}

    if not (-90 <= data.lat <= 90 and -180 <= data.lon <= 180):
        raise HTTPException(status_code=400,
                            detail="lat must be in [-90, 90], lon in [-180, 180]")
    if data.poll_minutes < 5 or data.poll_minutes > 120:
        raise HTTPException(status_code=400,
                            detail="poll_minutes must be in [5, 120]")

    new_w = WeatherCfg(
        provider=data.provider, lat=data.lat, lon=data.lon,
        poll_minutes=data.poll_minutes,
    )
    config.weather = new_w
    def _mutate(raw):
        raw["weather"] = {
            "provider":     new_w.provider,
            "lat":          new_w.lat,
            "lon":          new_w.lon,
            "poll_minutes": new_w.poll_minutes,
        }
        return raw
    _save_config(config_path, _mutate)
    log.info("weather integration configured (%s @ %.4f, %.4f, every %dm)",
             new_w.provider, new_w.lat, new_w.lon, new_w.poll_minutes)
    asyncio.create_task(_hot_reload_bg(state))
    return {"ok": True, "configured": True, "restart_required": False}


@post("/api/weather/test")
async def test_weather_fetch(
    data: WeatherConfigPayload, state: State,
) -> dict[str, Any]:
    if data.provider != "openmeteo":
        raise HTTPException(status_code=400,
                            detail=f"unknown provider {data.provider!r}")
    if data.lat is None or data.lon is None:
        raise HTTPException(status_code=400,
                            detail="lat and lon are required")
    provider = OpenMeteoProvider(lat=data.lat, lon=data.lon)
    try:
        w = await provider.fetch()
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Open-Meteo unreachable: {e}")
    return {
        "ok":            True,
        "temperature_c": w.temperature_c,
        "cloud_cover":   w.cloud_cover_pct,
        "weather_code":  w.weather_code,
    }
