"""Passive BLE Instant Readout transport for Victron Energy devices.

Device broadcasts encrypted advertisements ~1Hz; consumers decrypt
with the per-device key from VictronConnect (Product info → Show
device key). No GATT, no writes.

`request()` raises (push-only); drivers override `poll()` to read
via `transport.get_latest()`. A module-level singleton scanner
(`_GLOBAL_SCANNER`) routes adverts by MAC to the right transport.
"""
from __future__ import annotations

import asyncio
import binascii
import logging
import time
from typing import Any

from bleak import BleakScanner

from .base import Transport, TransportError
from .registry import register_transport

log = logging.getLogger(__name__)

# Victron's BLE manufacturer ID. All Instant Readout advertisements
# include their payload under this key in advertisement_data.manufacturer_data.
VICTRON_MANUFACTURER_ID = 0x02E1

# How stale a decoded payload may be before get_latest() reports None.
# Devices broadcast roughly once per second, so 60s of silence is well
# beyond "BLE wobble" and into "the dongle's actually gone" territory.
STALE_AFTER_SECONDS = 60.0


# ---------- module-level shared scanner ----------

def _list_hci_adapters() -> list[str]:
    """Local BLE adapters as ['hci0', 'hci1', …], read from sysfs. An
    empty list means "let Bleak pick the default" (non-Linux hosts, or
    sysfs unreadable) — callers fall back to a single default scanner."""
    import glob as _glob
    import os as _os
    import re as _re
    try:
        # /sys/class/bluetooth also lists per-connection children like
        # "hci0:64" — only real adapters match ^hci\d+$.
        return sorted(
            n for n in (_os.path.basename(p)
                        for p in _glob.glob("/sys/class/bluetooth/hci*"))
            if _re.fullmatch(r"hci\d+", n)
        )
    except Exception:
        return []


class _SharedVictronScanner:
    """Singleton wrapping a BleakScanner that fans every Victron
    advertisement out to the transport instance registered for the
    sending device's MAC. Transports register on open(), unregister
    on close(); the scanner only runs while at least one transport
    is subscribed."""

    def __init__(self) -> None:
        self._subscribers: dict[str, "BleVictronAdvertiseTransport"] = {}
        # One BleakScanner per local HCI adapter (hci0, hci1, …) so a
        # Victron device on ANY radio is heard, not just the BlueZ
        # default. Empty until the first transport registers.
        self._scanners: list[BleakScanner] = []
        self._lock = asyncio.Lock()
        # Adapter-health tracking (#244). When the BLE dongle wedges
        # (Realtek firmware bug on the RTL8761B family is the common
        # case) the scanner still reports "running" but no callbacks
        # ever fire. Compare _last_any_advert_at against
        # _scan_started_at to tell "we've been listening but heard
        # nothing in N seconds" from "we just started, give it time".
        self._scan_started_at: float = 0.0
        self._last_any_advert_at: float = 0.0

    async def _start_all(self) -> None:
        """Start one scanner per HCI adapter. Best-effort per adapter:
        a failure on one (busy/missing) is logged and skipped so the
        others still run. Falls back to the Bleak default when no
        adapters can be enumerated (non-Linux / single-radio hosts).
        Active scan (default) sees advertisements quickly."""
        adapters = _list_hci_adapters() or [None]
        for ad in adapters:
            try:
                kw: dict = {"detection_callback": self._on_detection}
                if ad:
                    kw["adapter"] = ad
                sc = BleakScanner(**kw)
                await sc.start()
                self._scanners.append(sc)
                log.info("victron scanner started on %s (subscribers=%d)",
                         ad or "default", len(self._subscribers))
            except Exception:
                log.exception("victron scanner: failed to start on %s",
                              ad or "default")
        self._scan_started_at = time.monotonic()
        # Reset on every start, we're tracking "did we hear ANY advert
        # since the most recent scan-start".
        self._last_any_advert_at = 0.0

    async def _stop_all(self) -> None:
        for sc in self._scanners:
            try:
                await sc.stop()
            except Exception:
                log.exception("victron scanner stop failed")
        self._scanners = []

    async def register(self, transport: "BleVictronAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers[transport.address] = transport
            if not self._scanners:
                await self._start_all()

    async def unregister(self, transport: "BleVictronAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers.pop(transport.address, None)
            if not self._subscribers and self._scanners:
                await self._stop_all()
                log.info("victron scanner stopped (no subscribers)")

    async def pause(self) -> bool:
        """Briefly stop every scanner so another transport can run its
        own discovery. BlueZ only allows one in-flight discovery session
        per HCI adapter, without this the Renogy BT-2 transport's
        `find_device_by_address` fights the Victron passive scanner and
        loses with `org.bluez.Error.InProgress`. We stop ALL adapters'
        scanners (the peer connect may land on any radio). Returns True
        if anything was running (so the caller knows to resume)."""
        async with self._lock:
            if not self._scanners:
                return False
            await self._stop_all()
            log.info("victron scanner paused (peer transport scanning)")
            return True

    async def resume(self) -> None:
        """Counterpart to pause(). Restarts scanners if there are still
        subscribers; if every Victron transport closed during the pause
        window, leave them stopped (unregister-equivalent)."""
        async with self._lock:
            if self._scanners or not self._subscribers:
                return
            await self._start_all()
            log.info("victron scanner resumed (subscribers=%d)",
                     len(self._subscribers))

    def _on_detection(self, device, ad_data) -> None:
        # Stamp adapter-alive marker before any filtering, receiving
        # ANY advert means the dongle is delivering data, even if
        # none of them are Victron right now. Used by adapter_health()
        # to distinguish "wedged dongle" from "no Victron devices in
        # range".
        self._last_any_advert_at = time.monotonic()
        # Always-on discovery: drop a classified row in the shared registry
        # for ANY recognised broadcast device (Victron / sensors / Renogy
        # BT), so the setup UI can offer in-range gear without the user
        # configuring a transport first. Best-effort; never perturbs the
        # Victron decode path below.
        try:
            from . import ble_discovery as _disc
            _disc.record(device, ad_data)
        except Exception:
            pass
        # Cheap fast-path filter first, most advertisements on a
        # crowded RF environment have nothing to do with Victron.
        mfr = getattr(ad_data, "manufacturer_data", None) or {}
        payload = mfr.get(VICTRON_MANUFACTURER_ID)
        if payload is None:
            return
        addr = device.address.upper()
        t = self._subscribers.get(addr)
        if t is None:
            return
        try:
            t._on_advertisement(payload)
        except Exception:
            log.exception("[%s] advertisement decode crashed", t.id)

    def adapter_health(self) -> str:
        """Snapshot of the BLE adapter's state, from the scanner's
        perspective. Returns one of:

          "ok"     , scanner running, received an advert recently
          "wedged" , scanner running ≥30s, but no callbacks at all.
                      Classic Realtek RTL8761B firmware lockup.
          "idle"   , no subscribers / scanner not running
          "warming", scanner just started, hasn't been long enough
                      to call it wedged yet

        Heartbeat extras surface this so the cloud dashboard can
        render a "Bluetooth dongle not responding, try a power-cycle"
        banner instead of the user seeing every Victron device
        independently appear silent."""
        if not self._scanners or self._scan_started_at == 0.0:
            return "idle"
        now = time.monotonic()
        running_for = now - self._scan_started_at
        if self._last_any_advert_at > 0.0:
            since = now - self._last_any_advert_at
            # Within the last 90s, adapter is delivering.  Adverts
            # from non-Victron devices are common enough in any
            # populated RF environment that even rural installs
            # should see *something*.
            if since < 90.0:
                return "ok"
        # No callbacks at all yet.  Give the dongle 30s to warm up
        # before declaring it wedged, BlueZ filter rearm + first
        # scan window takes 5-10s on slow hosts.
        if running_for < 30.0:
            return "warming"
        return "wedged"


_GLOBAL_SCANNER: _SharedVictronScanner | None = None


def _scanner() -> _SharedVictronScanner:
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = _SharedVictronScanner()
    return _GLOBAL_SCANNER


def adapter_health() -> str:
    """Module-level shortcut for the cloud heartbeat to query without
    instantiating the singleton if it doesn't already exist. Returns
    "idle" when no scanner has ever been built (no Victron devices
    configured)."""
    if _GLOBAL_SCANNER is None:
        return "idle"
    return _GLOBAL_SCANNER.adapter_health()


# ---------- transport ----------

class BleVictronAdvertiseTransport(Transport):
    """Passive BLE Instant Readout transport for a single Victron device.

    Configured with a MAC address + per-device encryption key (hex).
    Maintains the most recently decoded payload + timestamp; the driver
    reads via `get_latest()`. `request()` is unsupported (passive only).
    """

    def __init__(
        self,
        id: str,
        address: str,
        encryption_key_hex: str,
    ) -> None:
        self.id = id
        self.address = address.upper()
        # Encryption keys ship as 32-char hex (16 bytes AES-128). Tolerate
        # whitespace/separators the user may have pasted in. We keep the
        # value as a hex *string*, victron-ble's Device class expects
        # the hex form and decodes internally on each parse() call.
        cleaned = encryption_key_hex.replace(" ", "").replace(":", "").replace("-", "")
        try:
            decoded = binascii.unhexlify(cleaned)
        except Exception as e:
            raise ValueError(f"encryption_key is not valid hex: {e}")
        if len(decoded) != 16:
            raise ValueError(
                f"encryption_key must be 16 bytes / 32 hex chars; "
                f"got {len(decoded)} bytes after cleaning"
            )
        self._key_hex = cleaned.lower()

        # victron-ble's Device class binds a key to a device subclass
        # (BatteryMonitor for SmartShunt, SolarCharger for SmartSolar,
        # …) at construction; we don't know which one we're talking to
        # until the first advertisement lands and detect_device_type()
        # classifies it. Resolved lazily in _on_advertisement().
        self._device = None
        # The latest parsed payload, typed as Any because the concrete
        # class depends on the device (BatteryMonitorData,
        # SolarChargerData, …). Drivers cast / inspect as needed.
        self._latest: Any = None
        self._latest_at: float = 0.0
        self._registered = False

    async def open(self) -> None:
        if self._registered:
            return
        await _scanner().register(self)
        self._registered = True
        log.info("[%s] passive Victron listener active for %s",
                 self.id, self.address)

    async def close(self) -> None:
        if not self._registered:
            return
        await _scanner().unregister(self)
        self._registered = False

    async def request(
        self, frame: bytes, expected_response_len: int, timeout: float = 5.0,
    ) -> bytes:
        # Victron BLE is one-way: device broadcasts, we listen. The
        # driver should never call request() on this transport; raising
        # makes the misuse loud rather than silent.
        raise TransportError(
            f"{self.id}: request() is unsupported on a passive Victron "
            "transport, drivers must override poll() and use get_latest()"
        )

    # ---------- Victron-specific surface ----------

    def get_latest(self):
        """The most recent decoded `DeviceData` (or subclass), or None
        if no advertisement has arrived yet / the last one is stale.

        Per-device drivers (e.g. SmartShunt) call the appropriate
        `parsed.get_voltage()` / `get_current()` / etc., we don't
        flatten to a dict here because each Victron device kind has
        a different set of methods and aux-mode-dependent semantics."""
        if self._latest is None:
            return None
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest

    def last_advertisement_age_s(self) -> float | None:
        """Real-time age of the last decoded advertisement. Unlike
        `get_latest()`, this keeps growing past STALE_AFTER_SECONDS so
        drivers can record exactly how silent a device has gone, the
        dashboard surfaces this as "Silent for X min" + greys out the
        device card (#171). Returns None if we've never seen an
        advertisement from this device."""
        if self._latest_at == 0.0:
            return None
        return max(0.0, time.time() - self._latest_at)

    def get_device_class_name(self) -> str | None:
        """Name of the victron-ble DeviceData subclass currently bound
        to this transport (e.g. "BatteryMonitor", "SolarCharger").
        Used by the driver to verify it's looking at the right device
        type before calling kind-specific methods."""
        return self._device.__class__.__name__ if self._device else None

    def _on_advertisement(self, payload: bytes) -> None:
        # Lazy device-type detection on the first packet. victron-ble
        # exposes detect_device_type(payload) which returns the right
        # subclass (BatteryMonitor / SolarCharger / ...) based on a
        # model-id byte in the payload header.
        if self._device is None:
            try:
                from victron_ble.devices import detect_device_type
            except ImportError:
                log.error("[%s] victron-ble package not installed", self.id)
                return
            device_class = detect_device_type(payload)
            if device_class is None:
                # Not a recognised Victron Instant Readout model. Could
                # be an old-firmware GATT-only device, or a model the
                # library doesn't decode yet. Log once and bail, we
                # don't want to spam every advertisement.
                if not getattr(self, "_unsupported_logged", False):
                    log.warning("[%s] payload not a known Victron model; "
                                "first %d bytes=%s", self.id, min(8, len(payload)),
                                payload[:8].hex())
                    self._unsupported_logged = True
                return
            try:
                self._device = device_class(self._key_hex)
            except Exception as e:
                log.error("[%s] failed to construct %s with key: %s",
                          self.id, device_class.__name__, e)
                return
            log.info("[%s] detected device type: %s",
                     self.id, device_class.__name__)

        try:
            parsed = self._device.parse(payload)
        except Exception as e:
            # Most parse failures = wrong encryption key. Log
            # rate-limited; a wrong key means EVERY advertisement
            # fails, which would flood the logs.
            now = time.time()
            last = getattr(self, "_last_parse_warn_at", 0)
            if now - last > 60:
                log.warning("[%s] parse failed (wrong key?): %s", self.id, e)
                self._last_parse_warn_at = now
            return

        self._latest = parsed
        self._latest_at = time.time()


@register_transport("ble_victron_advertise")
def _factory(cfg: dict) -> BleVictronAdvertiseTransport:
    """Build a BleVictronAdvertiseTransport from a YAML config dict.

    Expected fields:
      id: stable id (e.g. "victron_shunt_main")
      type: "ble_victron_advertise"
      address: BT MAC (e.g. CC:CC:CC:CC:CC:CC)
      encryption_key: 32-char hex string from VictronConnect's
                      "Product info → Show device key"
    """
    return BleVictronAdvertiseTransport(
        id=cfg["id"],
        address=cfg["address"],
        encryption_key_hex=cfg["encryption_key"],
    )
