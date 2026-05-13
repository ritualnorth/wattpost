"""Spawn `cloudflared tunnel run --token <token>` and keep it alive.

The cloud allocates one Cloudflare Tunnel per appliance at pair
time. The token we get back from `/api/pair/exchange` is the only
thing cloudflared needs — it embeds the account / tunnel / secret
triple, and cloudflared knows the rest (where to connect, how to
identify itself).

Lifecycle:
  - Started when CloudCfg.tunnel_token is non-empty AND
    cloudflared is on PATH.
  - Restart on crash with exponential backoff (cap 5 min) so a
    transient network blip doesn't blackhole the tunnel forever.
  - Clean shutdown on scheduler stop (SIGTERM → wait → SIGKILL).
  - Logs cloudflared's stdout/stderr at INFO so a `journalctl -u
    wattpost` tail surfaces tunnel events.

Errors here NEVER affect the rest of the daemon. The local UI, the
poll loop, the alert engine — all keep running if the tunnel can't
come up.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from ..config import CloudCfg

log = logging.getLogger(__name__)

# Backoff schedule for the supervisor loop. Each value is the
# delay (seconds) before re-spawning cloudflared after a crash;
# we walk through the list and cap at the last entry.
_BACKOFF_SECONDS = [2, 5, 15, 60, 300]


class TunnelService:
    def __init__(self, cfg: CloudCfg) -> None:
        self.cfg = cfg
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = asyncio.Event()
        self._restarts = 0

    @staticmethod
    def is_available(cfg: CloudCfg) -> bool:
        """Should we spin this up? Both token present AND
        cloudflared on PATH. Caller does the env check; we do the
        binary check here so a missing binary is logged once
        rather than per restart attempt."""
        if not cfg.tunnel_token:
            return False
        if shutil.which("cloudflared") is None:
            log.warning(
                "tunnel: cloudflared not found on PATH — "
                "install from https://github.com/cloudflare/cloudflared/releases "
                "to expose this appliance at %s",
                cfg.tunnel_hostname or "<slug>.wattpost.io",
            )
            return False
        return True

    async def start(self) -> None:
        if not self.is_available(self.cfg):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._supervise(), name="tunnel-supervisor")
        log.info("tunnel service started (hostname=%s)",
                 self.cfg.tunnel_hostname or "<unknown>")

    async def stop(self) -> None:
        self._stop.set()
        # Tell cloudflared to wind down first; the supervisor task
        # exits cleanly once the child is dead.
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("tunnel: cloudflared didn't terminate in 5s, killing")
                self._proc.kill()
                try:
                    await self._proc.wait()
                except Exception:
                    pass
            except Exception:
                pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
            self._task = None

    async def _supervise(self) -> None:
        """Run cloudflared. If it exits, log + sleep + retry until
        stop() is called. Resets the backoff counter when a run
        survives 60+ seconds — short crashes get exponential,
        stable-but-occasional-disconnect runs don't get punished."""
        while not self._stop.is_set():
            try:
                run_start = time.monotonic()
                await self._run_once()
                survived = time.monotonic() - run_start
                if survived >= 60:
                    self._restarts = 0
            except Exception as e:
                log.warning("tunnel: cloudflared launch failed: %s", e)
            if self._stop.is_set():
                break
            delay = _BACKOFF_SECONDS[min(self._restarts, len(_BACKOFF_SECONDS) - 1)]
            self._restarts += 1
            log.info("tunnel: respawning cloudflared in %ds (attempt %d)",
                     delay, self._restarts)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass

    async def _run_once(self) -> None:
        """Spawn cloudflared once. Returns when the process exits
        for any reason — supervisor decides whether to respawn."""
        cmd = ["cloudflared", "tunnel", "--no-autoupdate", "run",
               "--token", self.cfg.tunnel_token]
        # Logs go to our logger at INFO so the daemon's existing log
        # plumbing carries them; cloudflared's own JSON logging is
        # human-readable enough at INFO.
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        log.info("tunnel: cloudflared started pid=%s", self._proc.pid)

        # Drain output line-by-line. Stops naturally when the child
        # closes its pipe.
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                log.info("[cloudflared] %s", line)
        rc = await self._proc.wait()
        log.warning("tunnel: cloudflared exited rc=%s", rc)
        self._proc = None
