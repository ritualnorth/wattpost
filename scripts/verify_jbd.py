#!/usr/bin/env python3
"""End-to-end verification for the JBD BMS protocol path (#201).

Pure-Python parse-side smoke test. No hardware required — exercises
the frame builder, frame parser, checksum validation, and driver
field mapping with synthetic basic-info + cell-info frames. The
real BLE GATT round-trip lands when a customer reports back; this
script catches any regression in the parse + mapping layer before
that point.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.transport.ble_jbd import (
    CMD_BASIC_INFO, CMD_CELL_INFO,
    _checksum, build_request, parse_frame,
)
from solar_monitor.vendors.jbd.bms import JbdBms


def build_response(cmd: int, payload: bytes) -> bytes:
    body = bytes([cmd, 0x00, len(payload)]) + payload
    return bytes([0xDD]) + body + _checksum(body) + bytes([0x77])


BASIC = bytes([
    0x05, 0x3C,
    0x13, 0x88,
    0x40, 0xC8,
    0x4E, 0x20,
    0x00, 0x2A,
    0x2E, 0x6F,
    0x00, 0x00,
    0x00, 0x00,
    0x00, 0x00,
    0x10,
    0x5C,
    0x03,
    0x04,
    0x02,
    0x0B, 0x4F,
    0x0B, 0x55,
])
CELLS = bytes([0x0D, 0x12,  0x0D, 0x14,  0x0D, 0x10,  0x0D, 0x16])


class FakeTransport:
    def get_latest_frame(self, cmd):
        return {CMD_BASIC_INFO: BASIC, CMD_CELL_INFO: CELLS}.get(cmd)

    def last_frame_age_s(self):
        return 0.5


async def main() -> int:
    rc = 0
    if build_request(CMD_BASIC_INFO) != bytes([0xDD, 0xA5, 0x03, 0x00, 0xFF, 0xFD, 0x77]):
        print("[verify] request-build mismatch"); return 1
    if parse_frame(build_response(CMD_BASIC_INFO, BASIC)) is None:
        print("[verify] basic-info parse failed"); return 1
    if parse_frame(build_response(CMD_CELL_INFO, CELLS)) is None:
        print("[verify] cell-info parse failed"); return 1

    drv = JbdBms(slave_id=0, label="jbd")
    r = await drv.poll(FakeTransport())
    expected = {
        "voltage_v": 13.40,
        "current_a": -50.0,
        "capacity_ah": 200.0,
        "soc_pct": 92.0,
        "cycle_count": 42,
        "cell_count": 4,
        "temperature_c": 16.4,
        "cell_voltage_0_v": 3.346,
        "cell_voltage_3_v": 3.350,
    }
    for k, v in expected.items():
        actual = r.get(k)
        if isinstance(v, float):
            if actual is None or abs(actual - v) > 1e-3:
                print(f"[verify] {k}={actual!r}, expected ≈{v}"); rc = 1
        else:
            if actual != v:
                print(f"[verify] {k}={actual!r}, expected {v!r}"); rc = 1
    if rc == 0:
        print("[verify] JBD parser + driver round-trip OK")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
