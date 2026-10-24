"""Push closed 1-minute rollups to the cloud.

The appliance is the source of truth; the cloud keeps a copy so its
dashboard can show history and live values without reaching into the
appliance. Delivery is at-least-once and resumable:

* A watermark (the newest `bucket_ts` the cloud has acknowledged) lives in
  the local `kv` table, so a restart, a dead uplink or a week offline all
  resume from the same place.
* Batches are gzipped, signed with the appliance keypair, and replayable:
  the cloud upserts on (device, metric, bucket), so a response lost in
  transit just means the same rows are sent again.
* Failures back off exponentially with full jitter (cap 15 min) and never
  touch the local dashboard.
* Live lease: while someone is watching the cloud dashboard, the cloud's
  reply carries `live: {ttl_s, interval_s}`. The appliance then pushes a
  fresh snapshot every `interval_s` seconds until the lease runs out (the
  viewer keeps renewing it), and goes back to its normal cadence. The lease
  is a duration, not a timestamp, so the appliance's clock doesn't matter.
  The cloud never connects to the appliance: the appliance asks by polling.

Bytes matter on cellular and Starlink links. A day of 100 metrics at
1-minute resolution is ~144k rows; gzipped JSON with short repeated keys
compresses very well, and the default 24 h first-sync backfill is sent in
5 000-row batches.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

WATERMARK_KEY = "cloud_rollup_watermark"
BATCH_ROWS = 5000
MAX_BATCHES_PER_RUN = 24          # ~120k rows; the rest waits for the next run
BACKOFF_CAP_S = 15 * 60
BACKOFF_BASE_S = 30
LIVE_MIN_INTERVAL_S = 2           # whatever the cloud asks for, never faster than this
LIVE_MAX_TTL_S = 300              # nor leased for longer than this per grant


@dataclass
class SyncResult:
    sent_rows: int = 0
    batches: int = 0
    caught_up: bool = False
    error: str | None = None


def backoff_delay(failures: int, rng: random.Random | None = None) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2^n))."""
    r = rng or random
    ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, failures - 1)))
    return r.uniform(0, ceiling)


class RollupSync:
    """One instance per CloudService. `run_once()` drains the backlog;
    `loop()` calls it on a schedule."""

    def __init__(
        self,
        *,
        store,
        endpoint: Callable[[], str],
        bearer: Callable[[], str],
        sign: Callable[[bytes], dict[str, str]],
        backfill_hours: Callable[[], int],
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        views=None,
        enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        self._enabled = enabled           # Settings can switch uploads off live
        self.views = views            # cloud.views.ViewPublisher | None
        self.store = store
        self._endpoint = endpoint
        self._bearer = bearer
        self._sign = sign
        self._backfill_hours = backfill_hours
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=30.0, follow_redirects=True))
        self.failures = 0
        self._live_until_mono = 0.0     # monotonic deadline of the current lease
        self._live_interval = 0.0
        self.last_ok_at: float | None = None
        self.last_error: str | None = None

    def live_interval(self) -> float | None:
        """Seconds between pushes while a viewer holds a lease, else None."""
        if time.monotonic() < self._live_until_mono and self._live_interval > 0:
            return self._live_interval
        return None

    def _note_lease(self, ack: dict) -> None:
        live = ack.get("live")
        if not isinstance(live, dict):
            self._live_until_mono = 0.0
            return
        try:
            ttl = min(float(live.get("ttl_s", 0)), LIVE_MAX_TTL_S)
            interval = max(float(live.get("interval_s", 0)), LIVE_MIN_INTERVAL_S)
        except (TypeError, ValueError):
            self._live_until_mono = 0.0
            return
        if ttl <= 0:
            self._live_until_mono = 0.0
            return
        self._live_until_mono = time.monotonic() + ttl
        self._live_interval = interval

    async def _watermark(self, now: int) -> int:
        got = await self.store.kv_get(WATERMARK_KEY)
        if got is not None:
            try:
                return int(got[0])
            except ValueError:
                log.warning("rollup sync: bad watermark %r, restarting backfill", got[0])
        return (now - self._backfill_hours() * 3600) // 60 * 60 - 60

    async def _inventory(self) -> tuple[list[dict], list[list]]:
        devices = [
            {"device": d["label"], "vendor": d["vendor"] or "unknown",
             "kind": d["kind"] or "unknown", "slave_id": d["slave_id"],
             "display_name": d["display_name"],
             "first_seen": d["first_seen"], "last_seen": d["last_seen"]}
            for d in await self.store.list_devices()
        ]
        latest: list[list] = []
        for device, metrics in (await self.store.get_latest()).items():
            ts = int(metrics.get("_updated_at", 0))
            for metric, val in metrics.items():
                if metric.startswith("_"):
                    continue
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    latest.append([device, metric, ts, float(val), None])
                elif isinstance(val, str):
                    latest.append([device, metric, ts, None, val[:256]])
        return devices, latest

    async def _post(self, client: httpx.AsyncClient, body: dict) -> dict:
        raw = gzip.compress(
            json.dumps(body, separators=(",", ":")).encode("utf-8"), compresslevel=6)
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            **self._sign(raw),
        }
        r = await client.post(
            f"{self._endpoint().rstrip('/')}/api/ingest/rollups",
            content=raw, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    async def run_once(self, now: int | None = None) -> SyncResult:
        res = SyncResult()
        now = now if now is not None else int(time.time())
        try:
            wm = await self._watermark(now)
            async with self._client_factory() as client:
                for _ in range(MAX_BATCHES_PER_RUN):
                    rows = await self.store.closed_rollups_after(
                        wm, now=now, limit=BATCH_ROWS)
                    devices, latest = await self._inventory()
                    views = (await self.views.due(live=self.live_interval() is not None)
                             if self.views is not None else {})
                    if not rows and res.batches > 0:
                        res.caught_up = True
                        break
                    # Even with nothing new, send inventory + latest once per
                    # run so the cloud's device cards and live values stay fresh.
                    ack = await self._post(client, {
                        "batch_id": uuid.uuid4().hex, "devices": devices,
                        "rollups": rows, "latest": latest, "views": views})
                    if self.views is not None:
                        self.views.confirm(views)
                    self._note_lease(ack)
                    res.batches += 1
                    res.sent_rows += len(rows)
                    if not rows:
                        res.caught_up = True
                        break
                    top = ack.get("acked_through")
                    if top is None or int(top) < rows[-1][0]:
                        raise RuntimeError(
                            f"cloud acked {top}, expected >= {rows[-1][0]}")
                    wm = int(top)
                    await self.store.kv_set(WATERMARK_KEY, str(wm))
                else:
                    res.caught_up = False
            self.failures = 0
            self.last_ok_at = time.time()
            self.last_error = None
        except Exception as e:  # noqa: BLE001 — never let sync break the daemon
            self.failures += 1
            res.error = f"{type(e).__name__}: {e}"
            self.last_error = res.error
            log.warning("rollup sync failed (%d in a row): %s", self.failures, res.error)
        return res

    async def loop(
        self, stop: asyncio.Event, period_s: Callable[[], float],
        sleep: Callable[[asyncio.Event, float], Awaitable[bool]] | None = None,
    ) -> None:
        async def _wait(ev: asyncio.Event, t: float) -> bool:
            try:
                await asyncio.wait_for(ev.wait(), timeout=t)
                return True
            except asyncio.TimeoutError:
                return False
        wait = sleep or _wait
        while not stop.is_set():
            if not self._enabled():
                # Uploads switched off: do nothing, but keep checking so
                # switching them back on takes effect without a restart.
                if await wait(stop, 15.0):
                    return
                continue
            t0 = time.monotonic()
            try:
                res = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a bug here must not end the loop
                log.exception("rollup sync loop iteration crashed")
                self.failures += 1
                res = SyncResult(error=f"{type(e).__name__}: {e}")
            log.info("rollup sync: %d rows in %d batch(es), %s, %.1fs",
                     res.sent_rows, res.batches,
                     res.error or ("caught up" if res.caught_up else "more pending"),
                     time.monotonic() - t0)
            live = self.live_interval()
            if res.error:
                delay = backoff_delay(self.failures)
            elif not res.caught_up:
                delay = 1.0            # more backlog: keep draining
            elif live is not None:
                delay = live           # someone is watching
            else:
                delay = period_s()
            if await wait(stop, delay):
                return
