"""Background heartbeat poster.

Reads the latest bank snapshot from the scheduler's `last_result`
plus today's energy aggregates from the scheduler, packages them
into a small JSON payload, and POSTs to `<endpoint>/api/heartbeat`
with the bearer token.

Failures are swallowed — losing internet must not break the local
dashboard. Each failure is logged at WARNING for diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import CloudCfg

log = logging.getLogger(__name__)


class CloudService:
    def __init__(self, cfg: CloudCfg, scheduler) -> None:
        self.cfg = cfg
        self.scheduler = scheduler
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if not self.cfg.bearer_token:
            log.info("cloud: no bearer_token configured — skipping heartbeat loop")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="cloud-heartbeat")
        log.info("cloud heartbeat service started (endpoint=%s, every %dm)",
                 self.cfg.endpoint, self.cfg.heartbeat_minutes)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def heartbeat_once(self) -> bool:
        """Build + send one heartbeat. Returns True on 2xx, False on
        anything else. Used by the loop and also exposed for the
        Settings UI's "Send heartbeat now" button."""
        payload = self._build_payload()
        url = f"{self.cfg.endpoint.rstrip('/')}/api/heartbeat"
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }
        try:
            # follow_redirects=True so an appliance still pointing at
            # an older hostname (e.g. https://wattpost.io after we
            # moved the API to app.wattpost.io) succeeds via the 308
            # rather than silently 308-ing into a no-op. POST → POST
            # is method-preserving under 308 by spec.
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
            ) as client:
                r = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            log.warning("cloud heartbeat failed: %s", e)
            return False
        if r.status_code >= 400:
            log.warning("cloud heartbeat HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True

    def _build_payload(self) -> dict[str, Any]:
        """Pull SoC + net power from the scheduler's last snapshot.
        Defensive about every step — a half-built snapshot during
        startup should not crash the heartbeat task."""
        soc_pct = None
        net_w = None
        try:
            last = self.scheduler.last_result or {}
            devices = last.get("devices") or []
            for d in devices:
                if d.get("label") == "bank":
                    latest = d.get("latest") or {}
                    soc_pct = latest.get("soc_pct")
                    net_w   = latest.get("power_w")
                    break
        except Exception:
            log.exception("cloud heartbeat: could not read snapshot")

        # Free-form extras for the cloud dashboard to render later.
        # Keep this concise — the cloud caps extras at 2 KiB.
        extras: dict[str, Any] = {}
        try:
            from .. import __version__
            extras["version"] = __version__
        except Exception:
            pass
        try:
            alert_count = len([
                r for r in (getattr(self.scheduler._alerts, "rules", []) or [])
                if r.id in getattr(self.scheduler._alerts, "_last_fired", {})
            ])
            extras["alert_count"] = alert_count
        except Exception:
            pass

        return {
            "soc_pct": soc_pct,
            "net_w":   net_w,
            "extras":  extras,
        }

    async def _loop(self) -> None:
        # First heartbeat immediately so the cloud's online pill flips
        # within seconds of the daemon coming up, not after the first
        # full poll_minutes window.
        try:
            await self.heartbeat_once()
        except Exception as e:
            log.warning("initial cloud heartbeat failed: %s", e)
        period_s = max(1, self.cfg.heartbeat_minutes) * 60
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=period_s)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.heartbeat_once()
            except Exception as e:
                log.warning("cloud heartbeat failed: %s", e)
