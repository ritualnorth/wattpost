"""Passive BLE listener for Mopeka Pro / Check Pro tank-level sensors (#254).

Mopeka sells small ultrasonic sensors that magnetically mount to the
bottom of a propane / water / oat / coffee tank and broadcast the
fluid level over BLE. Three things to know:

  * **Plaintext** advertisements, no encryption key needed (unlike
    Victron, which requires a per-device AES key). Just listen.
  * Manufacturer ID is **0x0059** (Nordic Semiconductor, Mopeka's
    chip vendor). Several BLE devices use this prefix; the first
    byte of payload disambiguates Mopeka (hardware-id 0x03/0x05/
    0x06/0x08) from generic Nordic samples.
  * Multiple firmware revisions in the wild. Pro Check (0x03) and
    Pro Plus (0x05) are the volume sellers; Pro Check H2O (0x06)
    and Bottom-mounted Pro (0x08) the niche variants.

This transport is **read-only** and exposes the decoded latest
advertisement via ``get_latest()``. Drivers that want a fluid-level
percentage layer per-install calibration (tank height, fluid type
→ speed-of-sound) on top of the raw distance reading we emit here.

Why a separate shared scanner from Victron's:
  * Different manufacturer-id filter (cleaner than one big scanner
    that fans Victron + Mopeka through the same callback)
  * Decouples Mopeka driver development from the Victron transport
    (their adapter-health debugging is enough surface as it is)

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak import BleakScanner

from .base import Transport, TransportError
from .registry import register_transport

log = logging.getLogger(__name__)

# Nordic Semiconductor manufacturer ID. Mopeka uses Nordic nRF chips
# and ships with this ID. Other Nordic devices exist too, we filter
# further by the hardware-id byte at the start of the payload.
MOPEKA_MANUFACTURER_ID = 0x0059

# Mopeka hardware-id bytes (first byte of manufacturer_data[mfr_id]).
# Source: reverse-engineered protocol, validated against the public
# `mopeka_iot_ble` Home Assistant integration's parser.
_HW_KINDS = {
    0x03: "pro_check",           # Universal LPG / propane (most common)
    0x05: "pro_plus",            # Pro Plus, extended range
    0x06: "pro_check_h2o",       # Water tanks, different speed-of-sound
    0x08: "pro_check_bottom",    # Bottom-mounted variant
    0x09: "pro_universal",       # Newer firmware "Universal", multi-fluid
}

# How stale a decoded payload may be before get_latest() reports None.
# Mopekas broadcast every ~10s by default (battery-conserving); 60s
# of silence means the unit's likely battery-dead or out of range.
STALE_AFTER_SECONDS = 90.0


# ---------- payload parser ----------

def parse_mopeka_advertisement(mfr_data: bytes) -> dict[str, Any] | None:
    """Decode a Mopeka manufacturer_data byte string into a dict, or
    None if the bytes don't look like Mopeka.

    Returned dict shape:
      {
        "hardware_kind":   str,         # e.g. "pro_check"
        "battery_pct":     float | None,
        "temperature_c":   int   | None,
        "quality":         int,          # 0=junk, 1=ok, 2=good, 3=excellent
        "raw_distance_mm": int   | None, # ultrasonic time-of-flight reading
        "accel_x":         int,          # signed, ±127
        "accel_y":         int,
      }

    The raw distance + accelerometer let a driver compute a calibrated
    fluid level (with the tank's empty/full distance bounds + tilt
    rejection). We don't compute a fluid-level % here because that's
    per-install, what we DO compute is everything we can without
    knowing the tank.
    """
    if not mfr_data or len(mfr_data) < 8:
        return None
    hw_byte = mfr_data[0]
    kind = _HW_KINDS.get(hw_byte)
    if kind is None:
        return None  # Not a Mopeka, some other Nordic device

    # Battery: byte 1, lower 7 bits map to 2.2-3.45V via /32; convert to
    # a rough percentage (lithium coin cell discharge curve is roughly
    # linear in that window).
    raw_batt = mfr_data[1] & 0x7F
    voltage = raw_batt / 32.0
    if voltage < 2.2:
        battery_pct: float | None = 0.0
    elif voltage > 3.45:
        battery_pct = 100.0
    else:
        battery_pct = round((voltage - 2.2) / (3.45 - 2.2) * 100.0, 1)

    # Temperature: byte 2 lower 6 bits, offset -40°C. Range -40 to +23.
    # Above +23 reads as a higher 6-bit value (the sensor caps there).
    raw_temp = mfr_data[2] & 0x3F
    temperature_c: int | None = raw_temp - 40

    # Quality bits + raw distance: bytes 3-4 little-endian. Top 2 bits
    # are quality (0-3); lower 14 bits are the raw distance reading in
    # units of (1/64) mm of ultrasonic time-of-flight (firmware-dep).
    rd = int.from_bytes(mfr_data[3:5], "little")
    quality = (rd >> 14) & 0x03
    raw_distance = rd & 0x3FFF
    # Quality=0 means the sensor couldn't get a clean reflection;
    # surface as None so drivers don't render junk.
    distance_mm: int | None = raw_distance if quality > 0 else None

    # Accelerometer X / Y. Signed bytes. Used for tilt-reject: if the
    # tank is being moved (e.g. swapping cylinders) the reading is
    # untrustworthy, regardless of quality bits.
    def _s8(b: int) -> int:
        return b - 256 if b > 127 else b
    accel_x = _s8(mfr_data[5]) if len(mfr_data) > 5 else 0
    accel_y = _s8(mfr_data[6]) if len(mfr_data) > 6 else 0

    return {
        "hardware_kind":   kind,
        "battery_pct":     battery_pct,
        "temperature_c":   temperature_c,
        "quality":         quality,
        "raw_distance_mm": distance_mm,
        "accel_x":         accel_x,
        "accel_y":         accel_y,
    }


# ---------- module-level shared scanner ----------

class _SharedMopekaScanner:
    """Singleton, one BleakScanner that fans Mopeka adverts out to
    the transport instance registered for the sending device's MAC."""

    def __init__(self) -> None:
        self._subscribers: dict[str, "BleMopekaAdvertiseTransport"] = {}
        self._scanner: BleakScanner | None = None
        self._lock = asyncio.Lock()

    async def register(self, transport: "BleMopekaAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers[transport.address] = transport
            if self._scanner is None:
                self._scanner = BleakScanner(detection_callback=self._on_advert)
                await self._scanner.start()
                log.info("Mopeka shared scanner started (%d subscriber(s))",
                         len(self._subscribers))

    async def unregister(self, transport: "BleMopekaAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers.pop(transport.address, None)
            if not self._subscribers and self._scanner is not None:
                try:
                    await self._scanner.stop()
                except Exception:
                    log.exception("Mopeka scanner.stop() failed")
                self._scanner = None
                log.info("Mopeka shared scanner stopped (no subscribers)")

    async def pause(self) -> bool:
        """Stop the scanner so another caller can run a discovery on
        the same HCI adapter. Returns True iff we actually stopped a
        running scanner (caller should then resume())."""
        async with self._lock:
            if self._scanner is None:
                return False
            try:
                await self._scanner.stop()
            except Exception:
                log.exception("Mopeka scanner pause: stop failed")
            self._scanner = None
            log.info("Mopeka scanner paused (peer transport scanning)")
            return True

    async def resume(self) -> None:
        """Restart the scanner if there are still subscribers. No-op
        otherwise."""
        async with self._lock:
            if self._scanner is not None or not self._subscribers:
                return
            self._scanner = BleakScanner(detection_callback=self._on_advert)
            try:
                await self._scanner.start()
                log.info("Mopeka scanner resumed (subscribers=%d)",
                         len(self._subscribers))
            except Exception:
                log.exception("Mopeka scanner resume: start failed")
                self._scanner = None

    def _on_advert(self, device, advertisement_data) -> None:
        try:
            mfr_data = (advertisement_data.manufacturer_data or {}).get(
                MOPEKA_MANUFACTURER_ID,
            )
            if not mfr_data:
                return
            mac = (device.address or "").upper()
            sub = self._subscribers.get(mac)
            if sub is None:
                return  # advert from a Mopeka we don't have configured
            parsed = parse_mopeka_advertisement(mfr_data)
            if parsed is None:
                return
            sub._on_payload(parsed)
        except Exception:
            log.exception("mopeka scanner: callback failed for %s",
                          getattr(device, "address", "?"))


_GLOBAL_SCANNER: _SharedMopekaScanner | None = None


def _scanner() -> _SharedMopekaScanner:
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = _SharedMopekaScanner()
    return _GLOBAL_SCANNER


# ---------- transport ----------

class BleMopekaAdvertiseTransport(Transport):
    """Passive BLE transport for a single Mopeka tank sensor.

    Configured with a MAC only, no encryption key. Driver reads via
    `get_latest()`. `request()` is unsupported (passive).
    """

    def __init__(self, id: str, address: str) -> None:
        self.id = id
        self.address = address.upper()
        self._latest: dict[str, Any] | None = None
        self._latest_at: float = 0.0
        self._registered = False

    async def open(self) -> None:
        if self._registered:
            return
        await _scanner().register(self)
        self._registered = True
        log.info("[%s] passive Mopeka listener active for %s",
                 self.id, self.address)

    async def close(self) -> None:
        if not self._registered:
            return
        await _scanner().unregister(self)
        self._registered = False

    async def request(
        self, frame: bytes, expected_response_len: int, timeout: float = 5.0,
    ) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on a passive Mopeka "
            "transport, drivers must override poll() and use get_latest()"
        )

    def _on_payload(self, parsed: dict[str, Any]) -> None:
        self._latest = parsed
        self._latest_at = time.time()

    def get_latest(self) -> dict[str, Any] | None:
        if self._latest is None:
            return None
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return dict(self._latest)

    def last_advertisement_age_s(self) -> float | None:
        """Seconds since the last decoded Mopeka advertisement, or
        None if we've never received one. Used by the driver to
        surface a Silent state on the dashboard without throwing the
        whole `latest` row away."""
        if self._latest_at == 0.0:
            return None
        return max(0.0, time.time() - self._latest_at)


@register_transport("ble_mopeka_advertise")
def _factory(cfg: dict[str, Any]) -> BleMopekaAdvertiseTransport:
    """Build a transport from a config-yaml `transports:` entry of
    `type: ble_mopeka_advertise`. Requires `id` + `address`."""
    tid     = cfg.get("id")
    address = cfg.get("address")
    if not tid or not address:
        raise ValueError("ble_mopeka_advertise requires id + address")
    return BleMopekaAdvertiseTransport(id=tid, address=address)
