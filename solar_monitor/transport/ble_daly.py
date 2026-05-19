"""BLE GATT transport for Daly Smart BMS (#202).

Daly (Dongguan Daly Electronics) is the second-most-common BMS in
budget LFP packs after JBD. Their app is "Smart BMS"; the device
identifies in BLE scans as "DL-…" or "BMS-…" followed by an alpha
suffix.

Protocol summary (from dalybms.com docs + the dalybms open-source
project + bigmonkeyboy/daly-bms-uart reverse engineering):

  * BLE service:  0000FFF0-0000-1000-8000-00805F9B34FB
  * Write char:   0000FFF2-0000-1000-8000-00805F9B34FB
  * Notify char:  0000FFF1-0000-1000-8000-00805F9B34FB

Frame format (host -> BMS):

    A5 <ADDR> <CMD> 08 <8 bytes data, usually zeros> <CHK>

Frame format (BMS -> host, possibly fragmented across notifies):

    A5 <ADDR> <CMD> 08 <8 bytes data> <CHK>

ADDR is 0x40 (host -> BMS) or 0x01 (BMS -> host on UART; on BLE the
BMS replies with whatever ADDR was sent, so we send 0x80 which a
lot of Daly BLE firmwares accept). CHK is the low byte of the sum
of all preceding bytes.

Commands we read:

  * 0x90 — SoC + total V + total I
  * 0x91 — min / max cell V + index
  * 0x92 — min / max temperature + sensor index
  * 0x93 — charge / discharge MOS state + cycle count
  * 0x94 — cells count + temp sensor count + charger/load status
  * 0x95 — per-cell voltages (multi-frame, one cell every 3 bytes)
  * 0x96 — per-temperature-sensor readings

All read frames are 13 bytes. Cell-voltage replies span multiple
13-byte frames; we accumulate them in the parser.

Read-only at v1. Daly does support writes (BMS reset, threshold
configuration) but they're behind the same brick-the-pack risk as
JBD — gated on hardware validation.
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


DALY_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
DALY_NOTIFY_UUID  = "0000fff1-0000-1000-8000-00805f9b34fb"
DALY_WRITE_UUID   = "0000fff2-0000-1000-8000-00805f9b34fb"

FRAME_HEADER = 0xA5
FRAME_LEN = 13     # every Daly frame, request and response, is 13 bytes

# Commands we poll. Order matters: cell voltages (0x95) is variable
# count + the BMS replies with N frames, so it goes last in the
# request burst.
COMMANDS = (0x90, 0x91, 0x92, 0x93, 0x94, 0x96, 0x95)

STALE_AFTER_SECONDS = 60.0


def _checksum(b: bytes) -> int:
    return sum(b) & 0xFF


def build_request(command: int) -> bytes:
    # 0x80 = "BLE bridge" address. Most Daly BLE firmwares accept it;
    # the few that don't accept 0x40 (the UART host address) — those
    # would need a config override we'd add when a real device reports
    # back. v1 picks one and ships.
    body = bytes([FRAME_HEADER, 0x80, command, 0x08,
                  0, 0, 0, 0, 0, 0, 0, 0])
    return body + bytes([_checksum(body)])


REQUESTS = {cmd: build_request(cmd) for cmd in COMMANDS}


def parse_frame(buf: bytes) -> tuple[int, bytes] | None:
    """Validate a 13-byte frame. Returns (command, 8-byte data) or
    None if invalid."""
    if len(buf) != FRAME_LEN:
        return None
    if buf[0] != FRAME_HEADER:
        return None
    if buf[3] != 0x08:
        return None
    if _checksum(buf[:-1]) != buf[-1]:
        return None
    return buf[2], buf[4:12]


class BleDalyTransport(Transport):
    def __init__(self, id: str, address: str,
                 discovery_timeout: float = 20.0) -> None:
        self.id = id
        self.address = address.upper()
        self.discovery_timeout = discovery_timeout
        self._client: BleakClient | None = None
        self._buf = bytearray()
        # Per-command latest data. Cell voltages (0x95) accumulate
        # as a list because the BMS sends multiple 0x95 frames in a
        # row, one per group of 3 cells.
        self._latest: dict[int, bytes] = {}
        self._cells: list[int] = []
        self._latest_at: float = 0.0
        self._poll_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        log.info("[%s] discovering Daly BMS %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout,
        )
        if dev is None:
            raise TransportError(
                f"Daly BMS {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to Daly BMS {self.address}")
        try:
            await self._client.start_notify(DALY_NOTIFY_UUID, self._on_notify)
        except Exception as e:
            raise TransportError(f"start_notify failed: {e}")
        self._stop.clear()
        self._poll_task = asyncio.get_event_loop().create_task(self._poll_loop())
        log.info("[%s] connected; poll loop started", self.id)

    async def close(self) -> None:
        self._stop.set()
        if self._poll_task is not None:
            try:
                await asyncio.wait_for(self._poll_task, timeout=1.5)
            except asyncio.TimeoutError:
                self._poll_task.cancel()
            self._poll_task = None
        if self._client is not None:
            try:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(DALY_NOTIFY_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
            finally:
                self._client = None
                self._buf.clear()

    async def request(self, frame: bytes, expected_response_len: int,
                      timeout: float = 5.0) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on ble_daly — "
            "drivers must override poll() and use get_latest_frame()"
        )

    def get_latest_frame(self, command: int) -> bytes | None:
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest.get(command)

    def get_cell_voltages_mv(self) -> list[int]:
        """Cell-voltage frames arrive as N×13-byte chunks (3 cells
        each, uint16 BE per cell). We accumulate the latest set."""
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return []
        return list(self._cells)

    def last_frame_age_s(self) -> float | None:
        if self._latest_at == 0.0:
            return None
        return time.time() - self._latest_at

    async def _poll_loop(self) -> None:
        assert self._client is not None
        while not self._stop.is_set():
            for cmd in COMMANDS:
                if cmd == 0x95:
                    # Cell voltages: reset the accumulator before
                    # asking so we don't mix old + new readings.
                    self._cells = []
                try:
                    await self._client.write_gatt_char(
                        DALY_WRITE_UUID, REQUESTS[cmd], response=False,
                    )
                except Exception as e:
                    log.info("[%s] write 0x%02X failed: %s", self.id, cmd, e)
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.5)

    def _on_notify(self, _sender, data: bytearray) -> None:
        """Slice out 13-byte frames from the incoming stream and
        update the per-command cache. Daly's BLE bridge tends to
        deliver one frame per notification but some firmware
        coalesces multiple frames into one BLE PDU; handle both."""
        self._buf.extend(data)
        while len(self._buf) >= FRAME_LEN:
            if self._buf[0] != FRAME_HEADER:
                del self._buf[:1]
                continue
            candidate = bytes(self._buf[:FRAME_LEN])
            parsed = parse_frame(candidate)
            if parsed is None:
                del self._buf[:1]
                continue
            cmd, payload = parsed
            self._latest[cmd] = payload
            if cmd == 0x95:
                # frame index in byte 0 of payload; three uint16 BE
                # cell voltages follow in bytes 1..6. Append all
                # non-zero cells; firmware pads with zeros for the
                # last frame if cell_count isn't a multiple of 3.
                for j in range(3):
                    off = 1 + j * 2
                    mv = (payload[off] << 8) | payload[off + 1]
                    if mv == 0:
                        continue
                    self._cells.append(mv)
            self._latest_at = time.time()
            del self._buf[:FRAME_LEN]


@register_transport("ble_daly")
def _factory(cfg: dict) -> BleDalyTransport:
    return BleDalyTransport(
        id=cfg["id"],
        address=cfg["address"],
        discovery_timeout=float(cfg.get("discovery_timeout", 20.0)),
    )
