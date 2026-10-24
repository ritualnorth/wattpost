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
import math
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
SWITCH_POLL_S = 10                # how often a sleeping loop re-reads the Settings switch
LIVE_MIN_INTERVAL_S = 2           # whatever the cloud asks for, never faster than this
LIVE_MAX_INTERVAL_S = 60          # nor slower: a hostile reply must not park the loop
LIVE_MAX_TTL_S = 300              # nor leased for longer than this per grant


IDENTITY_KEY = "cloud_rollup_identity"
POISON_THRESHOLD = 3              # identical-batch failures before we bisect
# A status that says "this request is bad", as opposed to "try again later"
# (401 auth, 408/429 transient). Persistent ones are treated as a poison batch.
_RETRYABLE_STATUS = frozenset({401, 408, 429})


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text[:200]}")
        self.status = status


def _clean_str(v):
    return v.replace("\x00", "") if isinstance(v, str) else v


def scrub(obj):
    """Make a body safe for strict JSON: non-finite floats become None and NUL
    characters are stripped from every string (keys included)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, str):
        return _clean_str(obj)
    if isinstance(obj, dict):
        return {_clean_str(k) if isinstance(k, str) else k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


def _row_ok(r) -> bool:
    return all(math.isfinite(float(x)) for x in (r[3], r[4], r[5]))


@dataclass
class SyncResult:
    sent_rows: int = 0
    skipped_rows: int = 0          # poison / non-finite rows given up on
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
        identity: Callable[[], str] = lambda: "",
    ) -> None:
        # "{appliance_id}|{endpoint}": the watermark only means anything for
        # the cloud account it was earned against.
        self._identity = identity
        self._poison: tuple | None = None       # (batch key, consecutive failures)
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

    def live_remaining(self) -> float:
        """Seconds left on the current lease (0.0 when none)."""
        return max(0.0, self._live_until_mono - time.monotonic())

    def _note_lease(self, ack: dict) -> None:
        live = ack.get("live")
        if not isinstance(live, dict):
            self._live_until_mono = 0.0
            return
        try:
            ttl = float(live.get("ttl_s", 0))
            interval = float(live.get("interval_s", 0))
        except (TypeError, ValueError):
            self._live_until_mono = 0.0
            return
        if not (math.isfinite(ttl) and math.isfinite(interval)):
            self._live_until_mono = 0.0          # NaN / inf from the cloud
            return
        ttl = min(ttl, LIVE_MAX_TTL_S)
        interval = min(max(interval, LIVE_MIN_INTERVAL_S), LIVE_MAX_INTERVAL_S)
        if ttl <= 0:
            self._live_until_mono = 0.0
            return
        self._live_until_mono = time.monotonic() + ttl
        self._live_interval = interval

    async def _watermark(self, now: int) -> int:
        fresh = (now - self._backfill_hours() * 3600) // 60 * 60 - 60
        ident = self._identity()
        if ident:
            stored = await self.store.kv_get(IDENTITY_KEY)
            if stored is not None and stored[0] != ident:
                # Re-paired, or a different account/endpoint: what the old
                # cloud acknowledged says nothing about the new one.
                log.warning("rollup sync: cloud identity changed (%s -> %s); "
                            "restarting the backfill", stored[0], ident)
                await self.store.kv_set(WATERMARK_KEY, str(fresh))
                await self.store.kv_set(IDENTITY_KEY, ident)
                if self.views is not None and hasattr(self.views, "reset"):
                    self.views.reset()
                self._poison = None
                return fresh
            if stored is None:
                await self.store.kv_set(IDENTITY_KEY, ident)   # adopt; keep any watermark
        got = await self.store.kv_get(WATERMARK_KEY)
        if got is not None:
            try:
                wm = int(got[0])
                if wm <= now:
                    return wm
                # A watermark in the future (clock stepped forward, or a bad
                # ack got persisted) would silence uploads until real time
                # catches up. Never trust it: re-send the backfill window
                # (the cloud upserts, so this is idempotent).
                log.warning("rollup sync: watermark %d is in the future (now %d), "
                            "restarting backfill", wm, now)
            except ValueError:
                log.warning("rollup sync: bad watermark %r, restarting backfill", got[0])
        return fresh

    async def _inventory(self) -> tuple[list[dict], list[list]]:
        devices = [
            {"device": _clean_str(d["label"]), "vendor": _clean_str(d["vendor"] or "unknown"),
             "kind": _clean_str(d["kind"] or "unknown"), "slave_id": d["slave_id"],
             "display_name": _clean_str(d["display_name"]),
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
                    if math.isfinite(float(val)):
                        latest.append([_clean_str(device), _clean_str(metric), ts, float(val), None])
                elif isinstance(val, str):
                    latest.append([_clean_str(device), _clean_str(metric), ts, None,
                                   _clean_str(val[:256])])
        return devices, latest

    async def _post(self, client: httpx.AsyncClient, body: dict) -> dict:
        raw = gzip.compress(
            json.dumps(scrub(body), separators=(",", ":"),
                       allow_nan=False, default=str).encode("utf-8"), compresslevel=6)
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
            raise HttpStatusError(r.status_code, r.text)
        return r.json()

    async def _isolate(self, client: httpx.AsyncClient, rows: list) -> int:
        """The full batch keeps getting a 4xx/5xx. Find the rows the cloud
        refuses by bisection (rollups only, no inventory/views), so the good
        ones are delivered and the watermark can move past the bad ones.
        Returns how many rows were given up on.

        Guarded against mistaking an outage for poison: a rollup-less post
        must succeed first, and again before any single row is condemned;
        if the cloud is simply down this raises and nothing is skipped."""
        def control() -> dict:
            return {"batch_id": uuid.uuid4().hex, "devices": [], "rollups": [],
                    "latest": [], "views": {}}

        await self._post(client, control())
        skipped = 0

        async def go(chunk: list) -> None:
            nonlocal skipped
            try:
                ack = await self._post(client, {
                    "batch_id": uuid.uuid4().hex, "devices": [], "rollups": chunk,
                    "latest": [], "views": {}})
                top = ack.get("acked_through")
                if not isinstance(top, int) or isinstance(top, bool) or top != chunk[-1][0]:
                    raise RuntimeError(f"cloud acked {top!r}, expected exactly {chunk[-1][0]}")
            except HttpStatusError as e:
                if e.status in _RETRYABLE_STATUS:
                    raise
                if len(chunk) == 1:
                    await self._post(client, control())      # still alive? then it is the row
                    log.warning("rollup sync: cloud refuses row %r (%s); skipping it",
                                chunk[0], e)
                    skipped += 1
                    return
                mid = len(chunk) // 2
                await go(chunk[:mid])
                await go(chunk[mid:])

        await go(rows)
        return skipped

    async def run_once(self, now: int | None = None) -> SyncResult:
        res = SyncResult()
        now = now if now is not None else int(time.time())
        try:
            wm = await self._watermark(now)
            async with self._client_factory() as client:
                for _ in range(MAX_BATCHES_PER_RUN):
                    raw_rows = await self.store.closed_rollups_after(
                        wm, now=now, limit=BATCH_ROWS)
                    if not raw_rows and res.batches > 0:
                        res.caught_up = True
                        break
                    # Rows with a non-finite statistic can never be valid JSON;
                    # give up on them here rather than poisoning the batch.
                    rows = [r for r in raw_rows if _row_ok(r)]
                    if len(rows) != len(raw_rows):
                        res.skipped_rows += len(raw_rows) - len(rows)
                        log.warning("rollup sync: dropping %d row(s) with non-finite values",
                                    len(raw_rows) - len(rows))
                    if raw_rows and not rows:
                        wm = raw_rows[-1][0]
                        await self.store.kv_set(WATERMARK_KEY, str(wm))
                        continue
                    devices, latest = await self._inventory()
                    # Views are built once per run (first batch), and only
                    # when something is actually about to be sent.
                    views = (await self.views.due(live=self.live_interval() is not None)
                             if self.views is not None and res.batches == 0 else {})
                    # Even with nothing new, send inventory + latest once per
                    # run so the cloud's device cards and live values stay fresh.
                    try:
                        ack = await self._post(client, {
                            "batch_id": uuid.uuid4().hex, "devices": devices,
                            "rollups": rows, "latest": latest, "views": views})
                    except HttpStatusError as e:
                        if not rows or e.status in _RETRYABLE_STATUS:
                            raise
                        key = (wm, rows[-1][0], len(rows))
                        n = self._poison[1] + 1 if self._poison and self._poison[0] == key else 1
                        self._poison = (key, n)
                        if n < POISON_THRESHOLD:
                            raise
                        skipped = await self._isolate(client, rows)
                        self._poison = None
                        res.skipped_rows += skipped
                        res.batches += 1
                        res.sent_rows += len(rows) - skipped
                        wm = raw_rows[-1][0]
                        await self.store.kv_set(WATERMARK_KEY, str(wm))
                        continue
                    self._poison = None
                    if isinstance(ack, dict) and ack.get("rejected"):
                        log.warning("rollup sync: cloud rejected %s row(s) of the batch",
                                    ack.get("rejected"))
                    if self.views is not None:
                        self.views.confirm(views)
                    self._note_lease(ack)
                    res.batches += 1
                    res.sent_rows += len(rows)
                    if not rows:
                        res.caught_up = True
                        break
                    # The cloud acks the newest bucket of THIS batch, exactly.
                    # Less means it stored only part; more means a bad or
                    # hostile reply that would skip data (and, persisted, park
                    # the watermark in the future).
                    top = ack.get("acked_through")
                    if (not isinstance(top, int) or isinstance(top, bool)
                            or top != rows[-1][0]):
                        raise RuntimeError(
                            f"cloud acked {top!r}, expected exactly {rows[-1][0]}")
                    wm = max(top, raw_rows[-1][0])    # trailing dropped rows are behind us too
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
            """Sleep up to `t` seconds, but in slices, waking early when the
            Settings switch flips so turning uploads back on takes effect
            within seconds instead of at the end of the (up to 60 min) period."""
            was = self._enabled()
            left = t
            while left > 0:
                step = min(left, SWITCH_POLL_S)
                try:
                    await asyncio.wait_for(ev.wait(), timeout=step)
                    return True
                except asyncio.TimeoutError:
                    pass
                left -= step
                if self._enabled() != was:
                    return False
            return False
        wait = sleep or _wait
        while not stop.is_set():
            if not self._enabled():
                # Uploads switched off: do nothing, but keep checking so
                # switching them back on takes effect without a restart.
                if await wait(stop, SWITCH_POLL_S):
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
                # someone is watching; never sleep past the end of the lease
                delay = min(live, max(self.live_remaining(), 0.1))
            else:
                delay = period_s()
            if await wait(stop, delay):
                return
