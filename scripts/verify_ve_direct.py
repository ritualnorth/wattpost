#!/usr/bin/env python3
"""End-to-end verification for the VE.Direct transport + Victron
device drivers (#197). Mirrors scripts/verify_fc06_serial.py:
opens a pty pair, has a tiny background thread emit canned
VE.Direct text frames on one side, and runs the production
VeDirectTransport + per-device-kind drivers against the other.

Exits 0 on success, 1 on the first failure. Safe to wire into
CI later — needs no hardware.

Why this exists: VE.Direct is text-framed with a checksum byte
that's easy to compute wrong if a label gets edited. Catching
that here is much cheaper than catching it on a customer's Pi.
"""
from __future__ import annotations

import asyncio
import os
import pty
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.transport.ve_direct import VeDirectTransport
from solar_monitor.vendors.victron.ve_direct import (
    VictronVeDirectShunt, VictronVeDirectMppt, VictronVeDirectPhoenix,
)


def build_frame(lines: list[tuple[str, str]]) -> bytes:
    body = b""
    for label, value in lines:
        body += b"\r\n" + label.encode() + b"\t" + value.encode()
    body += b"\r\nChecksum\t"
    s = sum(body) & 0xFF
    return body + bytes([(-s) & 0xFF])


def emit_frames(fd: int, frame: bytes, stop: threading.Event) -> None:
    """A real device emits a fresh frame ~once per second. We do the
    same so the transport's reader has more than one chance to land
    a frame before the test gives up."""
    while not stop.is_set():
        try:
            os.write(fd, frame)
        except OSError:
            return
        time.sleep(0.5)


async def _one_case(name: str, driver_cls, frame: bytes,
                    expected: dict) -> int:
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    stop = threading.Event()
    t = threading.Thread(target=emit_frames, args=(master, frame, stop),
                         daemon=True)
    t.start()

    transport = VeDirectTransport(id="probe", port=path)
    await transport.open()
    try:
        # Wait for a frame.
        deadline = asyncio.get_event_loop().time() + 3.0
        while transport.get_latest() is None:
            if asyncio.get_event_loop().time() > deadline:
                print(f"[verify] {name}: FAIL no frame arrived")
                return 1
            await asyncio.sleep(0.05)

        driver = driver_cls(slave_id=0, label=name)
        result = await driver.poll(transport)
        for k, v in expected.items():
            actual = result.get(k)
            if isinstance(v, float):
                if actual is None or abs(actual - v) > 1e-3:
                    print(f"[verify] {name}: FAIL {k}={actual!r}, expected ≈{v}")
                    return 1
            else:
                if actual != v:
                    print(f"[verify] {name}: FAIL {k}={actual!r}, expected {v!r}")
                    return 1
        print(f"[verify] {name}: OK ({len(result)} fields)")
        return 0
    finally:
        stop.set()
        await transport.close()
        os.close(master)


async def main() -> int:
    shunt_frame = build_frame([
        ("PID", "0xA389"), ("V", "13412"), ("I", "-1230"), ("P", "-16"),
        ("SOC", "912"), ("TTG", "1450"), ("T", "23"),
        ("BMV", "SmartShunt 500A/50mV"), ("Alarm", "OFF"),
    ])
    mppt_frame = build_frame([
        ("PID", "0xA053"), ("V", "13680"), ("I", "5400"), ("VPV", "42100"),
        ("PPV", "120"), ("CS", "3"), ("ERR", "0"), ("H20", "240"),
        ("H19", "12500"), ("H21", "180"), ("LOAD", "ON"),
    ])
    inv_frame = build_frame([
        ("PID", "0xA231"), ("V", "12800"),
        ("AC_OUT_V", "23001"), ("AC_OUT_I", "12"), ("AC_OUT_S", "276"),
        ("MODE", "2"), ("ERR", "0"), ("WARN", "0"),
    ])

    rc = 0
    rc |= await _one_case("shunt", VictronVeDirectShunt, shunt_frame, {
        "voltage_v": 13.412, "current_a": -1.230, "soc_pct": 91.2,
        "time_to_go_minutes": 1450, "power_w": -16.0,
    })
    rc |= await _one_case("mppt", VictronVeDirectMppt, mppt_frame, {
        "voltage_v": 13.68, "pv_power_w": 120, "power_w": 120,
        "today_yield_wh": 2400, "total_yield_wh": 125000,
        "charging_state": "bulk", "load_output": True,
    })
    rc |= await _one_case("phoenix", VictronVeDirectPhoenix, inv_frame, {
        "battery_voltage_v": 12.8, "ac_output_voltage_v": 230.01,
        "ac_output_current_a": 1.2, "ac_output_apparent_va": 276,
    })
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
