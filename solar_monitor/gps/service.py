"""USB GPS background service.

Owns a single async task that reads NMEA lines from the configured
serial port, decodes RMC sentences, and maintains the latest fix
in memory. On significant movement (>5 km from the last "applied"
fix, or >30 min since the last apply), the service mutates the
running config's lat/lon AND triggers a one-shot re-fetch of the
weather + PV-forecast caches so the dashboard sees the new
location within a poll cycle.

Why mutate Config in place vs introducing a LocationService:
  * The existing weather + forecast services read
    `cfg.weather.lat/lon` and `cfg.forecast.lat/lon` directly at
    each fetch. Mutating them in place keeps the change surface
    tiny, no refactor of the per-provider plumbing.
  * The mutation is in-memory only. Persisting to YAML on every
    move would write hundreds of files a day in a moving van.
    On daemon restart, the config-on-disk's lat/lon (or the user-
    configured one) is the fallback.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from .nmea import parse_rmc

log = logging.getLogger(__name__)

# Earth radius in km for the haversine distance check.
_EARTH_KM = 6371.0
# Minimum movement (km) before we treat the new fix as "different
# enough" to re-fetch weather/forecast. 5 km matches the spec in
# #125, finer triggers too many re-fetches during slow city
# driving, coarser misses real moves.
DEFAULT_MIN_MOVE_KM = 5.0
# How often (seconds) we force a re-apply even when the van's
# stationary, catches the edge case where the GPS time has drifted
# such that we should refresh sunrise/sunset for the new day.
DEFAULT_REFRESH_AFTER_S = 1800   # 30 min


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in km."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_KM * c


class GpsService:
    """Background NMEA reader + location-update dispatcher.

    Constructed with a port path (e.g. `/dev/ttyACM0`) and a baud
    rate. Reads serial in a thread pool (pyserial is sync) and
    decodes RMC sentences in the event loop.

    On a significant move, calls back into the scheduler to:
      1. Mutate `config.weather.lat/lon` and `config.forecast.lat/lon`.
      2. Trigger one-shot re-fetch of both services.
    The callback is injected at construction so we don't import
    scheduler.py here (avoids a circular import).
    """

    def __init__(
        self, *, port: str, baudrate: int = 9600,
        on_significant_move,
        min_move_km: float = DEFAULT_MIN_MOVE_KM,
        refresh_after_s: int = DEFAULT_REFRESH_AFTER_S,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.min_move_km = min_move_km
        self.refresh_after_s = refresh_after_s
        self._on_move = on_significant_move

        self._latest_fix: dict[str, Any] | None = None
        self._latest_fix_at: float = 0.0
        self._last_applied_lat: float | None = None
        self._last_applied_lon: float | None = None
        self._last_applied_at: float = 0.0

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="gps-reader")
        log.info("gps service started (%s @ %d baud)", self.port, self.baudrate)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    def get_status(self) -> dict[str, Any]:
        """Read-only snapshot of the GPS state for the API."""
        return {
            "port":             self.port,
            "baudrate":         self.baudrate,
            "latest_fix":       dict(self._latest_fix) if self._latest_fix else None,
            "latest_fix_age_s": max(0, int(time.time() - self._latest_fix_at))
                                if self._latest_fix_at else None,
            "last_applied_at":  int(self._last_applied_at)
                                if self._last_applied_at else None,
            "last_applied_lat": self._last_applied_lat,
            "last_applied_lon": self._last_applied_lon,
        }

    # ---- internals ----

    async def _run(self) -> None:
        """Open the serial port + loop reading lines. Reconnect on
        any pyserial error with exponential backoff."""
        try:
            import serial as pyserial
        except ImportError:
            log.error("pyserial not installed, GPS service disabled")
            return

        backoff = 1.0
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            ser = None
            try:
                ser = await loop.run_in_executor(
                    None, lambda: pyserial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=1.0,
                    ),
                )
                log.info("gps: opened %s", self.port)
                backoff = 1.0
                while not self._stop.is_set():
                    line_bytes = await loop.run_in_executor(None, ser.readline)
                    if not line_bytes:
                        # Idle, readline timed out; just loop and check stop.
                        continue
                    try:
                        line = line_bytes.decode("ascii", errors="replace")
                    except Exception:
                        continue
                    fix = parse_rmc(line)
                    if fix is None:
                        continue
                    self._latest_fix = fix
                    self._latest_fix_at = time.time()
                    await self._maybe_apply(fix)
            except Exception as e:
                log.warning("gps: %s, retrying in %.1fs", e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 60.0)
            finally:
                if ser is not None:
                    try:
                        await loop.run_in_executor(None, ser.close)
                    except Exception:
                        pass

    async def _maybe_apply(self, fix: dict[str, Any]) -> None:
        """Decide whether this fix is "different enough" to trigger
        a downstream weather/forecast refresh + log + dispatch."""
        lat, lon = fix["lat"], fix["lon"]
        now = time.time()
        # First fix after start, always apply.
        if self._last_applied_lat is None:
            should_apply = True
            distance_km = 0.0
        else:
            distance_km = _haversine_km(
                self._last_applied_lat, self._last_applied_lon, lat, lon,
            )
            should_apply = (
                distance_km >= self.min_move_km
                or (now - self._last_applied_at) >= self.refresh_after_s
            )
        if not should_apply:
            return
        self._last_applied_lat = lat
        self._last_applied_lon = lon
        self._last_applied_at = now
        log.info("gps: applied fix lat=%.6f lon=%.6f (moved %.2f km)",
                 lat, lon, distance_km)
        try:
            await self._on_move(lat, lon)
        except Exception:
            log.exception("gps: on_significant_move callback crashed")
