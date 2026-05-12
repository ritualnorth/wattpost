"""Open transports, instantiate drivers, drive poll cycles.

Two entry points:

* `Poller` — long-lived. Opens transports once at start, reuses them across
  many `poll()` calls. Used by the daemon scheduler. Re-opens any transport
  that's dropped between calls.

* `poll_once(config)` — convenience for one-shot CLI usage. Wraps Poller in
  an async context manager.

Reconnection policy is deliberately simple: if a transport reports it isn't
open at the start of a poll, try to open it. If that fails, every device on
that transport is skipped this cycle and we try again next cycle.
"""
from __future__ import annotations

import logging
import time
from typing import Any

# Importing transport + vendor packages triggers registration side effects.
from . import transport as _transport_pkg  # noqa: F401
from . import vendors as _vendors_pkg  # noqa: F401
from .transport import TRANSPORTS, Transport
from .vendors import VENDORS
from .config import Config, DeviceCfg

log = logging.getLogger(__name__)


def _build_transport(cfg: dict[str, Any]) -> Transport:
    ttype = cfg["type"]
    factory = TRANSPORTS.get(ttype)
    if factory is None:
        raise ValueError(
            f"unknown transport type {ttype!r}; registered: {list(TRANSPORTS)}"
        )
    return factory(cfg)


def _build_driver(dev: DeviceCfg):
    vendor = VENDORS.get(dev.vendor)
    if vendor is None:
        raise ValueError(f"unknown vendor {dev.vendor!r}; registered: {list(VENDORS)}")
    factory = vendor.drivers.get(dev.kind)
    if factory is None:
        raise ValueError(
            f"vendor {dev.vendor!r} has no driver for kind {dev.kind!r}; "
            f"available: {list(vendor.drivers)}"
        )
    return factory(slave_id=dev.slave_id, label=dev.label)


class Poller:
    """Long-lived orchestrator that holds transports open across polls."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._transports: dict[str, Transport] = {}

    async def open(self) -> None:
        """Build + open every configured transport."""
        transport_ids = {t["id"] for t in self.config.transports}
        if len(transport_ids) != len(self.config.transports):
            raise ValueError("duplicate transport ids in config")
        for tcfg in self.config.transports:
            t = _build_transport(tcfg)
            try:
                await t.open()
                self._transports[t.id] = t
            except Exception:
                log.exception("transport %s failed to open at startup", tcfg.get("id"))
                # Don't keep a half-open transport in the dict.
                self._transports.pop(t.id, None)

    async def close(self) -> None:
        for t in self._transports.values():
            try:
                await t.close()
            except Exception:
                log.exception("transport %s close failed", t.id)
        self._transports.clear()

    async def __aenter__(self) -> "Poller":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _ensure_open(self, transport_id: str) -> Transport | None:
        """Best-effort: return an open transport, rebuilding/reopening if needed."""
        t = self._transports.get(transport_id)
        if t is None:
            # Was not built at startup (config error or first-open failure).
            # Try to build it now from config.
            cfg = next(
                (c for c in self.config.transports if c["id"] == transport_id),
                None,
            )
            if cfg is None:
                return None
            try:
                t = _build_transport(cfg)
                await t.open()
                self._transports[transport_id] = t
            except Exception:
                log.exception("transport %s open failed", transport_id)
                return None
            return t

        # Was built; check liveness via the underlying client where possible.
        # For BLE transport, the bleak client tracks is_connected. We use
        # duck typing to avoid coupling to one transport implementation.
        client = getattr(t, "_client", None)
        if client is None or not getattr(client, "is_connected", True):
            try:
                log.info("reopening transport %s", transport_id)
                await t.close()
                await t.open()
            except Exception:
                log.exception("transport %s reopen failed", transport_id)
                return None
        return t

    async def poll(self) -> dict:
        """Run one full poll across every device in the config."""
        started = time.time()
        result: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "devices": {},
            "errors": [],
        }

        for dev in self.config.devices:
            t = await self._ensure_open(dev.transport)
            if t is None:
                result["errors"].append(
                    f"device {dev.label or dev.slave_id}: "
                    f"transport {dev.transport!r} unavailable"
                )
                continue
            try:
                driver = _build_driver(dev)
                data = await driver.poll(t)
                result["devices"][driver.label] = data
            except Exception as e:
                result["errors"].append(
                    f"device {dev.label or dev.slave_id}: {type(e).__name__}: {e}"
                )
                log.exception("device %s poll failed", dev.label)

        result["elapsed_seconds"] = round(time.time() - started, 2)
        return result


async def poll_once(config: Config) -> dict:
    """One-shot poll: open transports, run one cycle, close. CLI use only."""
    async with Poller(config) as poller:
        return await poller.poll()
