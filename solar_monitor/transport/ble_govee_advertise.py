"""Passive BLE listener for Govee thermometer-hygrometers (#255).

Govee makes the dominant cheap ambient temp/humidity sensors — H5074,
H5075, H5101/H5102, etc. ~£10-15 on Amazon, palm-sized, CR2477 coin
cell, plaintext BLE advertisement every ~2 seconds.

  * Manufacturer ID 0xEC88 ("Shenzhen Govee") on every model we
    support. Different model families pack the temp/humid bytes
    slightly differently; we handle the two dominant formats:

      - **H5074 / H5072** (older with display): status byte 0x00,
        then int16 LE temp ÷100, uint16 LE humid ÷100, uint8 battery.
      - **H5075 / H5101 / H5102 / H5104** (newer): status byte
        0x00 (H5075) or 0x01 (H510x), then a 3-byte BE packed
        value encoding sign + temp + humid, then uint8 battery.

  * Read-only. Govee BLE advertisements don't carry commands; the
    H6 / H7 product lines that DO accept writes go through Govee's
    cloud, which we don't touch.

Per [[project_van_mode]], ambient temp on the dashboard answers
"is the inverter cabinet overheating?" and "how cold is the van
overnight?". Per [[project_target_customer]], Govee is what every
vanlife forum recommends as the cheap start.
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

# Shenzhen Govee Trading Co., Ltd. — assigned BLE manufacturer ID.
GOVEE_MANUFACTURER_ID = 0xEC88

# How long a decoded payload stays "fresh". Govee broadcasts every
# 2-3s on a good battery; 90s of silence reliably means
# out-of-range / battery dead.
STALE_AFTER_SECONDS = 90.0


def parse_govee_advertisement(mfr_data: bytes) -> dict[str, Any] | None:
    """Decode Govee manufacturer payload → {temperature_c, humidity_pct,
    battery_pct, hardware_kind} or None if the bytes don't match a
    Govee format we know.

    Both formats are 6 bytes. The status byte tells us which encoding
    to apply.
    """
    if not mfr_data or len(mfr_data) < 6:
        return None
    status = mfr_data[0]

    if status == 0x00:
        # H5074 (older) ships status=0x00 with a 2-byte signed
        # little-endian temp. H5075 ALSO ships 0x00 but uses the
        # 3-byte packed encoding. Disambiguate on the second byte:
        # H5075's first packed byte is sign|temp, never 0xFF. H5074
        # leaves bytes 3-4 as humidity, so the sniff is: if bytes
        # 3-4 LE looks like a plausible humidity (0..10000 = 0-100%),
        # it's H5074. Otherwise treat as H5075.
        humid_candidate = int.from_bytes(mfr_data[3:5], "little")
        if humid_candidate <= 10000:
            # H5074 / H5072 — explicit fields, no packed math.
            temp_raw = int.from_bytes(mfr_data[1:3], "little", signed=True)
            battery  = mfr_data[5]
            return {
                "hardware_kind": "h5074",
                "temperature_c": round(temp_raw / 100.0, 2),
                "humidity_pct":  round(humid_candidate / 100.0, 1),
                "battery_pct":   min(100, int(battery)),
            }
        # Fall through to H5075 packed decode

    if status in (0x00, 0x01):
        # H5075 / H5101 / H5102 packed encoding. Sign bit at 23, the
        # remaining 23 bits encode temp + humid together:
        #   abs / 10000.0          → °C  (e.g. 224517 → 22.4517)
        #   (abs % 1000) / 10.0    → %RH (e.g. 224517 → 517 → 51.7)
        packed = int.from_bytes(mfr_data[1:4], "big")
        sign = -1 if packed & 0x800000 else 1
        absv = packed & 0x7FFFFF
        temp_c = sign * (absv / 10000.0)
        humid  = (absv % 1000) / 10.0
        battery = mfr_data[4]
        kind = "h5101" if status == 0x01 else "h5075"
        return {
            "hardware_kind": kind,
            "temperature_c": round(temp_c, 2),
            "humidity_pct":  round(humid, 1),
            "battery_pct":   min(100, int(battery)),
        }

    return None


class _SharedGoveeScanner:
    """Singleton scanner that fans Govee adverts to per-MAC transports."""

    def __init__(self) -> None:
        self._subscribers: dict[str, "BleGoveeAdvertiseTransport"] = {}
        self._scanner: BleakScanner | None = None
        self._lock = asyncio.Lock()

    async def register(self, transport: "BleGoveeAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers[transport.address] = transport
            if self._scanner is None:
                self._scanner = BleakScanner(detection_callback=self._on_advert)
                await self._scanner.start()
                log.info("Govee shared scanner started (%d subscriber(s))",
                         len(self._subscribers))

    async def unregister(self, transport: "BleGoveeAdvertiseTransport") -> None:
        async with self._lock:
            self._subscribers.pop(transport.address, None)
            if not self._subscribers and self._scanner is not None:
                try:
                    await self._scanner.stop()
                except Exception:
                    log.exception("Govee scanner stop failed")
                self._scanner = None

    async def pause(self) -> bool:
        async with self._lock:
            if self._scanner is None:
                return False
            try:
                await self._scanner.stop()
            except Exception:
                log.exception("Govee scanner pause: stop failed")
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
                log.exception("Govee scanner resume: start failed")
                self._scanner = None

    def _on_advert(self, device, advertisement_data) -> None:
        try:
            mfr_data = (advertisement_data.manufacturer_data or {}).get(
                GOVEE_MANUFACTURER_ID,
            )
            if not mfr_data:
                return
            mac = (device.address or "").upper()
            sub = self._subscribers.get(mac)
            if sub is None:
                return
            parsed = parse_govee_advertisement(mfr_data)
            if parsed is None:
                return
            sub._on_payload(parsed)
        except Exception:
            log.exception("govee scanner: callback failed for %s",
                          getattr(device, "address", "?"))


_GLOBAL_SCANNER: _SharedGoveeScanner | None = None


def _scanner() -> _SharedGoveeScanner:
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = _SharedGoveeScanner()
    return _GLOBAL_SCANNER


class BleGoveeAdvertiseTransport(Transport):
    """Passive listener for a single Govee thermometer."""

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
        log.info("[%s] passive Govee listener active for %s",
                 self.id, self.address)

    async def close(self) -> None:
        if not self._registered:
            return
        await _scanner().unregister(self)
        self._registered = False

    async def request(self, frame, expected_response_len, timeout=5.0):
        raise TransportError(
            f"{self.id}: request() unsupported on passive Govee transport"
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


@register_transport("ble_govee_advertise")
def _factory(cfg: dict[str, Any]) -> BleGoveeAdvertiseTransport:
    tid     = cfg.get("id")
    address = cfg.get("address")
    if not tid or not address:
        raise ValueError("ble_govee_advertise requires id + address")
    return BleGoveeAdvertiseTransport(id=tid, address=address)
