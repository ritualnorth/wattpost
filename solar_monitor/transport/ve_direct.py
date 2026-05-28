"""Victron VE.Direct text-protocol transport (#197).

Serial, 19200 8N1. Device emits text frames ~1Hz as a series of
<CR><LF><label><TAB><value> lines terminated by a Checksum line
(unsigned sum of all bytes mod 256 = 0). HEX protocol lines
(start with `:`) are skipped.

Read-only.

  * `open()` starts a background reader.
  * `get_latest()` returns the most recent frame as {label: str,
    plus "_pid_int": int when PID is present}; per-device drivers
    convert from string.
  * `request()` raises, VE.Direct is push-only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import serial as pyserial

from .base import Transport, TransportError
from .registry import register_transport


log = logging.getLogger(__name__)


# Stale-after: VE.Direct emits roughly once a second; 60 s of silence
# means the cable is unplugged, the device is off, or something is
# very wrong. Same threshold the BLE Victron transport uses for the
# same reason, keeps the dashboard's "Silent" handling consistent.
STALE_AFTER_SECONDS = 60.0


def _verify_checksum(raw: bytes) -> bool:
    """VE.Direct frames are valid when the unsigned byte sum mod 256
    is zero. The Checksum field's value is chosen by the device to
    make this hold. Returns True iff `raw` is a complete, valid frame
    (including the trailing Checksum line's value byte).
    """
    return (sum(raw) & 0xFF) == 0


def _parse_frame(raw: bytes) -> dict[str, str]:
    """Decode the body of a valid frame into a dict. The Checksum
    line is dropped from the result. Values are kept as strings;
    callers do the unit conversion that makes sense for their field.

    Frame lines look like `\r\n<label>\t<value>` except the Checksum
    line whose value byte is binary, not text, we strip it before
    text-parsing the rest.
    """
    # Drop the trailing two bytes "Checksum\t<X>", the X byte is the
    # one we just used to validate. The "Checksum" label leads-in is
    # variable length depending on whether there was a CR/LF before it.
    text = raw.decode("latin-1", errors="replace")
    out: dict[str, str] = {}
    for line in text.split("\r\n"):
        if not line:
            continue
        if line.startswith("Checksum"):
            continue
        if "\t" not in line:
            continue
        label, _, value = line.partition("\t")
        out[label.strip()] = value.strip()
    if "PID" in out:
        try:
            out["_pid_int"] = int(out["PID"], 0)
        except ValueError:
            pass
    return out


class VeDirectTransport(Transport):
    """Reads VE.Direct text frames from a serial port. One transport
    per cable / device.

    Background reader task is started by `open()` and torn down by
    `close()`. The most recent valid frame is cached; drivers read
    it via `get_latest()` exactly like the BLE-advertise transport.
    """

    def __init__(
        self, id: str, port: str, baudrate: int = 19200,
    ) -> None:
        self.id = id
        self.port = port
        self.baudrate = baudrate

        self._ser: pyserial.Serial | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._latest: dict[str, str] | None = None
        self._latest_at: float = 0.0

    async def open(self) -> None:
        if self._ser is not None:
            return
        log.info("[%s] opening VE.Direct on %s @ %d", self.id, self.port, self.baudrate)
        # 100 ms read timeout keeps the reader loop responsive to
        # close() without spinning the CPU. The real frame arrival
        # cadence is ~1 s so 100 ms is plenty fine-grained.
        self._ser = pyserial.Serial(
            port=self.port, baudrate=self.baudrate,
            bytesize=8, parity="N", stopbits=1, timeout=0.1,
        )
        self._stop.clear()
        self._task = asyncio.get_event_loop().create_task(self._reader_loop())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    async def request(
        self, frame: bytes, expected_response_len: int, timeout: float = 5.0,
    ) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on a VE.Direct transport, "
            "drivers must override poll() and use get_latest()"
        )

    def get_latest(self) -> dict[str, str] | None:
        if self._latest is None:
            return None
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return self._latest

    def last_frame_age_s(self) -> float | None:
        if self._latest_at == 0.0:
            return None
        return time.time() - self._latest_at

    async def _reader_loop(self) -> None:
        """Continuously read frames from the serial port. Each frame
        ends at the byte immediately after the literal `Checksum\t`
        marker (one binary byte). We accumulate bytes, look for the
        marker, then capture marker+1 and verify the running sum.

        Robust against partial reads, garbage on resync, and HEX
        protocol lines (which start with `:` and end with `\n`). HEX
        lines aren't part of the text-frame checksum so we skip
        their bytes when accumulating.
        """
        loop = asyncio.get_event_loop()
        ser = self._ser
        assert ser is not None
        buf = bytearray()
        marker = b"Checksum\t"
        while not self._stop.is_set():
            # Run the blocking pyserial.read() in the executor so we
            # don't block the event loop. ser.read(N) returns up to
            # N bytes within the read-timeout window.
            try:
                chunk = await loop.run_in_executor(None, ser.read, 256)
            except Exception as e:
                log.warning("[%s] VE.Direct read failed: %s; retrying", self.id, e)
                await asyncio.sleep(0.5)
                continue
            if not chunk:
                continue
            buf.extend(chunk)
            # Strip out any HEX-protocol lines so they don't poison
            # the checksum accumulator. HEX lines start with `:` and
            # end at the next LF. We do this in-place on the buffer.
            buf = _strip_hex_lines(buf)
            # Look for one or more complete frames. The checksum byte
            # is the single byte that follows the marker.
            while True:
                idx = buf.find(marker)
                if idx < 0:
                    break
                end = idx + len(marker) + 1  # marker + 1 byte checksum
                if end > len(buf):
                    # Checksum byte not yet received; wait for more.
                    break
                frame = bytes(buf[:end])
                del buf[:end]
                if not _verify_checksum(frame):
                    # Bad frame, keep scanning forward. Discard
                    # nothing extra; the next iteration's find()
                    # picks up the next Checksum marker.
                    log.debug("[%s] dropped frame with bad checksum", self.id)
                    continue
                try:
                    parsed = _parse_frame(frame)
                except Exception:
                    log.exception("[%s] frame parse crashed", self.id)
                    continue
                if parsed:
                    self._latest = parsed
                    self._latest_at = time.time()


def _strip_hex_lines(buf: bytearray) -> bytearray:
    """Remove `:`-prefixed HEX-protocol lines from a serial buffer.
    HEX lines aren't part of any text frame's checksum so leaving
    them in would corrupt the byte-sum calculation. Operates on a
    bytearray and returns a fresh bytearray; callers reassign."""
    out = bytearray()
    i = 0
    n = len(buf)
    while i < n:
        # A HEX line starts with `:` at the line head, the byte just
        # before it is \n or \r, or it's the start of the buffer.
        at_line_start = (i == 0) or buf[i - 1] in (0x0A, 0x0D)
        if at_line_start and buf[i] == 0x3A:  # ':'
            # Skip until next LF.
            j = buf.find(b"\n", i)
            if j < 0:
                # Incomplete HEX line, keep waiting for more data.
                # Truncate the buffer at i so the next read continues
                # the HEX line collection.
                break
            i = j + 1
            continue
        out.append(buf[i])
        i += 1
    return out


@register_transport("ve_direct")
def _factory(cfg: dict) -> VeDirectTransport:
    return VeDirectTransport(
        id=cfg["id"],
        port=cfg["port"],
        baudrate=cfg.get("baudrate", 19200),
    )
