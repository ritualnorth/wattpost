"""USB-HID transport for Voltronic-family inverters.

Voltronic / Axpert / MPP Solar / EG4 hybrids expose a Cypress
USB-HID interface (default VID:PID 0665:5161) that speaks the same
ASCII protocol as the RS-232 port, every request is an ASCII
command + XMODEM CRC + 0x0D, and every response comes back the
same shape starting with '('.

The HID layer chunks both directions into 8-byte reports padded
with 0x00; we reassemble the response until we see 0x0D.

Read-only. We never send a write command (no PSAVE/POP/PCP/PBT/etc.)
on this transport. The protocol does expose writes but every model's
acceptable parameter ranges are firmware-version-specific and one
bad value bricks the inverter until manual reset, same risk model
as Victron writes, same "no" answer.

Contract:

  * `open()` opens the HID device.
  * `query(cmd, timeout)` sends an ASCII command, returns the
    decoded payload (the bytes between '(' and the CRC).
  * `request()` raises, Voltronic doesn't speak Modbus.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..voltronic_crc import frame_command, verify_and_strip
from .base import Transport, TransportError, TransportTimeout
from .registry import register_transport

try:
    import hid  # python `hid` package wraps hidapi
except ImportError:  # pragma: no cover - optional dep
    hid = None  # type: ignore[assignment]


log = logging.getLogger(__name__)


# Cypress HID-to-UART chip ID, the overwhelming majority of
# Voltronic-derived inverters ship with this combo. Some EG4
# variants ship 0001:0000; expose as config so installers can
# override without a firmware patch.
DEFAULT_VID = 0x0665
DEFAULT_PID = 0x5161

REPORT_SIZE = 8     # Voltronic HID reports are always 8 bytes
MAX_RESPONSE = 256  # longest documented response (QPIGS2) fits comfortably


class UsbHidVoltronicTransport(Transport):
    """Request/response over USB-HID for Voltronic-family inverters.

    Not a Modbus transport, drivers using this must override poll()
    and call query() directly. The Voltronic family is the only HID
    transport in the codebase today; everything else is Modbus over
    BLE/serial.
    """

    def __init__(
        self,
        id: str,
        vid: int = DEFAULT_VID,
        pid: int = DEFAULT_PID,
        serial_number: Optional[str] = None,
    ) -> None:
        self.id = id
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self._dev = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if hid is None:
            raise TransportError(
                f"{self.id}: hidapi not installed, pip install hid"
            )
        if self._dev is not None:
            return
        log.info(
            "[%s] opening USB-HID Voltronic %04x:%04x", self.id, self.vid, self.pid,
        )
        loop = asyncio.get_event_loop()
        dev = hid.device()
        try:
            await loop.run_in_executor(
                None,
                lambda: dev.open(self.vid, self.pid, self.serial_number),
            )
        except Exception as e:
            raise TransportError(
                f"{self.id}: could not open HID {self.vid:04x}:{self.pid:04x}: {e}"
            ) from e
        dev.set_nonblocking(True)
        self._dev = dev

    async def close(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.close()
        except Exception:
            pass
        self._dev = None

    async def request(
        self, frame: bytes, expected_response_len: int, timeout: float = 5.0,
    ) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on a Voltronic transport, "
            "drivers must override poll() and call query()"
        )

    async def query(self, cmd: str, timeout: float = 5.0) -> bytes:
        """Send a Voltronic ASCII command and return the response
        payload (the bytes between '(' and the CRC). Raises
        TransportTimeout when the inverter doesn't reply within
        `timeout` seconds, ValueError on a CRC or framing failure."""
        if self._dev is None:
            raise TransportError(f"{self.id}: transport not open")
        async with self._lock:
            return await self._query_locked(cmd, timeout)

    async def _query_locked(self, cmd: str, timeout: float) -> bytes:
        loop = asyncio.get_event_loop()
        dev = self._dev
        assert dev is not None

        wire = frame_command(cmd)
        # HID write protocol: report id byte 0x00 then up to REPORT_SIZE
        # data bytes, padded with 0x00. Pad the final chunk so every
        # write is exactly REPORT_SIZE bytes long, every Voltronic
        # firmware I've seen complains about short reports.
        for i in range(0, len(wire), REPORT_SIZE):
            chunk = wire[i:i + REPORT_SIZE]
            if len(chunk) < REPORT_SIZE:
                chunk = chunk + b"\x00" * (REPORT_SIZE - len(chunk))
            report = b"\x00" + chunk
            await loop.run_in_executor(None, dev.write, report)

        # Read until we see the trailing 0x0D or hit the timeout. The
        # device pushes 8-byte reports as fast as it can fill them;
        # gaps are normal between reports so we poll with a short
        # sleep rather than busy-wait.
        buf = bytearray()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            chunk = await loop.run_in_executor(None, dev.read, REPORT_SIZE)
            if chunk:
                buf.extend(chunk)
                if 0x0D in buf:
                    break
                if len(buf) > MAX_RESPONSE:
                    raise ValueError(
                        f"{self.id}: response exceeded {MAX_RESPONSE} bytes without CR"
                    )
            else:
                await asyncio.sleep(0.02)
        else:
            raise TransportTimeout(
                f"{self.id}: no response to {cmd!r} within {timeout:.1f}s"
            )

        # Trim at the first 0x0D (anything after is from the next
        # response, unlikely with the lock held, but cheap to guard).
        end = buf.index(0x0D)
        frame = bytes(buf[:end + 1])
        # Drop the leading '(' framing byte before CRC verification,
        # voltronic_crc validates the payload only.
        if not frame.startswith(b"("):
            raise ValueError(
                f"{self.id}: response to {cmd!r} did not start with '(', got {frame[:8]!r}"
            )
        # Strip the trailing 0x00 padding the firmware sometimes adds
        # inside the last 8-byte HID report before the CR.
        clean = frame.lstrip(b"(")
        clean = clean.replace(b"\x00", b"")
        return verify_and_strip(clean)


@register_transport("usbhid_voltronic")
def _factory(cfg: dict) -> UsbHidVoltronicTransport:
    return UsbHidVoltronicTransport(
        id=cfg["id"],
        vid=int(cfg.get("vid", DEFAULT_VID)),
        pid=int(cfg.get("pid", DEFAULT_PID)),
        serial_number=cfg.get("serial_number"),
    )
