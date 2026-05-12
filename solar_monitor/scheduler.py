"""Background poll scheduler.

Owns the asyncio loop for periodic polling. The Litestar app starts one of
these on startup and cancels it on shutdown. Crash-resistant: a failed poll
backs off but doesn't kill the loop.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from .config import Config
from .export import EXPORTERS, Exporter
from .orchestrator import Poller
from .storage import Store

log = logging.getLogger(__name__)


class PollScheduler:
    def __init__(
        self,
        config: Config,
        store: Store,
        interval_seconds: int = 60,
        max_backoff_seconds: int = 300,
        maintenance_interval_seconds: int = 600,
    ) -> None:
        self.config = config
        self.store = store
        self.interval_seconds = interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.maintenance_interval_seconds = maintenance_interval_seconds

        self._task: asyncio.Task | None = None
        self._maint_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._consecutive_failures = 0
        self._last_result: dict[str, Any] | None = None
        self._poller: Poller | None = None
        self._exporters: list[Exporter] = []

    @property
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._poller = Poller(self.config)
        await self._poller.open()

        # Bring up any configured exporters.
        for ecfg in self.config.exporters:
            etype = ecfg.get("type")
            factory = EXPORTERS.get(etype)
            if factory is None:
                log.error("unknown exporter type %r (registered: %s)",
                          etype, list(EXPORTERS))
                continue
            try:
                exp = factory(ecfg)
                await exp.start()
                self._exporters.append(exp)
            except Exception:
                log.exception("exporter %s failed to start", ecfg.get("id"))

        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="poll-scheduler")
        self._maint_task = asyncio.create_task(self._maintenance(), name="maintenance")
        log.info(
            "scheduler started (interval=%ss, maintenance=%ss, exporters=%d)",
            self.interval_seconds,
            self.maintenance_interval_seconds,
            len(self._exporters),
        )

    async def stop(self) -> None:
        if self._task is None and self._maint_task is None:
            return
        self._stop.set()

        for t in (self._task, self._maint_task):
            if t is None:
                continue
            try:
                await asyncio.wait_for(t, timeout=10)
            except asyncio.TimeoutError:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        self._task = None
        self._maint_task = None
        if self._poller is not None:
            await self._poller.close()
            self._poller = None

        for exp in self._exporters:
            try:
                await exp.stop()
            except Exception:
                log.exception("exporter %s stop failed", exp.id)
        self._exporters.clear()

        log.info("scheduler stopped")

    def _next_sleep(self) -> float:
        if self._consecutive_failures == 0:
            return float(self.interval_seconds)
        # Exponential backoff with jitter, capped.
        backoff = min(
            self.max_backoff_seconds,
            self.interval_seconds * (2 ** (self._consecutive_failures - 1)),
        )
        return backoff + random.uniform(0, backoff * 0.1)

    async def _run(self) -> None:
        log.info("first poll begins")
        while not self._stop.is_set():
            try:
                assert self._poller is not None
                result = await self._poller.poll()
                self._last_result = result
                await self.store.record_poll(result)
                # Fan out to exporters. Each exporter is non-blocking; if it
                # has its own queue it'll buffer. A misbehaving exporter does
                # not stall the scheduler.
                for exp in self._exporters:
                    try:
                        await exp.export(result)
                    except Exception:
                        log.exception("exporter %s.export() failed", exp.id)

                if result.get("errors"):
                    log.warning(
                        "poll errors (%d): %s",
                        len(result["errors"]),
                        result["errors"][:3],  # cap log spam
                    )
                # A poll with no device data at all = failure
                if not result.get("devices"):
                    self._consecutive_failures += 1
                    log.warning(
                        "no devices polled successfully; failure #%d",
                        self._consecutive_failures,
                    )
                else:
                    if self._consecutive_failures:
                        log.info(
                            "recovered after %d failures",
                            self._consecutive_failures,
                        )
                    self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                log.exception(
                    "scheduler iteration crashed (#%d): %s",
                    self._consecutive_failures,
                    e,
                )

            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._next_sleep()
                )
            except asyncio.TimeoutError:
                pass

    async def _maintenance(self) -> None:
        # Run once shortly after startup, then on the configured interval.
        # The initial delay avoids stepping on the very first poll's writes.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=30)
            return  # asked to stop before first maintenance pass
        except asyncio.TimeoutError:
            pass

        while not self._stop.is_set():
            try:
                await self.store.rollup_and_purge()
            except Exception:
                log.exception("maintenance pass failed; will retry next interval")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.maintenance_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
