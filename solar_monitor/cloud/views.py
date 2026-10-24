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

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .. import viewmodels
from ..api.energy import compute_energy

log = logging.getLogger(__name__)

SNAPSHOT_EVERY_S = 0            # every sync
ANALYTICS_EVERY_S = 15 * 60     # battery health, heatmap, runtime, per-device stats
ENERGY_TODAY_EVERY_S = 5 * 60   # the "Energy today" chart moves faster than the rest

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

    def __init__(self, store, scheduler, *, analytics_every_s: int = ANALYTICS_EVERY_S) -> None:
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

        With `live=True` (a viewer holds a lease) only the snapshot is built:
        it is what the dashboard animates from, and the analytics keep their
        own slower cadence."""
        now = now if now is not None else time.time()
        if live:
            self._pending = {}
            try:
                body = await self.scheduler.build_snapshot()
            except Exception:  # noqa: BLE001
                log.exception("live snapshot failed to build")
                return {}
            self._pending["snapshot"] = _Sent("", now)
            return {"snapshot": body}
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

        out: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, _Sent] = {}
        for name, (every, fn) in builders.items():
            prev = self._sent.get(name)
            if prev is not None and every and now - prev.at < every:
                continue
            try:
                body = await fn()
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

    def confirm(self, names) -> None:
        for n in names:
            if n in self._pending:
                self._sent[n] = self._pending[n]


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every call but carry no information, so the
    change-detection hash does not fire on a ticking clock."""
    return {k: v for k, v in body.items() if k not in ("ts", "now_ts")}
