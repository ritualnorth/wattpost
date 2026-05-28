"""Deye / Sunsynk / Sol-Ark single-phase + split-phase driver.

Covers:
  * Deye SUN-5K/8K-SG04LP1
  * Sunsynk SG01LP1 3.6K / 5.5K / 8K / 16K
  * Sol-Ark 5K / 8K / 12K-1P
  * Sol-Ark 12K-2P (US split-phase 120/240 V, still single-MPPT-pair on
    the inverter side; treated as single-phase from the protocol view)

Modbus RTU, holding registers (FC03), 9600 8N1, slave 1.

Register map (decimal, holding registers):

    Reg  Field                       Scale    Unit  Notes
    ---  --------------------------- -------- ----- ----------------
     59  device_status               enum    ,     mode enum
     79  grid_frequency_hz           ×0.01    Hz
     90  dc_transformer_temperature  ×0.1     °C    main inverter temp
     91  radiator_temperature        ×0.1     °C
    109  pv1_voltage                 ×0.1     V
    111  pv2_voltage                 ×0.1     V
    150  grid_voltage_l1             ×0.1     V
    154  ac_output_voltage           ×0.1     V    inverter (load) bus
    167  load_l1_power               ×–1      W    sign-flipped
    169  power_to_grid               ×–1      W    derived from CT
    172  ct_power                    ×–1      W
    175  ac_output_power             ×–1      W    sign-flipped
    182  battery_temperature         ×0.1     °C
    183  battery_voltage             ×0.01    V    NOTE: ×0.01, NOT ×0.1
    184  battery_soc                 ×1       %
    186  pv1_power                   ×–1      W    sign-flipped
    187  pv2_power                   ×–1      W    sign-flipped
    190  battery_power               ×–1      W    sign-flipped; ours: +=charge
    191  battery_current             ×–0.01   A    sign-flipped; ours: +=charge
    193  ac_output_frequency         ×0.01    Hz

References (all credited in NOTICE, facts not creative expression):
  * kellerza/sunsynk MIT-then-Apache-2.0, definitions/single_phase.py
  * StephanJoubert/home_assistant_solarman Apache-2.0, solarman
    integration register tables.
  * Deye Modbus PDF mirror (domotica.solar).

The driver reads four targeted Sections rather than one big sweep
because the address space between covered registers is reserved
on Deye firmware and some FW revs NAK long reads that span
unmapped regions. Same defensive shape we used on the EG4 driver.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section
from ._common import label_for_mode, s16, u16, u32

log = logging.getLogger(__name__)


def _parse_status(bs: bytes) -> dict[str, Any]:
    """Reg 59, single-word read. Mode enum in low byte."""
    raw = u16(bs, 3)
    code = raw & 0xFF
    return {
        "device_status_code": code,
        "inverter_mode_code": code,
        "inverter_mode": label_for_mode(code),
    }


def _parse_temps_and_grid_hz(bs: bytes) -> dict[str, Any]:
    """Reg 79..91, 13 words. Pulls grid Hz (reg 79) + the two
    inverter temps (90, 91). Energy totals at 78/80/81/82 fall in
    this block too, surface them as lifetime kWh counters so the
    Energy page can chart real history when a customer's been
    running for weeks."""
    out: dict[str, Any] = {}
    # Reg 79: grid frequency ÷100. Off-grid installs report 0.
    grid_hz = u16(bs, 3) / 100.0
    if grid_hz > 0:
        out["grid_frequency_hz"] = round(grid_hz, 2)
    # Regs 80-81: total grid import (Wh, uint32 high-first). Reg 78
    # is "today" import; we surface only the lifetime running totals
    # because "today" stats are derived elsewhere from our own
    # samples.
    try:
        grid_import_wh = u32(bs, 5) * 100  # ×0.1 kWh = 100 Wh
        if grid_import_wh > 0:
            out["lifetime_grid_import_wh"] = grid_import_wh
    except IndexError:
        pass
    # Regs 82-83: lifetime grid export.
    try:
        grid_export_wh = u32(bs, 9) * 100
        if grid_export_wh > 0:
            out["lifetime_grid_export_wh"] = grid_export_wh
    except IndexError:
        pass
    # Reg 90: dc transformer temp = our canonical inverter temp.
    # Reg 91: radiator temp (secondary).
    dc_t = s16(bs, 25) / 10.0
    rad_t = s16(bs, 27) / 10.0
    out["temperature_c"] = round(dc_t, 1)
    out["radiator_temperature_c"] = round(rad_t, 1)
    return out


def _parse_pv_voltages(bs: bytes) -> dict[str, Any]:
    """Reg 109..111, 3 words. PV1 + PV2 voltage. Power lives in
    a separate block (reg 186-187)."""
    out: dict[str, Any] = {}
    pv1_v = u16(bs, 3) / 10.0
    pv2_v = u16(bs, 7) / 10.0
    if pv1_v > 0: out["pv1_voltage_v"] = round(pv1_v, 1)
    if pv2_v > 0: out["pv2_voltage_v"] = round(pv2_v, 1)
    if pv1_v > 0 and pv2_v > 0:
        out["pv_voltage_v"] = round((pv1_v + pv2_v) / 2.0, 1)
    elif pv1_v > 0 or pv2_v > 0:
        out["pv_voltage_v"] = round(max(pv1_v, pv2_v), 1)
    return out


def _parse_grid_and_ac(bs: bytes) -> dict[str, Any]:
    """Reg 150..175, 26 words. Grid voltage (150), AC output
    voltage (154), load + CT + AC output powers (167-175).

    The sign-flip on Deye wire-values for powers is universal in
    this driver: the wire-positive value means "flowing out" of
    the inverter's perspective; we flip so positive = charging
    into the bank / generating / exporting to the grid."""
    out: dict[str, Any] = {}
    # Reg 150: grid_voltage_l1 ×0.1
    grid_v = u16(bs, 3) / 10.0
    if grid_v > 0:
        out["grid_voltage_v"] = round(grid_v, 1)
    # Reg 154: ac_output_voltage (the load bus on a hybrid install)
    # = bytes offset 3 + (154-150)*2 = 11
    ac_v = u16(bs, 11) / 10.0
    if ac_v > 0:
        out["ac_output_voltage_v"] = round(ac_v, 1)
    # Reg 167: load_l1_power = bytes offset 3 + (167-150)*2 = 37
    load_w = -s16(bs, 37)
    if load_w != 0:
        out["load_power_w"] = load_w
    # Reg 169: power_to_grid (CT-derived). Bytes offset 41.
    grid_w = -s16(bs, 41)
    if grid_w > 0:
        out["power_to_grid_w"] = grid_w
    elif grid_w < 0:
        out["power_to_user_w"] = -grid_w
    # Reg 175: ac_output_power = bytes offset 3 + (175-150)*2 = 53
    ac_w = -s16(bs, 53)
    if ac_w != 0:
        out["ac_output_power_w"] = ac_w
    return out


def _parse_battery_and_pv_power(bs: bytes) -> dict[str, Any]:
    """Reg 182..194, 13 words. Battery temp/V/SoC/current/power
    plus PV per-string powers (186, 187) plus AC output frequency.

    The single most footgun-prone block on this protocol: battery
    voltage is ÷100 (not ÷10 like every other voltage on the
    inverter), battery_power and PV powers are sign-flipped, and
    battery_current is signed-and-×0.01."""
    out: dict[str, Any] = {}
    # Reg 182: battery_temperature ÷10 signed.
    out["battery_temperature_c"] = round(s16(bs, 3) / 10.0, 1)
    # Reg 183: battery_voltage ÷100 (Deye quirk vs ÷10 everywhere else).
    batt_v = u16(bs, 5) / 100.0
    out["battery_voltage_v"] = round(batt_v, 2)
    # Reg 184: SoC, 1:1.
    out["soc_pct"] = u16(bs, 7)
    # Reg 185: reserved.
    # Reg 186: pv1_power ×-1 (sign-flipped from wire).
    pv1_w = -s16(bs, 11)
    # Reg 187: pv2_power ×-1.
    pv2_w = -s16(bs, 13)
    if pv1_w != 0: out["pv1_power_w"] = pv1_w
    if pv2_w != 0: out["pv2_power_w"] = pv2_w
    # PV powers are non-negative in normal operation; sum into the
    # canonical pv_power_w. Clamp at 0 because a sign-flipped read
    # of a transient reverse-current can otherwise emit "-3 W PV"
    # which the Solar tile reads as garbage.
    pv_total = max(0, pv1_w) + max(0, pv2_w)
    out["pv_power_w"] = pv_total
    # Reg 188-189: reserved. Two-word gap shifts the rest of the
    # block by 4 bytes, easy to miscount, the original draft of
    # this parser had battery_power reading the reserved word and
    # always returning 0.
    # Reg 190: battery_power ×-1 (positive = charge). Offset:
    # 3 + (190 - 182) * 2 = 19.
    batt_w = -s16(bs, 19)
    out["battery_power_w"] = batt_w
    # Reg 191: battery_current ×-0.01 signed (positive = charge).
    # Offset: 3 + (191 - 182) * 2 = 21.
    batt_a = -s16(bs, 21) / 100.0
    out["battery_current_a"] = round(batt_a, 2)
    # Reg 192: reserved.
    # Reg 193: ac_output_frequency ÷100. Offset: 3 + 22 = 25.
    ac_hz = u16(bs, 25) / 100.0
    if ac_hz > 0:
        out["ac_output_frequency_hz"] = round(ac_hz, 2)
    return out


class DeyeInverter1P(DeviceDriver):
    """Deye / Sunsynk / Sol-Ark single-phase + split-phase driver.

    Single-MPPT-pair (PV1 + PV2). All readings via FC03 on the
    inverter's RS485 RJ45 port. Experimental until first real
    customer confirms per-firmware scale factors."""
    vendor_id = "deye"
    device_kind = "inverter_1p"

    @property
    def sections(self) -> tuple[Section, ...]:
        # FC03 (Read Holding Registers). Four targeted reads,
        # carefully sized to avoid the unmapped-region NAKs some
        # Deye firmwares emit on long sweeps.
        return (
            Section(register=59, word_count=1,
                    parser=_parse_status, name="status",
                    function_code=3),
            Section(register=79, word_count=13,
                    parser=_parse_temps_and_grid_hz, name="temps_grid_hz",
                    function_code=3),
            Section(register=109, word_count=3,
                    parser=_parse_pv_voltages, name="pv_voltages",
                    function_code=3),
            Section(register=150, word_count=26,
                    parser=_parse_grid_and_ac, name="grid_ac",
                    function_code=3),
            Section(register=182, word_count=12,
                    parser=_parse_battery_and_pv_power, name="battery_pv_power",
                    function_code=3),
        )
