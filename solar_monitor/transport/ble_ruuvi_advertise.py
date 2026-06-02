"""Passive BLE listener for RuuviTag environmental sensors (#255).

RuuviTag is the open-hardware go-to for serious vanlife / off-grid
monitoring. Where Govee gives you the cheap-and-cheerful temp+humid,
Ruuvi adds pressure (good for weather forecasting on the move),
accelerometer (movement detect), and a much better battery life than
the Govee coin cells. ~£25-35, manufacturer ID 0x0499.

We decode **RAWv2 (format 5)**, the dominant modern Ruuvi payload.
Format 3 (legacy) and 8 (encrypted) are rare enough we don't bother
yet; the dashboard surfaces a "format not supported" error if those
turn up so the user knows to enable RAWv2 in the Ruuvi app.

Read-only, RuuviTags accept GATT writes for config, but the BLE
advertisement is the read path and the only thing the average user
cares about. WattPost doesn't (re)configure Ruuvi units.
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

# Ruuvi Innovations Ltd., assigned BLE manufacturer ID.
RUUVI_MANUFACTURER_ID = 0x0499

STALE_AFTER_SECONDS = 90.0

# RuuviTag CR2477 battery curve. 3.0V fully charged, ~2.0V cutoff.
# Linear interpolation is good enough for "is this thing about to die".
_BATT_FULL_MV = 3000
_BATT_EMPTY_MV = 2000


def parse_ruuvi_advertisement(mfr_data: bytes) -> dict[str, Any] | None:
    """Decode a RuuviTag manufacturer payload → normalised dict, or
    None if the format isn't one we recognise (or bytes are short)."""
    if not mfr_data or len(mfr_data) < 1:
        return None
    fmt = mfr_data[0]
    if fmt != 0x05:
        # Format 3 (legacy) + format 8 (encrypted) exist but we
        # don't support them. Return None so the driver's silent-state
        # logic fires rather than emitting junk.
        return None
    if len(mfr_data) < 24:
        return None

    # Format 5 layout, all big-endian:
    #   0:    format (0x05)
    #   1-2:  temp_int16  × 0.005 °C  (signed)
    #   3-4:  humid_u16   × 0.0025 %
    #   5-6:  pressure_u16 + 50000 Pa
    #   7-12: accel x/y/z int16 (mg)
    #   13-14: power_info, 11 upper bits = battery_mv − 1600,
    #                       5 lower bits = tx_power × 2 + 40
    #   15:   movement_counter
    #   16-17: measurement_sequence
    #   18-23: MAC (echoed; we ignore, appliance row has it already)
    temp_raw = int.from_bytes(mfr_data[1:3], "big", signed=True)
    humid_raw = int.from_bytes(mfr_data[3:5], "big")
    press_raw = int.from_bytes(mfr_data[5:7], "big")
    power_info = int.from_bytes(mfr_data[13:15], "big")

    # Ruuvi uses 0x8000 as the temp "invalid" sentinel.
    temp_c: float | None = None
    if temp_raw != -0x8000:
        temp_c = round(temp_raw * 0.005, 2)
    # Humidity 0xFFFF means invalid; otherwise scale.
    humid: float | None = None
    if humid_raw != 0xFFFF:
        humid = round(humid_raw * 0.0025, 1)
    # Pressure: 0xFFFF means invalid.
    pressure_pa: int | None = None
    if press_raw != 0xFFFF:
        pressure_pa = press_raw + 50000

    # Battery: upper 11 bits of power_info, encoded as mV-1600.
    # The sentinel 0x7FF means invalid.
    battery_mv: int | None = None
    battery_pct: float | None = None
    batt_field = power_info >> 5
    if batt_field != 0x7FF:
        battery_mv = batt_field + 1600
        battery_pct = round(
            max(0.0, min(100.0,
                (battery_mv - _BATT_EMPTY_MV) * 100.0
                / (_BATT_FULL_MV - _BATT_EMPTY_MV))), 1)

    return {
        "format": fmt,
        "temperature_c": temp_c,
        "humidity_pct":  humid,
        "pressure_pa":   pressure_pa,
        "battery_mv":    battery_mv,
        "battery_pct":   battery_pct,
    }


class _SharedRuuviScanner:
    def __init__(self) -> None:
        self._subscribers: dict[str, "BleRuuviAdvertiseTransport"] = {}
        self._scanner: BleakScanner | None = None
        self._lock = asyncio.Lock()

    async def register(self, transport: "BleRuuviAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers[transport.address] = transport
            if self._scanner is None:
                self._scanner = BleakScanner(detection_callback=self._on_advert)
                await self._scanner.start()
                log.info("Ruuvi shared scanner started (%d subscriber(s))",
                         len(self._subscribers))

    async def unregister(self, transport: "BleRuuviAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers.pop(transport.address, None)
            if not self._subscribers and self._scanner is not None:
                try:
                    await self._scanner.stop()
                except Exception:
                    log.exception("Ruuvi scanner stop failed")
                self._scanner = None

    async def pause(self) -> bool:
        async with self._lock:
            if self._scanner is None:
                return False
            try:
                await self._scanner.stop()
            except Exception:
                log.exception("Ruuvi scanner pause: stop failed")
            self._scanner = None
            return True

    async def resume(self) -> None:
        async with self._lock:
            if self._scanner is not None or not self._subscribers:
                return
            self._scanner = BleakScanner(detection_callback=self._on_advert)
            try:
                await self._scanner.start()
            except Exception:
                log.exception("Ruuvi scanner resume: start failed")
                self._scanner = None

    def _on_advert(self, device, advertisement_data) -> None:
        try:
            mfr_data = (advertisement_data.manufacturer_data or {}).get(
                RUUVI_MANUFACTURER_ID,
            )
            if not mfr_data:
                return
            mac = (device.address or "").upper()
            sub = self._subscribers.get(mac)
            if sub is None:
                return
            parsed = parse_ruuvi_advertisement(mfr_data)
            if parsed is None:
                return
            sub._on_payload(parsed)
        except Exception:
            log.exception("ruuvi scanner: callback failed for %s",
                          getattr(device, "address", "?"))


_GLOBAL_SCANNER: _SharedRuuviScanner | None = None


def _scanner() -> _SharedRuuviScanner:
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = _SharedRuuviScanner()
    return _GLOBAL_SCANNER


class BleRuuviAdvertiseTransport(Transport):
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
        log.info("[%s] passive Ruuvi listener active for %s",
                 self.id, self.address)

    async def close(self) -> None:
        if not self._registered:
            return
        await _scanner().unregister(self)
        self._registered = False

    async def request(self, frame, expected_response_len, timeout=5.0):
        raise TransportError(
            f"{self.id}: request() unsupported on passive Ruuvi transport"
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
        if self._latest_at == 0.0:
            return None
        return max(0.0, time.time() - self._latest_at)


@register_transport("ble_ruuvi_advertise")
def _factory(cfg: dict[str, Any]) -> BleRuuviAdvertiseTransport:
    tid     = cfg.get("id")
    address = cfg.get("address")
    if not tid or not address:
        raise ValueError("ble_ruuvi_advertise requires id + address")
    return BleRuuviAdvertiseTransport(id=tid, address=address)
