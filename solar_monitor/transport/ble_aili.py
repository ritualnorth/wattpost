"""BLE GATT transport for AiLi smart shunts (#204).

  * BLE service:  0000FFE0-0000-1000-8000-00805F9B34FB
  * Notify char:  0000FFE1-0000-1000-8000-00805F9B34FB

Shunt streams a 20-byte status frame ~1Hz on notify, no command
needed. Connect + subscribe + read.

    byte  0      header 0xFF
    byte  1      header 0x55
    bytes 2-3    voltage         mV (uint16)
    bytes 4-6    current         24-bit signed 0.001 A
                                 (top bit of byte 4 = sign)
    bytes 7-10   remaining_mAh   uint32
    byte  11     soc_pct         0-100
    byte  12     temperature_c   signed int8 (some rebrands offset 50)
    bytes 13-14  time_to_go      minutes uint16, 0xFFFF = unknown
    bytes 15-18  cumulative_mAh  uint32
    byte  19     checksum        sum-mod-256 (some rebrands XOR)

Read-only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak import BleakClient, BleakScanner

from .base import Transport, TransportError
from .registry import register_transport


log = logging.getLogger(__name__)


AILI_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
AILI_NOTIFY_UUID  = "0000ffe1-0000-1000-8000-00805f9b34fb"

FRAME_LEN = 20
HEADER = bytes([0xFF, 0x55])

STALE_AFTER_SECONDS = 60.0


def _verify_frame(buf: bytes) -> bool:
    if len(buf) != FRAME_LEN or buf[:2] != HEADER:
        return False
    # Sum-of-bytes-mod-256 OR XOR, both are seen in the wild.
    body = buf[:-1]
    if (sum(body) & 0xFF) == buf[-1]:
        return True
    xor = 0
    for b in body:
        xor ^= b
    return xor == buf[-1]


def _i24(b: bytes, off: int) -> int:
    """Top bit of the first byte is sign; remaining 23 bits magnitude."""
    raw = (b[off] << 16) | (b[off + 1] << 8) | b[off + 2]
    if raw & 0x800000:
        return -(raw & 0x7FFFFF)
    return raw


def _u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _u32(b: bytes, off: int) -> int:
    return (b[off] << 24) | (b[off + 1] << 16) | (b[off + 2] << 8) | b[off + 3]


def _i8(b: bytes, off: int) -> int:
    v = b[off]
    return v - 256 if v & 0x80 else v


class BleAiliTransport(Transport):
    def __init__(self, id: str, address: str,
                 discovery_timeout: float = 20.0) -> None:
        self.id = id
        self.address = address.upper()
        self.discovery_timeout = discovery_timeout
        self._client: BleakClient | None = None
        self._buf = bytearray()
        self._latest: dict[str, Any] | None = None
        self._latest_at: float = 0.0

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        log.info("[%s] discovering AiLi shunt %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout,
        )
        if dev is None:
            raise TransportError(
                f"AiLi shunt {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to AiLi shunt {self.address}")
        try:
            await self._client.start_notify(AILI_NOTIFY_UUID, self._on_notify)
        except Exception as e:
            raise TransportError(f"start_notify failed: {e}")
        log.info("[%s] connected; listening for notifications", self.id)

    async def close(self) -> None:
        if self._client is not None:
            try:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(AILI_NOTIFY_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
            finally:
                self._client = None
                self._buf.clear()

    async def request(self, frame: bytes, expected_response_len: int,
                      timeout: float = 5.0) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on ble_aili, "
            "drivers must override poll() and use get_latest()"
        )

    def get_latest(self) -> dict[str, Any] | None:
        if self._latest is None:
            return None
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest

    def last_frame_age_s(self) -> float | None:
        if self._latest_at == 0.0:
            return None
        return time.time() - self._latest_at

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._buf.extend(data)
        # Slice 20-byte frames out of the accumulator. Some AiLi
        # rebrands chunk one frame per notify; others send half-
        # frames. Either way we walk until we find HEADER + 18
        # bytes + checksum that validates.
        while len(self._buf) >= FRAME_LEN:
            i = self._buf.find(HEADER)
            if i < 0:
                self._buf.clear()
                return
            if i > 0:
                del self._buf[:i]
            if len(self._buf) < FRAME_LEN:
                return
            candidate = bytes(self._buf[:FRAME_LEN])
            if not _verify_frame(candidate):
                del self._buf[:1]
                continue
            parsed = _parse_frame(candidate)
            self._latest = parsed
            self._latest_at = time.time()
            del self._buf[:FRAME_LEN]


def _parse_frame(buf: bytes) -> dict[str, Any]:
    """Decode a validated 20-byte AiLi frame. Returns the raw field
    surface; unit conversion + dashboard naming happens in the
    driver."""
    voltage_v = _u16(buf, 2) / 1000.0
    current_a = _i24(buf, 4) / 1000.0
    remaining_ah = _u32(buf, 7) / 1000.0
    soc_pct = buf[11]
    temp_c = _i8(buf, 12)
    ttg_raw = _u16(buf, 13)
    cumulative_ah = _u32(buf, 15) / 1000.0
    return {
        "voltage_v":      voltage_v,
        "current_a":      current_a,
        "remaining_ah":   remaining_ah,
        "soc_pct":        soc_pct,
        "temperature_c":  temp_c,
        "time_to_go_minutes": ttg_raw if ttg_raw != 0xFFFF else None,
        "cumulative_ah":  cumulative_ah,
    }


@register_transport("ble_aili")
def _factory(cfg: dict) -> BleAiliTransport:
    return BleAiliTransport(
        id=cfg["id"],
        address=cfg["address"],
        discovery_timeout=float(cfg.get("discovery_timeout", 20.0)),
    )
