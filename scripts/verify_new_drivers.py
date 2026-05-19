#!/usr/bin/env python3
"""End-to-end verify-from-docs for the v0.1.25 driver batch.

JBD / Daly / EPEVER / AiLi / Junctek all ship as "pending real-
hardware validation" per project_no_victron_lab_purchases. This
script exercises each protocol's parse layer + each driver's field
mapping with synthetic frames so any regression in the maths shows
up in CI before a customer reports it.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---- JBD --------------------------------------------------------

from solar_monitor.transport.ble_jbd import (
    CMD_BASIC_INFO, CMD_CELL_INFO, _checksum as _jbd_cks, parse_frame as _jbd_parse,
)
from solar_monitor.vendors.jbd.bms import JbdBms


def _jbd_resp(cmd, payload):
    body = bytes([cmd, 0x00, len(payload)]) + payload
    return bytes([0xDD]) + body + _jbd_cks(body) + bytes([0x77])


JBD_BASIC = bytes([
    0x05, 0x3C, 0x13, 0x88, 0x40, 0xC8, 0x4E, 0x20,
    0x00, 0x2A, 0x2E, 0x6F, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x10, 0x5C, 0x03, 0x04, 0x02,
    0x0B, 0x4F, 0x0B, 0x55,
])
JBD_CELLS = bytes([0x0D, 0x12, 0x0D, 0x14, 0x0D, 0x10, 0x0D, 0x16])


class JbdFakeTransport:
    def get_latest_frame(self, cmd):
        return {CMD_BASIC_INFO: JBD_BASIC, CMD_CELL_INFO: JBD_CELLS}.get(cmd)
    def last_frame_age_s(self): return 0.5


async def check_jbd():
    drv = JbdBms(slave_id=0, label="jbd")
    r = await drv.poll(JbdFakeTransport())
    assert abs(r["voltage_v"] - 13.40) < 1e-3
    assert abs(r["current_a"] - (-50.0)) < 1e-3
    assert r["soc_pct"] == 92.0 and r["cell_count"] == 4
    assert r["cell_voltage_0_v"] == 3.346
    print("[verify] JBD: OK")


# ---- Daly -------------------------------------------------------

from solar_monitor.transport.ble_daly import _checksum as _daly_cks
from solar_monitor.vendors.daly.bms import DalyBms


class DalyFakeTransport:
    def __init__(self):
        self._f = {
            0x90: bytes([0x00, 0x86, 0x00, 0x00, 0x76, 0x2A, 0x03, 0x90]),
            0x92: bytes([62, 1, 61, 3, 0, 0, 0, 0]),
            0x93: bytes([0x01, 0x01, 0x01, 100, 0x00, 0x02, 0x71, 0x00]),
            0x94: bytes([0x04, 0x02, 0x01, 0x01, 0x00, 0x00, 0x2A, 0x00]),
        }
        self._cells = [3346, 3348, 3344, 3350]
    def get_latest_frame(self, cmd): return self._f.get(cmd)
    def get_cell_voltages_mv(self): return list(self._cells)
    def last_frame_age_s(self): return 0.3


async def check_daly():
    drv = DalyBms(slave_id=0, label="daly")
    r = await drv.poll(DalyFakeTransport())
    assert abs(r["voltage_v"] - 13.4) < 1e-3
    assert abs(r["current_a"] - 25.0) < 1e-3
    assert r["soc_pct"] == 91.2 and r["cell_count"] == 4
    assert r["temperature_c"] == 22
    print("[verify] Daly: OK")


# ---- EPEVER -----------------------------------------------------

from solar_monitor.modbus import crc16
from solar_monitor.vendors.epever.tracer import EpeverTracer


def _ep_fc04(slave, words):
    body = bytes([slave, 4, len(words) * 2])
    for w in words:
        body += bytes([(w >> 8) & 0xFF, w & 0xFF])
    return body + crc16(body)


EP_REPLIES = {
    0x3100: _ep_fc04(1, [1850, 240, 4440, 0, 1350, 210, 2835, 0, 0]),
    0x310C: _ep_fc04(1, [1295, 410, 5310, 0, 2350, 3120]),
    0x311A: _ep_fc04(1, [84]),
    0x3201: _ep_fc04(1, [0x04]),
    0x3302: _ep_fc04(1, [40, 0, 200, 0, 1200, 0, 5000, 0,
                         120, 0, 500, 0, 3000, 0, 9000, 0]),
}


class EpeverFakeTransport:
    async def request(self, frame, n, timeout=5.0):
        reg = (frame[2] << 8) | frame[3]
        return EP_REPLIES[reg]


async def check_epever():
    drv = EpeverTracer(slave_id=1, label="tracer")
    r = await drv.poll(EpeverFakeTransport())
    assert abs(r["pv_voltage_v"] - 18.5) < 1e-3
    assert abs(r["battery_voltage_v"] - 13.5) < 1e-3
    assert r["battery_percentage"] == 84
    assert r["charging_state"] == "mppt"
    assert r["pv_generated_today_wh"] == 1200
    print("[verify] EPEVER: OK")


# ---- AiLi -------------------------------------------------------

from solar_monitor.vendors.aili.shunt import AiliShunt


class AiliFakeTransport:
    def __init__(self, payload):
        self._payload = payload
    def get_latest(self): return self._payload
    def last_frame_age_s(self): return 0.4


async def check_aili():
    payload = {
        "voltage_v": 13.42, "current_a": -45.0,
        "remaining_ah": 165.0, "soc_pct": 82,
        "temperature_c": 21, "time_to_go_minutes": 150,
        "cumulative_ah": 50.0,
    }
    drv = AiliShunt(slave_id=0, label="aili")
    r = await drv.poll(AiliFakeTransport(payload))
    assert abs(r["voltage_v"] - 13.42) < 1e-3
    assert abs(r["power_w"] - (13.42 * -45.0)) < 1e-2
    assert abs(r["bank_capacity_ah"] - 201.22) < 0.01
    print("[verify] AiLi: OK")


# ---- Junctek ----------------------------------------------------

from solar_monitor.transport.ble_junctek import parse_response
from solar_monitor.vendors.junctek.shunt import JunctekShunt


class JunctekFakeTransport:
    def __init__(self, merged):
        self._m = merged
    def get_latest(self): return self._m
    def last_frame_age_s(self): return 0.4


async def check_junctek():
    r50 = parse_response(":r50=1342,4500,1,165000,200,")
    r51 = parse_response(":r51=123,12345,56789,")
    r53 = parse_response(":r53=82,150,-604,")
    merged = {**r50, **r51, **r53}
    drv = JunctekShunt(slave_id=0, label="junctek")
    r = await drv.poll(JunctekFakeTransport(merged))
    assert r["power_w"] == -604
    assert r["voltage_v"] == 13.42 and r["current_a"] == -45.0
    assert r["soc_pct"] == 82 and r["time_to_go_minutes"] == 150
    print("[verify] Junctek: OK")


async def main() -> int:
    await check_jbd()
    await check_daly()
    await check_epever()
    await check_aili()
    await check_junctek()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
