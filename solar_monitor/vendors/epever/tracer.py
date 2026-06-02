"""EPEVER Tracer-family MPPT driver (#203).

Live-state input registers (FC04):

  0x3100   PV input voltage              0.01 V
  0x3101   PV input current              0.01 A
  0x3102   PV input power (low word)     0.01 W (uint32 LE across 02-03)
  0x3103   PV input power (high word)
  0x3104   Battery voltage               0.01 V
  0x3105   Battery charging current      0.01 A
  0x3106   Battery charging power low    0.01 W
  0x3107   Battery charging power high
  0x310C   Load voltage                  0.01 V
  0x310D   Load current                  0.01 A
  0x310E   Load power low                0.01 W
  0x310F   Load power high
  0x3110   Battery temperature           0.01 °C signed
  0x3111   Device temperature            0.01 °C signed
  0x311A   Battery SoC                   1 %
  0x311B   Remote battery temperature    0.01 °C signed

Daily-stats input registers (FC04):

  0x3300   Battery V max today           0.01 V
  0x3301   Battery V min today           0.01 V
  0x3302   Consumed energy today (low)   0.01 kWh = 10 Wh
  0x3303   Consumed energy today (high)
  0x3304   Consumed energy month (low)
  0x3305   Consumed energy month (high)
  0x3306   Consumed energy year (low)
  0x3307   Consumed energy year (high)
  0x3308   Consumed energy total (low)
  0x3309   Consumed energy total (high)
  0x330A   Generated energy today (low)  0.01 kWh
  0x330B   Generated energy today (high)
  0x330C   Generated energy month (low)
  0x330D   Generated energy month (high)
  0x330E   Generated energy year (low)
  0x330F   Generated energy year (high)
  0x3310   Generated energy total (low)
  0x3311   Generated energy total (high)

Load switch state at 0x3201 (read via FC04). Read-only at v1.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section


log = logging.getLogger(__name__)


def _u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _u32_lh(b: bytes, off: int) -> int:
    """EPEVER reports 32-bit values as (low-word, high-word) in
    consecutive registers. Each 16-bit register is still big-endian
    on the wire; the byte order inside is BE but the word order is LE.
    Field at offset `off` is the low word, off+2 is the high word.
    """
    low = _u16(b, off)
    high = _u16(b, off + 2)
    return (high << 16) | low


def _i16(b: bytes, off: int) -> int:
    v = _u16(b, off)
    return v - 0x10000 if v & 0x8000 else v


# Charging state field is a packed uint16 in input register 0x3201:
# bit 0..1 → input mode (0=standby, 1=charge, 2=...)
# bit 2..3 → charging state (0=idle, 1=mppt, 2=equalize, 3=boost, 4=float, 5=current_limit)
# Map matches the Renogy Rover names so the dashboard treats them
# the same.
_CHARGE_STATES = {0: "deactivated", 1: "mppt", 2: "equalizing",
                  3: "boost", 4: "floating", 5: "current_limiting"}


# Live-state block. Two reads because the live data span has a
# gap from 0x3108 to 0x310B (reserved). Reading the gap eats time
# on flaky links.
def _parse_pv_batt(bs: bytes) -> dict:
    # Response starts at byte 3 (slave + fc + bytecount header).
    return {
        "pv_voltage_v":   _u16(bs, 3) / 100.0,
        "pv_current_a":   _u16(bs, 5) / 100.0,
        "pv_power_w":     _u32_lh(bs, 7) / 100.0,
        "battery_voltage_v":     _u16(bs, 11) / 100.0,
        "battery_charging_current_a": _u16(bs, 13) / 100.0,
        "power_w":        _u32_lh(bs, 15) / 100.0,   # = charging power
        "charging_power_w": _u32_lh(bs, 15) / 100.0,
    }


def _parse_load(bs: bytes) -> dict:
    return {
        "load_voltage_v":      _u16(bs, 3) / 100.0,
        "load_current_a":      _u16(bs, 5) / 100.0,
        "load_power_w":        _u32_lh(bs, 7) / 100.0,
        "battery_temperature_c":     _i16(bs, 11) / 100.0,
        "controller_temperature_c":  _i16(bs, 13) / 100.0,
    }


def _parse_soc_block(bs: bytes) -> dict:
    # Read of 0x311A 1 word.
    return {"battery_percentage": _u16(bs, 3)}


def _parse_status(bs: bytes) -> dict:
    raw = _u16(bs, 3)
    cs = (raw >> 2) & 0x0F
    return {
        "charging_state": _CHARGE_STATES.get(cs, f"state_{cs}"),
        "status_raw": raw,
    }


def _parse_daily(bs: bytes) -> dict:
    # 0x3302 (consumed today low) starts here in this read; we
    # also pull generated values which sit at +8 words. The block
    # is 18 words long.
    return {
        "load_consumed_today_wh":     _u32_lh(bs, 3)   * 10,
        "load_consumed_month_wh":     _u32_lh(bs, 7)   * 10,
        "load_consumed_year_wh":      _u32_lh(bs, 11)  * 10,
        "load_consumed_total_wh":     _u32_lh(bs, 15)  * 10,
        "pv_generated_today_wh":      _u32_lh(bs, 19)  * 10,
        "pv_generated_month_wh":      _u32_lh(bs, 23)  * 10,
        "pv_generated_year_wh":       _u32_lh(bs, 27)  * 10,
        "pv_generated_total_wh":      _u32_lh(bs, 31)  * 10,
    }


class EpeverTracer(DeviceDriver):
    """EPEVER MPPT driver. Pending real-hardware validation."""
    vendor_id = "epever"
    device_kind = "charge_controller"

    @property
    def sections(self) -> tuple[Section, ...]:
        return (
            # PV + battery side. 0x3100, 9 words = 18 bytes payload.
            Section(register=0x3100, word_count=9,
                    parser=_parse_pv_batt, name="pv_batt",
                    function_code=4),
            # Load + temps. 0x310C, 6 words.
            Section(register=0x310C, word_count=6,
                    parser=_parse_load, name="load",
                    function_code=4),
            # Battery SoC. 0x311A, 1 word.
            Section(register=0x311A, word_count=1,
                    parser=_parse_soc_block, name="soc",
                    function_code=4),
            # Status word. 0x3201, 1 word.
            Section(register=0x3201, word_count=1,
                    parser=_parse_status, name="status",
                    function_code=4),
            # Daily / monthly / yearly / total energy. 0x3302, 16 words.
            Section(register=0x3302, word_count=16,
                    parser=_parse_daily, name="daily",
                    function_code=4),
        )
