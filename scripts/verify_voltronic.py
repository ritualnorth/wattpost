#!/usr/bin/env python3
"""Pure-Python verification for the Voltronic-family driver (#360).

No hardware required — exercises the CRC layer, QPIGS / QMOD /
QPIWS parsers, and the full driver poll() against a fake transport
that mimics the usbhid_voltronic.query() contract.

The real USB-HID round-trip lands when a customer reports back;
this script catches regressions in the protocol layer before then.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.vendors.voltronic.inverter import (
    VoltronicInverter, parse_qmod, parse_qpigs, parse_qpiws,
)
from solar_monitor.voltronic_crc import (
    crc16_xmodem, frame_command, verify_and_strip, voltronic_crc,
)


# Canned QPIGS payload from a real-world MPP PIP 5048MK dump.
# Grid down, running off battery, 459 V bus, 50.40 V batt, 98 % SoC,
# 24 °C inverter, no PV, single-phase 230 V/50 Hz output @ 61 W.
QPIGS_PAYLOAD = (
    b"000.0 00.0 230.0 49.9 0089 0061 002 459 "
    b"50.40 000 098 0024 0000 000.0 50.40 00000 "
    b"00010110 00 00 00000 010"
)


def fake_response(payload: bytes) -> bytes:
    """Build a Voltronic on-wire response so we can exercise verify_and_strip."""
    return b"(" + payload + voltronic_crc(payload) + b"\r"


class FakeTransport:
    """Mimics usbhid_voltronic.query — returns the pre-decoded payload
    a real transport hands to the driver."""

    def __init__(self) -> None:
        self.responses = {
            "QPI":   b"PI30",
            "QID":   b"96332309100452",
            "QMOD":  b"B",
            "QPIGS": QPIGS_PAYLOAD,
            "QPIWS": b"00000000000000000000000000000000",
        }

    async def query(self, cmd: str, timeout: float = 5.0) -> bytes:
        if cmd not in self.responses:
            raise ValueError(f"unexpected cmd {cmd!r}")
        return self.responses[cmd]


async def main() -> int:
    rc = 0

    # 1. CRC sanity: QPIGS frame round-trip.
    framed = frame_command("QPIGS")
    if not (framed.startswith(b"QPIGS") and framed.endswith(b"\r")):
        print(f"[verify] frame_command shape wrong: {framed!r}"); rc = 1

    # Decode our own fake response and check the payload comes back clean.
    body = fake_response(QPIGS_PAYLOAD)
    # strip leading '(' as the real transport does
    decoded = verify_and_strip(body.lstrip(b"("))
    if decoded != QPIGS_PAYLOAD:
        print(f"[verify] verify_and_strip mismatch"); rc = 1

    # 2. XMODEM baseline vector ("123456789" → 0x31C3).
    if crc16_xmodem(b"123456789") != 0x31C3:
        print(f"[verify] XMODEM CRC seed broken"); rc = 1

    # 3. QPIGS parser fields.
    p = parse_qpigs(QPIGS_PAYLOAD)
    expected = {
        "grid_voltage_v":            0.0,
        "grid_frequency_hz":         0.0,
        "ac_output_voltage_v":     230.0,
        "ac_output_frequency_hz":   49.9,
        "ac_output_apparent_power_va": 89,
        "ac_output_power_w":        61,
        "ac_output_load_pct":        2,
        "bus_voltage_v":           459.0,
        "battery_voltage_v":        50.40,
        "soc_pct":                  98,
        "temperature_c":            24.0,
        "pv_voltage_v":              0.0,
        "battery_charging_current_a":     0.0,
        "battery_discharging_current_a":  0.0,
        "battery_current_a":              0.0,
        "pv_power_w":                0,
    }
    for k, v in expected.items():
        got = p.get(k)
        ok = (got == v) if isinstance(v, int) else (got is not None and abs(got - v) < 1e-3)
        if not ok:
            print(f"[verify] parse_qpigs[{k}]={got!r}, expected {v!r}"); rc = 1

    # 4. QMOD parser.
    if parse_qmod(b"B").get("inverter_mode") != "battery":
        print("[verify] parse_qmod failed for 'B'"); rc = 1
    if parse_qmod(b"L").get("inverter_mode") != "line":
        print("[verify] parse_qmod failed for 'L'"); rc = 1
    if parse_qmod(b"X").get("inverter_mode") != "unknown":
        print("[verify] parse_qmod failed unknown-fallback"); rc = 1

    # 5. QPIWS parser — count active warning bits.
    qpiws_active = parse_qpiws(b"00010000000000000000000000000000")
    if qpiws_active.get("warning_count") != 1:
        print(f"[verify] parse_qpiws warning_count={qpiws_active.get('warning_count')}"); rc = 1

    # 6. Driver poll round-trip.
    drv = VoltronicInverter(slave_id=1, label="voltronic.inverter.1")
    r = await drv.poll(FakeTransport())
    if r.get("inverter_mode") != "battery":
        print(f"[verify] driver inverter_mode={r.get('inverter_mode')!r}"); rc = 1
    if r.get("soc_pct") != 98:
        print(f"[verify] driver soc_pct={r.get('soc_pct')!r}"); rc = 1
    if r.get("battery_voltage_v") != 50.40:
        print(f"[verify] driver battery_voltage_v={r.get('battery_voltage_v')!r}"); rc = 1
    if r.get("serial_number") != "96332309100452":
        print(f"[verify] driver serial_number={r.get('serial_number')!r}"); rc = 1
    if r.get("protocol_id") != "PI30":
        print(f"[verify] driver protocol_id={r.get('protocol_id')!r}"); rc = 1
    if r.get("_errors"):
        print(f"[verify] driver reported errors: {r['_errors']}"); rc = 1

    if rc == 0:
        print("[verify] Voltronic CRC + parser + driver round-trip OK")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
