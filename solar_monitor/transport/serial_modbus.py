"""RS-485 Modbus RTU transport via pyserial.

For users who plug a USB-to-RS-485 adapter into the Pi and wire it to the
Renogy Hub / charge controller / inverter directly. Same Modbus frames as
the BLE transport, drivers don't know the difference.

Read path is exercised in production by every customer running USB-RS485.
Write path (FC06 via settings_write.write_setting_register) is verified
against a loopback Modbus slave in scripts/verify_fc06_serial.py (#116);
re-run that script after any change to this module or settings_write.py
to confirm the round-trip still works.
"""
from __future__ import annotations

import asyncio
import logging

import serial as pyserial

from .base import Transport, TransportError, TransportTimeout
from .registry import register_transport

log = logging.getLogger(__name__)


class SerialModbusTransport(Transport):
    def __init__(
        self,
        id: str,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        inter_frame_gap: float = 0.05,
    ) -> None:
        self.id = id
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.inter_frame_gap = inter_frame_gap

        self._ser: pyserial.Serial | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._ser is not None and self._ser.is_open:
            return
        log.info("[%s] opening %s @ %d", self.id, self.port, self.baudrate)
        # pyserial is sync; the only sane way to use it is via run_in_executor.
        # For now we open synchronously since it's a one-shot.
        self._ser = pyserial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=0.0,
        )

    async def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    async def request(
        self,
        frame: bytes,
        expected_response_len: int,
        timeout: float = 5.0,
    ) -> bytes:
        if self._ser is None or not self._ser.is_open:
            raise TransportError(f"transport {self.id} is not open")

        loop = asyncio.get_event_loop()
        async with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            self._ser.flush()

            # Read with a deadline, accumulating bytes until expected_response_len.
            buf = bytearray()
            deadline = loop.time() + timeout
            while len(buf) < expected_response_len:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TransportTimeout(
                        f"got {len(buf)}/{expected_response_len} bytes in {timeout}s "
                        f"on transport {self.id}"
                    )
                # Non-blocking-ish: poll briefly, yield to loop.
                chunk = await loop.run_in_executor(
                    None, self._ser.read, expected_response_len - len(buf)
                )
                if chunk:
                    buf.extend(chunk)
                else:
                    await asyncio.sleep(0.005)

            await asyncio.sleep(self.inter_frame_gap)  # honor Modbus inter-frame gap
            return bytes(buf[:expected_response_len])


@register_transport("serial_modbus")
def _factory(cfg: dict) -> SerialModbusTransport:
    return SerialModbusTransport(
        id=cfg["id"],
        port=cfg["port"],
        baudrate=cfg.get("baudrate", 9600),
        bytesize=cfg.get("bytesize", 8),
        parity=cfg.get("parity", "N"),
        stopbits=cfg.get("stopbits", 1),
        inter_frame_gap=cfg.get("inter_frame_gap", 0.05),
    )
