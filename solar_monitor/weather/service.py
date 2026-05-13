"""Background poll loop for current weather conditions.

Mirror of `forecast/service.py`. One instance per daemon, owns the
poll-loop task, caches the latest fetch in the SQLite `kv` table at
key `weather:current` so the dashboard isn't blank after a daemon
restart.
"""
from __future__ import annotations

import asyncio
import logging

import msgspec

from ..config import WeatherCfg
from ..storage.sqlite import Store
from .base import CurrentWeather
from . import openmeteo as _openmeteo_mod

log = logging.getLogger(__name__)

CACHE_KEY = "weather:current"

PROVIDERS = {
    "openmeteo": _openmeteo_mod.build,
}


class WeatherService:
    def __init__(self, cfg: WeatherCfg, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        factory = PROVIDERS.get(cfg.provider)
        if factory is None:
            raise ValueError(
                f"weather: unknown provider {cfg.provider!r}; "
                f"available: {sorted(PROVIDERS)}"
            )
        self.provider = factory(cfg)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="weather-poll")
        log.info("weather service started (%s, every %dm)",
                 self.cfg.provider, self.cfg.poll_minutes)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def fetch_once(self) -> CurrentWeather:
        cw = await self.provider.fetch()
        body = msgspec.json.encode(cw).decode("utf-8")
        await self.store.kv_set(CACHE_KEY, body)
        return cw

    async def _loop(self) -> None:
        try:
            await self.fetch_once()
        except Exception as e:
            log.warning("initial weather fetch failed: %s", e)
        period_s = max(1, self.cfg.poll_minutes) * 60
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=period_s)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.fetch_once()
            except Exception as e:
                log.warning("weather fetch failed: %s", e)
