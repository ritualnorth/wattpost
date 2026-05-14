"""Background poll loop for PV forecasts.

One ForecastService instance per daemon. Owns:
  - The provider (Solcast for now)
  - The poll-loop asyncio task
  - The cached JSON blob in the Store's kv table

The cache survives daemon restarts so the dashboard isn't blank for
the first 3 hours after a reboot — we serve the previous fetch and
the next poll refreshes it. Cache key: `forecast:pv`.
"""
from __future__ import annotations

import asyncio
import logging

import msgspec

from ..config import ForecastCfg
from ..storage.sqlite import Store
from .base import PvForecast
from . import solcast as _solcast_mod

log = logging.getLogger(__name__)

CACHE_KEY = "forecast:pv"

# Providers register themselves here. Adding tomorrow.io is just a new
# module imported by forecast/__init__.py that appends a factory.
from . import synthetic as _synth_mod

PROVIDERS = {
    "solcast":   _solcast_mod.build,
    # Demo-only fake forecast; never makes a network call. Selected by
    # the demo.wattpost.io container's config.yaml.
    "synthetic": _synth_mod.build,
}


class ForecastService:
    def __init__(self, cfg: ForecastCfg, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        factory = PROVIDERS.get(cfg.provider)
        if factory is None:
            raise ValueError(
                f"forecast: unknown provider {cfg.provider!r}; "
                f"available: {sorted(PROVIDERS)}"
            )
        self.provider = factory(cfg)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="forecast-poll")
        log.info("forecast service started (%s, every %dh)",
                 self.cfg.provider, self.cfg.poll_hours)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def fetch_once(self) -> PvForecast:
        """Single fetch + cache write + archive. Exposed for the
        /api/forecast/test endpoint; the background loop calls the
        same path so archive coverage matches what the kv cache held."""
        fc = await self.provider.fetch()
        body = msgspec.json.encode(fc).decode("utf-8")
        await self.store.kv_set(CACHE_KEY, body)
        # Archive every point under one fetched_at so the accuracy
        # widget can later look back at "the forecast as the user
        # would have seen it before day X started." Best-effort —
        # archiving failure must not block the dashboard refresh.
        try:
            archive_rows = [
                (p.ts, p.pv_w, p.pv_w_p10, p.pv_w_p90) for p in fc.points
            ]
            await self.store.archive_forecast(fc.fetched_at, archive_rows)
        except Exception:
            log.exception("forecast archive write failed")
        return fc

    async def _loop(self) -> None:
        # First fetch immediately so a fresh daemon doesn't sit with no
        # forecast for poll_hours. Subsequent ones honour the cadence.
        try:
            await self.fetch_once()
        except Exception as e:
            log.warning("initial forecast fetch failed: %s", e)
        period_s = max(1, self.cfg.poll_hours) * 3600
        while not self._stop.is_set():
            try:
                # Use wait_for so stop() interrupts the sleep cleanly.
                await asyncio.wait_for(self._stop.wait(), timeout=period_s)
                return  # stop() set the event
            except asyncio.TimeoutError:
                pass
            try:
                await self.fetch_once()
            except Exception as e:
                # Keep looping; the next attempt comes round on schedule.
                # The previous cached value is still there for the UI.
                log.warning("forecast fetch failed: %s", e)
