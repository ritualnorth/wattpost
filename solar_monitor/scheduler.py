"""Background poll scheduler.

Owns the asyncio loop for periodic polling. The Litestar app starts one of
these on startup and cancels it on shutdown. Crash-resistant: a failed poll
backs off but doesn't kill the loop.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from .alerts import AlertEngine, AlertRule
from .forecast import ForecastService
from .weather import WeatherService
from .cloud import CloudService
from .gps import GpsService
from .tunnel import TunnelService
from .update import UpdateChecker
from .config import Config
from .export import EXPORTERS, Exporter
from .orchestrator import Poller
from .outputs.service import OutputsService
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
        # SSE subscribers. Each subscriber gets its own bounded queue; if a
        # slow client falls behind we drop the oldest event for it (never
        # block the scheduler on a misbehaving consumer).
        self._subscribers: set[asyncio.Queue[dict]] = set()
        # Local alert engine — runs after every successful poll.
        rules = [
            AlertRule(
                id=r.id, name=r.name, metric=r.metric, op=r.op,
                threshold=r.threshold, severity=r.severity,
                cooldown_seconds=r.cooldown_seconds, transports=r.transports,
            )
            for r in config.alerts
        ]
        qh = config.quiet_hours
        quiet_hours = (qh.start_hour, qh.end_hour) if qh is not None else None
        self._alerts = AlertEngine(
            rules, config.notification_transports, quiet_hours=quiet_hours,
        )
        # PV forecast service — only built when the user has configured
        # a `forecast:` block. Stays None otherwise so the rest of the
        # daemon doesn't pay for an unused feature.
        self._forecast: ForecastService | None = None
        if config.forecast is not None:
            try:
                self._forecast = ForecastService(config.forecast, store)
            except Exception:
                log.exception("forecast service failed to initialise")

        # Current-weather service (Open-Meteo). Independent of the PV
        # forecast — many users will want one without the other.
        self._weather: WeatherService | None = None
        if config.weather is not None:
            try:
                self._weather = WeatherService(config.weather, store)
            except Exception:
                log.exception("weather service failed to initialise")

        # Cloud heartbeat. Only spun up when an actual bearer token is
        # present — daemon stays fully offline-capable when not paired.
        self._cloud: CloudService | None = None
        if config.cloud is not None and config.cloud.bearer_token:
            try:
                self._cloud = CloudService(config.cloud, self)
            except Exception:
                log.exception("cloud heartbeat service failed to initialise")

        # Outbound Cloudflare Tunnel — exposes the local dashboard at
        # `<slug>.wattpost.io`. Only spun up when the cloud has issued
        # a tunnel token at pair time AND cloudflared is on PATH.
        # Off entirely otherwise; appliance keeps working locally.
        self._tunnel: TunnelService | None = None
        if config.cloud is not None and TunnelService.is_available(config.cloud):
            try:
                self._tunnel = TunnelService(config.cloud)
            except Exception:
                log.exception("tunnel service failed to initialise")

        # Self-update *check* — polls the cloud's release manifest
        # daily and exposes the result on /api/system/update so the
        # UI can show "v0.0.x available". No auto-apply yet.
        self._updater: UpdateChecker | None = None
        try:
            self._updater = UpdateChecker()
        except Exception:
            log.exception("update checker failed to initialise")

        # Controllable outputs (#104). Discovery happens after the
        # first poll lands (otherwise device_meta is empty and no
        # adapter has anything to match against). The service then
        # refreshes state from every subsequent poll snapshot.
        self.outputs = OutputsService(config=config, store=store, scheduler=self)
        # Tracks whether we've performed the post-first-poll discovery.
        # Re-discover whenever new devices appear (count change).
        self._outputs_last_device_count = -1

        # USB GPS (#125). Optional; off entirely when config.gps is
        # absent. On significant movement we mutate the weather +
        # forecast cfg in-memory and trigger one-shot re-fetches so
        # a moving van's dashboard tracks its location automatically.
        self._gps: GpsService | None = None
        if config.gps is not None:
            try:
                self._gps = GpsService(
                    port=config.gps.port,
                    baudrate=config.gps.baudrate,
                    min_move_km=config.gps.min_move_km,
                    refresh_after_s=config.gps.refresh_after_s,
                    on_significant_move=self._on_gps_move,
                )
            except Exception:
                log.exception("gps service failed to initialise")

    @property
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    def get_transport(self, transport_id: str):
        """Expose an open transport so the setup wizard can piggyback on
        the live BLE link to probe slave IDs without taking BlueZ down."""
        if self._poller is None:
            return None
        return self._poller._transports.get(transport_id)

    @property
    def gps(self) -> GpsService | None:
        """Expose the GPS service for the /api/gps status endpoint
        (returns None when not configured)."""
        return self._gps

    async def _on_gps_move(self, lat: float, lon: float) -> None:
        """Called by GpsService when a fresh fix moves the daemon's
        effective location enough to warrant a refresh. Mutates the
        weather + forecast cfg in memory and triggers one-shot
        re-fetches; weather/forecast then refresh their kv caches
        which the dashboard reads from on next poll.

        We DO NOT persist the new lat/lon to YAML — that would write
        hundreds of files a day in a moving van. The original config-
        file values stay as the cold-start fallback."""
        if self.config.weather is not None:
            self.config.weather.lat = lat
            self.config.weather.lon = lon
        if self.config.forecast is not None and self.config.forecast.provider == "openmeteo":
            # Solcast is site-based and can't follow a moving van
            # (see project_target_customer + #130). Only Open-Meteo
            # gets its forecast lat/lon updated.
            self.config.forecast.lat = lat
            self.config.forecast.lon = lon
        # Trigger one-shot re-fetches so the kv caches refresh
        # without waiting for the next poll-cadence tick.
        if self._weather is not None:
            try:
                await self._weather.fetch_once()
            except Exception:
                log.exception("gps move: weather refetch failed")
        if self._forecast is not None and self.config.forecast and self.config.forecast.provider == "openmeteo":
            try:
                # Rebuild the provider so it picks up the new lat/lon —
                # ForecastService caches the provider built at start.
                from .forecast.service import PROVIDERS as _FC
                self._forecast.provider = _FC[self.config.forecast.provider](
                    self.config.forecast,
                )
                await self._forecast.fetch_once()
            except Exception:
                log.exception("gps move: forecast refetch failed")

    # ---------- SSE broadcast ----------
    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, payload: dict) -> None:
        # Non-blocking publish. A full queue means the consumer is slow —
        # drop the oldest event for them rather than stall the scheduler.
        for q in self._subscribers:
            while True:
                try:
                    q.put_nowait(payload)
                    break
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    async def build_snapshot(self) -> dict[str, Any]:
        """Same shape the SPA already consumes from /api/devices +
        /api/poll_run + /api/today, bundled into one payload so the SSE
        stream replaces the three separate REST fetches on each tick."""
        now = int(time.time())
        local = time.localtime(now)
        midnight = int(time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        ))
        devs = await self.store.list_devices()
        latest = await self.store.get_latest()
        last_run = await self.store.last_poll_run()
        today = await self.store.today_aggregate(midnight, now)

        # Count configured + open transports the same way the REST
        # /api/poll_run does — otherwise every SSE tick handed the
        # dashboard a poll_run with no transports field, the pill
        # logic treated that as `configured: 0`, and a healthy
        # appliance painted "Setup needed" until the next manual
        # /api/poll_run fetch.
        configured = 0
        open_count = 0
        if self._poller is not None:
            configured = len(self._poller._transports)
            for t in self._poller._transports.values():
                client = getattr(t, "_client", None)
                if client and getattr(client, "is_connected", False):
                    open_count += 1
        return {
            "type": "snapshot",
            "ts": now,
            "devices": [{**d, "latest": latest.get(d["label"], {})} for d in devs],
            "poll_run": {
                "last_run": last_run,
                "scheduler_running": self._task is not None and not self._task.done(),
                "transports": {
                    "configured": configured,
                    "open":       open_count,
                },
            },
            "today": today,
        }

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Demo mode swaps the real BLE poller for a synthetic data
        # generator (solar_monitor/demo.py). Same poll() contract,
        # zero hardware required — used by demo.wattpost.io.
        import os
        if os.environ.get("WATTPOST_DEMO") == "1":
            from .demo import SyntheticPoller, seed_history
            log.info("WATTPOST_DEMO=1 — using synthetic poller (no real BLE)")
            # Backfill 30 days of synthetic history so charts have
            # something to draw immediately. Idempotent — skips if
            # the store already has recent data.
            try:
                await seed_history(self.store, days=30, step_minutes=60)
            except Exception:
                log.exception("demo history seed failed (non-fatal)")
            self._poller = SyntheticPoller(self.config)
        else:
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

        # Bring up alert transports (ntfy / Discord / webhook / …).
        await self._alerts.start()

        # Background forecast poller (only if configured).
        if self._forecast is not None:
            await self._forecast.start()
        if self._gps is not None:
            await self._gps.start()
        if self._weather is not None:
            await self._weather.start()
        if self._cloud is not None:
            await self._cloud.start()
        if self._tunnel is not None:
            await self._tunnel.start()
        if self._updater is not None:
            await self._updater.start()

        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="poll-scheduler")
        self._maint_task = asyncio.create_task(self._maintenance(), name="maintenance")
        log.info(
            "scheduler started (interval=%ss, maintenance=%ss, exporters=%d, "
            "alert_rules=%d, alert_transports=%d)",
            self.interval_seconds,
            self.maintenance_interval_seconds,
            len(self._exporters),
            len(self._alerts.rules),
            len(self._alerts.transport_ids),
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

        await self._alerts.stop()
        if self._forecast is not None:
            await self._forecast.stop()
        if self._gps is not None:
            await self._gps.stop()
        if self._weather is not None:
            await self._weather.stop()
        if self._cloud is not None:
            await self._cloud.stop()
        if self._tunnel is not None:
            await self._tunnel.stop()
        if self._updater is not None:
            await self._updater.stop()

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
                # Output adapters (#104) — discover-on-first-poll and
                # refresh state from every snapshot. Tolerant of crash:
                # any failure logs but doesn't stall polling.
                try:
                    devices_now = len(result.get("devices") or [])
                    if devices_now != self._outputs_last_device_count:
                        await self.outputs.discover_all()
                        self._outputs_last_device_count = devices_now
                    await self.outputs.apply_snapshot()
                    # Schedule engine (#117) — fires any rule whose
                    # trigger landed since the last tick. Tolerant of
                    # crash; no schedules configured = cheap no-op.
                    await self.outputs.fire_schedules_if_due()
                except Exception:
                    log.exception("outputs service hook failed")
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

                # Build the snapshot once per poll — used by both SSE and
                # the alert evaluator. Skip the work entirely when no one
                # cares (no subscribers, no rules) so an idle daemon stays
                # cheap.
                if self._subscribers or self._alerts.rules:
                    try:
                        snapshot = await self.build_snapshot()
                    except Exception:
                        log.exception("snapshot build failed")
                        snapshot = None
                    if snapshot is not None:
                        if self._subscribers:
                            self._broadcast(snapshot)
                        if self._alerts.rules:
                            try:
                                await self._alerts.evaluate(snapshot)
                            except Exception:
                                log.exception("alert evaluator crashed")
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
