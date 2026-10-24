"""View-models the appliance publishes to the cloud.

The cloud dashboard renders the same UI as the local one, so it needs the
same view-models. Rather than re-implement the analytics in the cloud and
let the two drift apart, the appliance runs its own code (`viewmodels`,
`Scheduler.build_snapshot`) and ships the finished JSON. The cloud stores
it and serves it verbatim.

Each view has a minimum interval: the snapshot goes with every sync, the
heavier analytics (histograms, lifetime stats) only every few minutes and
only when their content changed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .. import viewmodels
from ..api.energy import compute_energy
from ..api.forecast_admin import build_forecast_accuracy, build_pv_forecast
from ..api.weather_admin import build_current_weather
from ..api.weather_history import build_weather_history

log = logging.getLogger(__name__)

SNAPSHOT_EVERY_S = 0            # every sync
ANALYTICS_EVERY_S = 15 * 60     # battery health, heatmap, runtime, per-device stats
BUILDER_TIMEOUT_S = 20          # one slow view must not stall the whole sync
ENERGY_TODAY_EVERY_S = 5 * 60   # the "Energy today" chart moves faster than the rest
MAX_VIEWS_PER_BATCH = 32        # the cloud's ingest limit; the rest wait for the next sync
FORECAST_EVERY_S = 30 * 60      # PV forecast + yesterday's accuracy move slowly
WEATHER_CURRENT_EVERY_S = 10 * 60
WEATHER_HISTORY_EVERY_S = 30 * 60   # each build is an Open-Meteo call, one per range

# The History page's range buttons: bucket size (seconds) -> span (None = midnight to now).
# Mirrors ENERGY_RANGES in web/app.js; the cloud serves /api/energy/today?bucket=N from
# the view published under f"energy/b{N}".
ENERGY_RANGES: dict[int, int | None] = {300: None, 900: 86400, 3600: 7 * 86400, 21600: 30 * 86400}


@dataclass
class _Sent:
    digest: str
    at: float


class ViewPublisher:
    """Builds the set of views due for upload and remembers what the cloud
    already has, so unchanged analytics are not re-sent."""

    def __init__(self, store, scheduler, *, analytics_every_s: int = ANALYTICS_EVERY_S,
                 builder_timeout_s: float = BUILDER_TIMEOUT_S) -> None:
        self.builder_timeout_s = builder_timeout_s
        self._pending: dict[str, _Sent] = {}
        self.store = store
        self.scheduler = scheduler
        self.analytics_every_s = analytics_every_s
        self._sent: dict[str, _Sent] = {}

    async def _device_views(self) -> dict[str, Callable[[], Awaitable[dict[str, Any]]]]:
        out: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {}
        latest = await self.store.get_latest()
        for label, m in latest.items():
            if label == "bank":
                continue
            kind = str(m.get("_kind", ""))
            if kind == "smart_battery" or kind == "shunt" or "cycle_count" in m:
                out[f"device/{label}/lifetime"] = (
                    lambda l=label: self.store.battery_lifetime_stats(l))
                out[f"device/{label}/efficiency"] = (
                    lambda l=label: viewmodels.efficiency(self.store, l))
            if "output_1_power_w" in m or "pv_power_w" in m:
                out[f"device/{label}/charger-stats"] = (
                    lambda l=label: viewmodels.charger_stats(self.store, l))
        return out

    async def due(self, now: float | None = None, *, live: bool = False) -> dict[str, dict[str, Any]]:
        """Views to upload now, as {name: body}. Call `confirm()` with the
        returned names once the cloud has acked the batch.

        `live` is accepted for compatibility but changes nothing: while a
        viewer holds a lease the snapshot still goes with every sync (its
        cadence is "every sync") and the analytics views are still built
        at their normal cadence. Skipping them in live mode meant a page
        watched for hours never refreshed its histograms or energy chart."""
        now = now if now is not None else time.time()
        builders: dict[str, tuple[int, Callable[[], Awaitable[dict[str, Any]]]]] = {
            "snapshot": (SNAPSHOT_EVERY_S, self.scheduler.build_snapshot),
        }
        analytics: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
            "today/soc-envelope": lambda: viewmodels.soc_envelope(self.store),
            "runtime-forecast":   lambda: viewmodels.runtime_forecast(self.store),
            "battery-health":     lambda: viewmodels.battery_health(self.store),
            "load-heatmap":       lambda: viewmodels.load_heatmap(self.store),
        }
        analytics.update(await self._device_views())
        for name, fn in analytics.items():
            builders[name] = (self.analytics_every_s, fn)
        for bucket, span in ENERGY_RANGES.items():
            builders[f"energy/b{bucket}"] = (
                min(ENERGY_TODAY_EVERY_S, self.analytics_every_s) if span is None
                else self.analytics_every_s,
                lambda b=bucket, sp=span: compute_energy(
                    self.store, since=(int(time.time()) - sp) if sp else None, bucket=b),
            )

        # Forecast and weather panels. Each reuses the builder behind the
        # appliance's own /api endpoint; when the integration is not set up
        # the builder returns its "nothing here" shape, which the cloud
        # serves as-is (so removing an integration also clears the panel).
        config = getattr(self.scheduler, "config", None)
        builders["forecast/pv"] = (FORECAST_EVERY_S, lambda: build_pv_forecast(self.store))
        if config is not None:
            builders["forecast/accuracy"] = (
                FORECAST_EVERY_S, lambda: build_forecast_accuracy(config, self.store, 1))
        builders["weather/current"] = (
            WEATHER_CURRENT_EVERY_S, lambda: build_current_weather(self.store))
        if config is not None:
            wcfg = config.weather
            for bucket, span in ENERGY_RANGES.items():
                builders[f"weather/history/b{bucket}"] = (
                    WEATHER_HISTORY_EVERY_S,
                    lambda b=bucket, sp=span, w=wcfg: _weather_history(config, self.store, b, sp, w))

        out: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, _Sent] = {}
        for name, (every, fn) in builders.items():
            if len(out) >= MAX_VIEWS_PER_BATCH:
                break
            prev = self._sent.get(name)
            if prev is not None and every and now - prev.at < every:
                continue
            try:
                body = await asyncio.wait_for(fn(), timeout=self.builder_timeout_s)
            except asyncio.TimeoutError:
                log.warning("view %s took longer than %.0fs to build; skipping",
                            name, self.builder_timeout_s)
                continue
            except Exception:  # noqa: BLE001 — one broken view must not block the rest
                log.exception("view %s failed to build; skipping", name)
                continue
            if not isinstance(body, dict):
                continue
            digest = hashlib.blake2b(
                json.dumps(_stable(body), sort_keys=True, separators=(",", ":"),
                           default=str).encode(), digest_size=12).hexdigest()
            if prev is not None and prev.digest == digest and name != "snapshot":
                # Unchanged: count it as sent so we don't rebuild it every run.
                self._sent[name] = _Sent(digest, now)
                continue
            out[name] = body
            self._pending[name] = _Sent(digest, now)
        return out

    def reset(self) -> None:
        """Forget what the cloud has (it is a different cloud/account now)."""
        self._sent.clear()
        self._pending = {}

    def confirm(self, names) -> None:
        for n in names:
            if n in self._pending:
                self._sent[n] = self._pending[n]


async def _weather_history(config, store, bucket: int, span: int | None, wcfg) -> dict[str, Any]:
    """The Energy chart's cloud-cover overlay for one range button. Without a
    weather location there is no series to send, so skip the (large) empty
    grid and say so."""
    if wcfg is None or wcfg.lat is None or wcfg.lon is None:
        return {"configured": False, "available": False, "series": {"ts": [], "cloud_cover_pct": []}}
    since = (int(time.time()) - span) if span else None
    return await build_weather_history(config, store, since=since, bucket=bucket)


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every call but carry no information, so the
    change-detection hash does not fire on a ticking clock."""
    return {k: v for k, v in body.items() if k not in ("ts", "now_ts")}
