"""BLE GATT transport for JBD / Overkill Solar BMS family (#201).

JBD (Jiabaida) makes the BMS inside most cheap LFP packs in the
hobby market. Battle Born, LiTime, Power Queen, many Eco-Worthy
SKUs, anything sold rebranded on Amazon under "100Ah LiFePO4
smart battery", almost all of them are JBD inside. Overkill
Solar is the most prominent rebadge in the US market; the
"Overkill Solar app" is the open-source-friendly client for the
same protocol.

Protocol summary (from Overkill's documentation + community
reverse engineering):

  * BLE service:  0000FF00-0000-1000-8000-00805F9B34FB
  * Notify char:  0000FF01-0000-1000-8000-00805F9B34FB
  * Write char:   0000FF02-0000-1000-8000-00805F9B34FB

Request frame (we send):

    DD A5 <CMD> 00 <DLEN> [<DATA…>] <CRC_HI> <CRC_LO> 77

Response frame (BMS sends, possibly split across notifications):

    DD <CMD> <STATUS> <DLEN> [<DATA…>] <CRC_HI> <CRC_LO> 77

CRC is the two's complement of the unsigned sum of bytes from
<CMD> through the end of <DATA>. We validate on receive and skip
malformed frames.

Two commands we care about:

  * 0x03, basic info: voltage, current, residual / nominal Ah,
    cycle count, FET status, balance bits, protection bits, SoC.
  * 0x04, cell voltages: one uint16 (mV) per cell.

The transport polls both per cycle by writing the request frame
and accumulating notifications until a complete CRC-valid frame
lands. Cached by command; the driver reads via get_latest_frame().

request() is unsupported (JBD doesn't fit the Modbus request /
response shape we use on Renogy serial). Drivers override poll()
and read both 0x03 and 0x04 frames directly.

Read-only at v1. JBD does expose write registers (over-volt
thresholds, balance start, etc.) but they're behind a checksum
gate and getting one wrong can brick a customer's pack. Match
the project_no_victron_lab_purchases discipline: ship reads
from docs, gate writes on hardware validation.
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


JBD_SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
JBD_NOTIFY_UUID  = "0000ff01-0000-1000-8000-00805f9b34fb"
JBD_WRITE_UUID   = "0000ff02-0000-1000-8000-00805f9b34fb"

FRAME_START = 0xDD
FRAME_END   = 0x77

CMD_BASIC_INFO = 0x03
CMD_CELL_INFO  = 0x04
CMD_HW_VERSION = 0x05

STALE_AFTER_SECONDS = 60.0


def _checksum(data: bytes) -> bytes:
    """JBD's frame checksum is the two's complement of the unsigned
    sum of all bytes from <CMD> through the end of <DATA>, packed
    big-endian. Bytes outside that range (start, end, the checksum
    itself) aren't part of the sum."""
    s = (-sum(data)) & 0xFFFF
    return bytes([(s >> 8) & 0xFF, s & 0xFF])


def build_request(command: int) -> bytes:
    """A JBD read request is fixed-length: header + CMD + status +
    data-length + checksum + tail. status / data-length are 0 for
    reads."""
    body = bytes([0xA5, command, 0x00])  # 0xA5 = read; data-length = 0
    cks = _checksum(bytes([command, 0x00]))
    return bytes([FRAME_START]) + body + cks + bytes([FRAME_END])


REQ_BASIC_INFO = build_request(CMD_BASIC_INFO)
REQ_CELL_INFO  = build_request(CMD_CELL_INFO)


def parse_frame(buf: bytes) -> tuple[int, bytes] | None:
    """Validate and extract one frame from a buffer. Returns
    (command_byte, payload) on a clean frame, or None if buf does
    not contain a complete + checksum-valid frame.

    Tolerant: a malformed frame returns None rather than raising,
    so the reader loop can keep going on the next chunk."""
    if len(buf) < 7:                # smallest valid frame
        return None
    if buf[0] != FRAME_START or buf[-1] != FRAME_END:
        return None
    cmd = buf[1]
    status = buf[2]
    dlen = buf[3]
    if len(buf) != 4 + dlen + 2 + 1:  # header + payload + cks + tail
        return None
    payload = buf[4:4 + dlen]
    expected = _checksum(bytes([cmd, status]) + bytes([dlen]) + payload)
    # The protocol spec covers CMD+STATUS+DLEN+DATA in the sum on
    # write frames, but on response frames the convention from
    # Overkill's reference implementation is CMD+STATUS+DLEN+DATA.
    # The two forms produce the same number for read responses
    # because STATUS is always 0, so this matches both paths.
    if buf[4 + dlen:4 + dlen + 2] != expected:
        return None
    return cmd, payload


class BleJbdTransport(Transport):
    """BLE GATT transport for one JBD-protocol BMS.

    Maintains a persistent connection + notification subscription.
    Polls the BMS for command 0x03 (basic info) and 0x04 (cell info)
    on a slow loop in the background so get_latest_frame() returns
    fresh data on every driver tick."""

    def __init__(self, id: str, address: str,
                 discovery_timeout: float = 20.0) -> None:
        self.id = id
        self.address = address.upper()
        self.discovery_timeout = discovery_timeout

        self._client: BleakClient | None = None
        self._buf = bytearray()
        self._latest_frames: dict[int, bytes] = {}
        self._latest_at: float = 0.0
        self._poll_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        log.info("[%s] discovering JBD BMS %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout,
        )
        if dev is None:
            raise TransportError(
                f"JBD BMS {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to JBD BMS {self.address}")
        try:
            await self._client.start_notify(JBD_NOTIFY_UUID, self._on_notify)
        except Exception as e:
            raise TransportError(f"start_notify failed: {e}")
        # Background poll: write 0x03 then 0x04 every ~1s. JBD
        # doesn't auto-stream (unlike JK), so we have to keep
        # asking. Cheap on a connected BLE link.
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
                        await self._client.stop_notify(JBD_NOTIFY_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
            finally:
                self._client = None
                self._buf.clear()

    async def request(self, frame: bytes, expected_response_len: int,
                      timeout: float = 5.0) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on ble_jbd, "
            "drivers must override poll() and use get_latest_frame()"
        )

    def get_latest_frame(self, command: int) -> bytes | None:
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest_frames.get(command)

    def last_frame_age_s(self) -> float | None:
        if self._latest_at == 0.0:
            return None
        return time.time() - self._latest_at

    async def _poll_loop(self) -> None:
        """Send 0x03 then 0x04 on a ~1s cadence. Either request
        failing logs but doesn't tear the connection down (BlueZ
        gets flaky under contention and we'd rather reconnect on
        the scheduler's next attempt than churn here)."""
        assert self._client is not None
        while not self._stop.is_set():
            for cmd, req in ((CMD_BASIC_INFO, REQ_BASIC_INFO),
                             (CMD_CELL_INFO,  REQ_CELL_INFO)):
                try:
                    await self._client.write_gatt_char(
                        JBD_WRITE_UUID, req, response=False,
                    )
                except Exception as e:
                    log.info("[%s] write 0x%02X failed: %s", self.id, cmd, e)
                # Spread the two requests out so notifications for
                # the first one finish accumulating before we ask
                # for the second. 200 ms is plenty on consumer JBD.
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.5)

    def _on_notify(self, _sender, data: bytearray) -> None:
        """Append the chunk to the accumulator and try to parse a
        frame. JBD chunks come in MTU-sized pieces (~20 bytes
        each) so a basic-info frame typically arrives in two
        notifications and cell-info in two or three."""
        self._buf.extend(data)
        # Search for a complete frame from each FRAME_START until
        # we find one or run out of buffer. Cap loop iterations.
        for _ in range(8):
            start = self._buf.find(bytes([FRAME_START]))
            if start < 0:
                self._buf.clear()
                return
            if start > 0:
                # Drop pre-amble garbage.
                del self._buf[:start]
            end = self._buf.find(bytes([FRAME_END]))
            if end < 0:
                return  # wait for more
            candidate = bytes(self._buf[:end + 1])
            parsed = parse_frame(candidate)
            if parsed is None:
                # Bad frame, advance past this 0xDD and keep
                # scanning. Don't drop the whole buffer; a real
                # frame might start within it.
                del self._buf[:1]
                continue
            cmd, payload = parsed
            self._latest_frames[cmd] = payload
            self._latest_at = time.time()
            del self._buf[:end + 1]


@register_transport("ble_jbd")
def _factory(cfg: dict) -> BleJbdTransport:
    return BleJbdTransport(
        id=cfg["id"],
        address=cfg["address"],
        discovery_timeout=float(cfg.get("discovery_timeout", 20.0)),
    )
