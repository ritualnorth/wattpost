"""Passive BLE Instant Readout transport for Victron Energy devices.

Victron's BLE story is fundamentally different from Renogy's BT-2:
the device broadcasts encrypted "Instant Readout" advertisements
~once per second, and consumers decrypt them with a per-device
encryption key (revealed in VictronConnect under "Product info →
Show device key"). There's no GATT connection, no Modbus, no
write capability — strictly read-only, but every metric you'd
want is in the payload.

Why a separate transport class (not BLE Modbus):

  * The orchestrator's Transport ABC requires a `request()` method
    (the Modbus pattern). Victron is passive: requests don't exist,
    the device pushes data on its own schedule. We satisfy the ABC
    by implementing `request()` that raises — Victron drivers
    override `poll()` and never call it.

  * One BleakScanner per process is plenty; spinning one up per
    Victron transport instance would be wasteful and risks scan
    conflicts. We use a module-level singleton (`_GLOBAL_SCANNER`)
    that every Victron transport instance registers with — the
    scanner's detection callback routes advertisements by MAC to
    the right transport instance for decoding.

  * Driver reads via `transport.get_latest()` — a dict of decoded
    fields or None if no advertisement landed yet / is stale.

See [[project-target-customer]] in memory for why this matters:
SmartShunt is the highest-leverage driver to add (Persona B —
"budget upgrader who buys a shunt for first-time visibility").
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

class _SharedVictronScanner:
    """Singleton wrapping a BleakScanner that fans every Victron
    advertisement out to the transport instance registered for the
    sending device's MAC. Transports register on open(), unregister
    on close(); the scanner only runs while at least one transport
    is subscribed."""

    def __init__(self) -> None:
        self._subscribers: dict[str, "BleVictronAdvertiseTransport"] = {}
        self._scanner: BleakScanner | None = None
        self._lock = asyncio.Lock()

    async def register(self, transport: "BleVictronAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers[transport.address] = transport
            if self._scanner is None:
                # Active scan (default) sees advertisements quickly;
                # passive would also work and is slightly cheaper but
                # has poorer device-name visibility for setup-time
                # discovery. Active is fine on the dongle.
                self._scanner = BleakScanner(detection_callback=self._on_detection)
                await self._scanner.start()
                log.info("victron scanner started (subscribers=%d)",
                         len(self._subscribers))

    async def unregister(self, transport: "BleVictronAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers.pop(transport.address, None)
            if not self._subscribers and self._scanner is not None:
                try:
                    await self._scanner.stop()
                except Exception:
                    log.exception("victron scanner stop failed")
                self._scanner = None
                log.info("victron scanner stopped (no subscribers)")

    def _on_detection(self, device, ad_data) -> None:
        # Cheap fast-path filter first — most advertisements on a
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


_GLOBAL_SCANNER: _SharedVictronScanner | None = None


def _scanner() -> _SharedVictronScanner:
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = _SharedVictronScanner()
    return _GLOBAL_SCANNER


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
        # value as a hex *string* — victron-ble's Device class expects
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
        # The latest parsed payload — typed as Any because the concrete
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
            "transport — drivers must override poll() and use get_latest()"
        )

    # ---------- Victron-specific surface ----------

    def get_latest(self):
        """The most recent decoded `DeviceData` (or subclass), or None
        if no advertisement has arrived yet / the last one is stale.

        Per-device drivers (e.g. SmartShunt) call the appropriate
        `parsed.get_voltage()` / `get_current()` / etc. — we don't
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
        drivers can record exactly how silent a device has gone — the
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
                # library doesn't decode yet. Log once and bail — we
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
