"""BLE GATT transport for JK BMS battery management systems.

JK BMS (JiKong) uses a proprietary BLE GATT protocol — not Modbus,
not advertisement-only like Victron's Instant Readout. The BMS
exposes a service at UUID 0xFFE0 with a single read/write/notify
characteristic at 0xFFE1; you connect, subscribe to notifications,
send a "request" frame, and the BMS streams back its current state
in a series of MTU-sized notifications (~20 bytes each) that
accumulate into a ~300-byte frame.

Once requested, the BMS continues streaming cell-info frames every
~1 second without further prompting. We send the command on connect
and keep the most recently completed frame cached for the driver
to read.

Protocol reference: syssi/esphome-jk-bms (the canonical OSS
implementation, validated against every JK BMS firmware in the
wild).
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

# JK BMS GATT service + characteristic UUIDs. Same on every JK
# protocol version (JK04 / JK02-24S / JK02-32S).
JK_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
JK_CHAR_UUID    = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Magic preamble that starts every JK frame. Used to find frame
# boundaries when the accumulator buffer overflows or syncs.
JK_FRAME_HEADER = bytes([0x55, 0xAA, 0xEB, 0x90])
# Reverse-byte variant — some JK firmware notifications come in
# with the header order flipped. Belt-and-braces detection.
JK_FRAME_HEADER_ALT = bytes([0xAA, 0x55, 0x90, 0xEB])

# Frame types we care about (set on byte 4 of the frame).
FRAME_TYPE_DEVICE_INFO = 0x03
FRAME_TYPE_CELL_INFO   = 0x02
FRAME_TYPE_SETTINGS    = 0x01

# Length of a complete JK02_32S cell-info frame. 32S has 32-cell
# voltage array (64 bytes) + 32-cell resistance array (64 bytes)
# + a long trailer (alarms, temps, SoC, cycle counts, etc.). The
# total comes out around 300 bytes. We use this as the "buffer is
# big enough to parse" threshold — too short means waiting for
# more chunks; too long means the previous frame went uncompleted
# and we should resync on the next preamble.
COMPLETE_FRAME_MIN = 200    # JK02_24S minimum
COMPLETE_FRAME_MAX = 320    # JK02_32S maximum

STALE_AFTER_SECONDS = 60.0

# Command frames the BMS understands. 20 bytes each. We only need
# COMMAND_CELL_INFO on connect — the BMS auto-streams after that.
def _build_command(register: int) -> bytes:
    """Build a 20-byte JK command frame. The single non-zero byte
    after the header is the register address that selects what
    the BMS should stream back."""
    frame = bytearray(20)
    frame[0] = 0xAA   # JK's write-side header (different order
    frame[1] = 0x55   # from the read-side notification header —
    frame[2] = 0x90   # syssi's reference flips them).
    frame[3] = 0xEB
    frame[4] = register
    frame[5] = 0x00
    # bytes 6..18 = zeros (no value payload for read commands)
    # byte 19 = CRC: 8-bit additive sum of bytes 0..18
    crc = sum(frame[:19]) & 0xFF
    frame[19] = crc
    return bytes(frame)


COMMAND_REQ_CELL_INFO   = _build_command(0x96)
COMMAND_REQ_DEVICE_INFO = _build_command(0x97)


class BleJkBmsTransport(Transport):
    """BLE GATT transport for a JK BMS.

    Configured with a MAC address (find via the wizard or the
    sticker on the BMS). Maintains a persistent GATT connection +
    notification subscription; the driver reads the latest cached
    frame via `get_latest_frame()`.

    `request()` is unsupported — JK is a push protocol once you've
    asked it to start streaming. The driver overrides poll() and
    reads from the cache directly.
    """

    def __init__(self, id: str, address: str,
                 discovery_timeout: float = 20.0) -> None:
        self.id = id
        self.address = address.upper()
        self.discovery_timeout = discovery_timeout

        self._client: BleakClient | None = None
        # Accumulator for incoming notifications. Cleared when a
        # full frame is decoded; carried over otherwise.
        self._buf = bytearray()
        # Most recently completed cell-info frame, keyed by frame
        # type so the driver can pull whichever one it needs.
        self._latest_frames: dict[int, bytes] = {}
        self._latest_at: float = 0.0
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        log.info("[%s] discovering JK BMS %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout,
        )
        if dev is None:
            raise TransportError(
                f"JK BMS {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to JK BMS {self.address}")
        try:
            await self._client.start_notify(JK_CHAR_UUID, self._on_notify)
        except Exception as e:
            raise TransportError(f"start_notify failed: {e}")
        # Kick off auto-streaming. The BMS responds with a device-
        # info frame first (so we know what protocol version it
        # speaks), then begins streaming cell-info every ~1 s.
        await self._client.write_gatt_char(
            JK_CHAR_UUID, COMMAND_REQ_DEVICE_INFO, response=False,
        )
        await asyncio.sleep(0.3)
        await self._client.write_gatt_char(
            JK_CHAR_UUID, COMMAND_REQ_CELL_INFO, response=False,
        )
        log.info("[%s] connected; auto-stream requested", self.id)

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            if self._client.is_connected:
                try:
                    await self._client.stop_notify(JK_CHAR_UUID)
                except Exception:
                    pass
                await self._client.disconnect()
        finally:
            self._client = None
            self._buf.clear()

    async def request(self, frame: bytes, expected_response_len: int,
                      timeout: float = 5.0) -> bytes:
        # JK is a push protocol once told to stream. Drivers must
        # override poll() and use get_latest_frame() — calling
        # request() is a configuration mistake.
        raise TransportError(
            f"{self.id}: request() is unsupported on ble_jkbms — "
            "drivers must override poll() and use get_latest_frame()"
        )

    # ---------- JK-specific surface ----------

    def get_latest_frame(self, frame_type: int = FRAME_TYPE_CELL_INFO) -> bytes | None:
        """Return the latest fully-received frame of the given type,
        or None if no fresh frame is cached."""
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest_frames.get(frame_type)

    def _on_notify(self, _char, data: bytearray) -> None:
        """Accumulate BLE notification chunks into complete frames.

        JK frames are too large for a single BLE MTU, so 15+
        notifications make up one frame. The header (4 magic bytes)
        marks the start. When the buffer has reached at least the
        minimum complete-frame length and the next batch starts a
        new frame, we treat the previous one as complete + parse.
        Belt-and-braces: also accept the alt header byte order
        some JK firmwares emit.
        """
        if not data:
            return
        self._buf.extend(data)

        # Find where a real frame starts. JK firmware can pre-pend
        # junk on first notify after subscribe, so scan rather than
        # trusting buf[0:4].
        start = -1
        for header in (JK_FRAME_HEADER, JK_FRAME_HEADER_ALT):
            idx = self._buf.find(bytes(header))
            if idx >= 0 and (start < 0 or idx < start):
                start = idx
        if start < 0:
            # No header in view yet — discard accumulated junk past
            # a soft cap so we don't grow indefinitely.
            if len(self._buf) > 1024:
                self._buf.clear()
            return
        if start > 0:
            del self._buf[:start]

        # Is the next-frame header visible after enough bytes? If
        # so, the first frame is complete — split + parse.
        while len(self._buf) >= COMPLETE_FRAME_MIN:
            next_idx = -1
            for header in (JK_FRAME_HEADER, JK_FRAME_HEADER_ALT):
                idx = self._buf.find(bytes(header), 4)
                if idx >= 0 and (next_idx < 0 or idx < next_idx):
                    next_idx = idx
            if next_idx < 0:
                # No next-frame header yet. If buffer's overgrown
                # the max valid frame length, the BMS sent garbage
                # — resync.
                if len(self._buf) > COMPLETE_FRAME_MAX * 2:
                    log.warning("[%s] buffer overrun without next "
                                "header — resyncing", self.id)
                    self._buf.clear()
                return
            # Frame is the slice [0:next_idx]. Type byte at offset 4.
            frame = bytes(self._buf[:next_idx])
            del self._buf[:next_idx]
            if len(frame) < 6:
                continue
            ftype = frame[4]
            self._latest_frames[ftype] = frame
            self._latest_at = time.time()
            log.debug("[%s] cached frame type=0x%02X len=%d",
                      self.id, ftype, len(frame))


@register_transport("ble_jkbms")
def _factory(cfg: dict) -> BleJkBmsTransport:
    """Build a BleJkBmsTransport from a YAML config dict.

    Expected fields:
      id: stable id (e.g. "jkbms_main")
      type: "ble_jkbms"
      address: BT MAC (e.g. CC:CC:CC:CC:CC:CC)
    """
    return BleJkBmsTransport(
        id=cfg["id"],
        address=cfg["address"],
        discovery_timeout=cfg.get("discovery_timeout", 20.0),
    )
