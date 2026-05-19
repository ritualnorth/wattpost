#!/usr/bin/env python3
"""Verify the FC06 writable-settings path works over the serial_modbus
transport (#116). USB-RS485 customers needed proof that the same
write_setting_register() flow that ships over BT-2 BLE Modbus works
identically over RS-485.

Strategy: openpty() gives us a pseudo-terminal pair. We run
SerialModbusTransport against one side and a tiny Modbus responder
against the other — the loopback exercises the actual pyserial
read/write code without needing a real Renogy device.

Run from the repo root:
    python3 scripts/verify_fc06_serial.py

Exits 0 on success, 1 on failure. Safe to wire into CI later.
"""
from __future__ import annotations

import asyncio
import os
import pty
import sys
import threading

# Make the package importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.modbus import (
    EXPECTED_WRITE_SINGLE_RESPONSE_LEN,
    crc16,
    expected_read_response_len,
    verify_response,
)
from solar_monitor.settings_write import write_setting_register
from solar_monitor.transport.serial_modbus import SerialModbusTransport


SLAVE_ID = 16          # typical Renogy default
REGISTER = 0xE004      # battery-type register (Rover / DCC50S)
NEW_VALUE = 4          # "lithium"


def fake_modbus_slave(fd: int, ready: threading.Event) -> None:
    """Tiny Modbus RTU slave running on the other end of the pty.

    Understands two requests for this test: FC06 (writes the value
    into a tiny in-memory register file and echoes the request) and
    FC03 (returns the latest written value). Anything else gets an
    exception response — same shape a real Renogy would return.

    Runs in a background thread because pty reads block on the
    file descriptor; making this async would need extra plumbing
    that doesn't add coverage."""
    registers: dict[int, int] = {}
    ready.set()
    buf = bytearray()
    while True:
        try:
            chunk = os.read(fd, 256)
        except OSError:
            return
        if not chunk:
            return
        buf.extend(chunk)
        # Drain complete frames. RTU has no length prefix so we
        # parse by function code.
        while True:
            if len(buf) < 8:
                break
            sid, fc = buf[0], buf[1]
            if fc == 6:
                frame_len = 8           # FC06 request is always 8 bytes
            elif fc == 3:
                frame_len = 8           # FC03 request is also 8 bytes
            else:
                # Unknown FC — drop one byte and resync.
                buf.pop(0)
                continue
            if len(buf) < frame_len:
                break
            frame = bytes(buf[:frame_len])
            buf = buf[frame_len:]
            if crc16(frame[:-2]) != frame[-2:]:
                continue  # bad CRC; real slaves stay silent
            if fc == 6:
                reg = (frame[2] << 8) | frame[3]
                val = (frame[4] << 8) | frame[5]
                registers[reg] = val
                os.write(fd, frame)  # FC06 success = echo
            elif fc == 3:
                reg = (frame[2] << 8) | frame[3]
                cnt = (frame[4] << 8) | frame[5]
                payload = bytearray()
                for i in range(cnt):
                    v = registers.get(reg + i, 0)
                    payload.append((v >> 8) & 0xFF)
                    payload.append(v & 0xFF)
                head = bytes([sid, 3, len(payload)]) + bytes(payload)
                os.write(fd, head + crc16(head))


async def main() -> int:
    # Create a pty pair. The "master" side (slave_fd in pty terms,
    # naming is confusing — fd we hand to the fake responder) and a
    # device path the SerialModbusTransport can open via pyserial.
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)

    ready = threading.Event()
    t = threading.Thread(
        target=fake_modbus_slave, args=(master_fd, ready), daemon=True,
    )
    t.start()
    ready.wait(timeout=2)

    transport = SerialModbusTransport(
        id="test", port=slave_path, baudrate=9600,
        inter_frame_gap=0.01,
    )
    await transport.open()

    print(f"[verify] writing reg 0x{REGISTER:04X} = {NEW_VALUE} on slave {SLAVE_ID}")
    result = await write_setting_register(
        transport, SLAVE_ID, REGISTER, NEW_VALUE,
    )
    print(f"[verify] result: {result}")
    await transport.close()
    os.close(master_fd)

    if not result["ok"]:
        print(f"[verify] FAIL: ok={result['ok']} detail={result['detail']}")
        return 1
    if result["confirmed_value"] != NEW_VALUE:
        print(
            f"[verify] FAIL: confirmed_value={result['confirmed_value']!r}, "
            f"expected {NEW_VALUE}"
        )
        return 1

    # Cross-check that the underlying frame builder + verifier round-
    # trip cleanly too — guards against a future refactor breaking
    # the CRC path silently.
    sample_req = bytes([SLAVE_ID, 6, 0xE0, 0x04, 0x00, 0x04]) + crc16(
        bytes([SLAVE_ID, 6, 0xE0, 0x04, 0x00, 0x04])
    )
    assert len(sample_req) == EXPECTED_WRITE_SINGLE_RESPONSE_LEN
    verify_response(sample_req, SLAVE_ID, expected_fc=6)
    print(f"[verify] OK: serial_modbus + FC06 write + FC03 read-back round-trip works")
    print(f"[verify] (expected_read_len for 1 word = {expected_read_response_len(1)})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
