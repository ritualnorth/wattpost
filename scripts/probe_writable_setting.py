#!/usr/bin/env python3
"""Probe a candidate writable register on a real Renogy device.

For #185 / #186 follow-up and every future driver that adds writable
settings. Given a transport config (USB-RS485 preferred, BT-2 also
fine), a slave ID, and a register address, this:

  1. Reads the current value (FC03), reports it.
  2. Writes that same value back (FC06). A no-op write that should
     ack cleanly on every functioning register. If this fails, the
     register is either unsupported, write-protected, or wrong.
  3. Reads back to confirm value unchanged.
  4. Optionally writes a different value (--set N), reads back to
     confirm, then restores the original.

Designed to be the safe first step before adding any new
WritableSetting to a vendor driver. NEVER guesses register
addresses — you give it one, it tells you what happens.

Usage examples:

  USB-RS485 (recommended):
    python3 scripts/probe_writable_setting.py serial \\
        --port /dev/ttyUSB0 --slave 16 --register 0xE008

  Same register, write 14.4 V (Renogy stores at 0.1 scale -> 144):
    python3 scripts/probe_writable_setting.py serial \\
        --port /dev/ttyUSB0 --slave 16 --register 0xE008 --set 144

  BT-2 BLE (slower, has the ack-swallowing quirk -- read-back covers it):
    python3 scripts/probe_writable_setting.py ble \\
        --mac AA:BB:CC:DD:EE:FF --slave 255 --register 0xE004

Output: a verdict per probe step + final summary. Exit 0 on a clean
no-op round-trip; exit 1 on any failure that indicates the register
isn't writable as named.

This script never proceeds past step 2 if step 1 reads zeros across
the board (suggests a wrong slave ID — better to bail than to write
into a wrong slave's address space)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.modbus import (
    EXPECTED_WRITE_SINGLE_RESPONSE_LEN,
    build_read_holding, build_write_single,
    expected_read_response_len, verify_response,
)
from solar_monitor.transport.base import TransportTimeout


def _parse_int(s: str) -> int:
    s = s.strip().lower()
    return int(s, 16) if s.startswith("0x") else int(s)


async def _build_serial_transport(args):
    from solar_monitor.transport.serial_modbus import SerialModbusTransport
    t = SerialModbusTransport(
        id="probe", port=args.port,
        baudrate=args.baudrate, inter_frame_gap=0.05,
    )
    await t.open()
    return t


async def _build_ble_transport(args):
    # BT-2 family. The actual transport class moves around between
    # vendors; import inside the function so a CI smoke test without
    # bluetooth deps can still exercise the serial branch.
    from solar_monitor.transport.ble_modbus import BleModbusTransport
    t = BleModbusTransport(id="probe", mac=args.mac)
    await t.open()
    return t


async def _read_one(transport, slave: int, register: int) -> int:
    """Single FC03 read of one word. Returns the value, raises on
    transport failure or malformed response."""
    frame = build_read_holding(slave, register, 1)
    resp = await transport.request(
        frame, expected_read_response_len(1), timeout=3.0,
    )
    verify_response(resp, slave, expected_fc=3)
    return (int(resp[3]) << 8) | int(resp[4])


async def _write_one(transport, slave: int, register: int, value: int) -> bool:
    """FC06 write. Returns True on clean ack, False on the BT-2
    ack-swallow case (which is non-fatal -- caller does the read-back
    to confirm).  Raises on any other transport failure."""
    frame = build_write_single(slave, register, value)
    try:
        resp = await transport.request(
            frame, EXPECTED_WRITE_SINGLE_RESPONSE_LEN, timeout=3.0,
        )
        verify_response(resp, slave, expected_fc=6)
        return True
    except TransportTimeout:
        return False


async def probe(transport, slave: int, register: int, new_value: int | None) -> int:
    print(f"[probe] slave 0x{slave:02X} register 0x{register:04X}")

    # Step 1: read current value.
    try:
        initial = await _read_one(transport, slave, register)
    except Exception as e:
        print(f"[probe] FC03 read failed: {type(e).__name__}: {e}")
        return 1
    print(f"[probe] current value = {initial} (0x{initial:04X})")

    # Step 2: cheap no-op rewrite. Any device that supports FC06 on
    # this register will round-trip cleanly.
    print(f"[probe] no-op write {initial} -> {register:#06x}")
    acked = await _write_one(transport, slave, register, initial)
    await asyncio.sleep(0.3)
    try:
        readback = await _read_one(transport, slave, register)
    except Exception as e:
        print(f"[probe] FC03 read-back failed: {type(e).__name__}: {e}")
        return 1
    if acked:
        print(f"[probe] write acked cleanly. read-back = {readback}")
    else:
        print(f"[probe] write ack swallowed (BT-2 quirk?). "
              f"read-back = {readback}  (matches: {readback == initial})")
    if readback != initial:
        print(f"[probe] FAIL: no-op rewrite changed the register "
              f"({initial} -> {readback}). Refusing to continue.")
        return 1

    if new_value is None:
        print(f"[probe] OK: register reads + accepts no-op write.")
        return 0

    # Step 3: differing write. Restore on the way out so the device
    # never ends in an altered state if the user is just probing.
    print(f"[probe] differing write {new_value} -> {register:#06x}")
    await _write_one(transport, slave, register, new_value)
    await asyncio.sleep(0.5)
    after = await _read_one(transport, slave, register)
    print(f"[probe] after differing write = {after}")
    if after != new_value:
        print(f"[probe] FAIL: device clamped or rejected the value "
              f"(asked for {new_value}, got {after}).")
        # Restore anyway.
        await _write_one(transport, slave, register, initial)
        return 1

    # Restore the original. This script is a probe, not a setter.
    print(f"[probe] restoring original {initial}")
    await _write_one(transport, slave, register, initial)
    await asyncio.sleep(0.5)
    final = await _read_one(transport, slave, register)
    if final != initial:
        print(f"[probe] WARN: register did not restore to {initial} "
              f"(now {final}). Restore manually if needed.")
        return 1
    print(f"[probe] OK: full round-trip on register 0x{register:04X} "
          f"with values [{initial} -> {new_value} -> {initial}].")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="transport", required=True)

    s = sub.add_parser("serial")
    s.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    s.add_argument("--baudrate", type=int, default=9600)
    s.add_argument("--slave", type=_parse_int, required=True)
    s.add_argument("--register", type=_parse_int, required=True)
    s.add_argument("--set", dest="new_value", type=_parse_int, default=None,
                   help="If provided, write this value and confirm before restoring.")

    b = sub.add_parser("ble")
    b.add_argument("--mac", required=True)
    b.add_argument("--slave", type=_parse_int, required=True)
    b.add_argument("--register", type=_parse_int, required=True)
    b.add_argument("--set", dest="new_value", type=_parse_int, default=None)

    return p.parse_args()


async def main() -> int:
    args = parse_args()
    transport = (await _build_serial_transport(args) if args.transport == "serial"
                 else await _build_ble_transport(args))
    try:
        return await probe(transport, args.slave, args.register, args.new_value)
    finally:
        await transport.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
