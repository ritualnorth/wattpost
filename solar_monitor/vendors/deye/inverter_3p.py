"""Deye / Sunsynk / Sol-Ark three-phase driver.

Covers:
  * Deye SUN-12K/15K/20K/25K-SG01HP3 (HV battery, 3-phase)
  * Sunsynk Max-15K / Max-20K
  * Sol-Ark 15K-3P
  * Sunsynk Max 3-Phase LV variants (share the common register
    block at 500..689 — the HV-vs-LV diff is mostly battery-side
    enums, not the live-watts surface this driver covers).

Modbus RTU, holding registers (FC03), 9600 8N1, slave 1.

Register map (decimal, holding registers):

    Reg  Field                       Scale    Unit  Notes
    ---  --------------------------- -------- ----- ------------------
    500  device_status               enum     —     same enum as 1P
    540  dc_transformer_temperature  ×0.1     °C
    541  radiator_temperature        ×0.1     °C
    586  battery_temperature         ×0.1     °C
    587  battery_voltage             ×0.1     V    NOTE: ÷10, NOT ÷100
    588  battery_soc                 ×1       %
    590  battery_power               ×–10     W    NOTE: ×10, sign-flipped
    591  battery_current             ×–0.01   A
    598  grid_l1_voltage             ×0.1     V
    599  grid_l2_voltage             ×0.1     V
    600  grid_l3_voltage             ×0.1     V
    609  grid_frequency              ×0.01    Hz
    622  grid_l1_power               ×–1      W    sign-flipped
    623  grid_l2_power               ×–1      W
    624  grid_l3_power               ×–1      W
    627  inverter_l1_voltage         ×0.1     V    AC output L1
    628  inverter_l2_voltage         ×0.1     V
    629  inverter_l3_voltage         ×0.1     V
    638  inverter_frequency          ×0.01    Hz   AC output Hz
    653  load_power                  ×–1      W
    672  pv1_power                   ×10      W    NOTE: deci-watts on wire
    673  pv2_power                   ×10      W
    674  pv3_power                   ×10      W
    675  pv4_power                   ×10      W
    676  pv1_voltage                 ×0.1     V
    678  pv2_voltage                 ×0.1     V
    680  pv3_voltage                 ×0.1     V
    682  pv4_voltage                 ×0.1     V

Footguns vs the single-phase variant:
  * battery_voltage on 3P is ÷10 (vs ÷100 on 1P). Easy mistake.
  * battery_power on 3P is ×–10 — wire-value is in deci-watts.
    Multiply raw by 10 BEFORE sign-flipping.
  * PV powers on 3P are POSITIVE deci-watts (×10), not sign-flipped
    watts like 1P. Same protocol family, different conventions.

References credited in NOTICE (Apache-2.0):
  * kellerza/sunsynk definitions/three_phase_common.py,
    definitions/three_phase_hv.py.
  * StephanJoubert/home_assistant_solarman 3P table.
  * Deye Modbus PDF mirror.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section
from ._common import label_for_mode, s16, u16

log = logging.getLogger(__name__)


def _parse_status(bs: bytes) -> dict[str, Any]:
    raw = u16(bs, 3)
    code = raw & 0xFF
    return {
        "device_status_code": code,
        "inverter_mode_code": code,
        "inverter_mode": label_for_mode(code),
    }


def _parse_temps(bs: bytes) -> dict[str, Any]:
    """Reg 540..541, 2 words. The inverter + radiator temps."""
    dc_t = s16(bs, 3) / 10.0
    rad_t = s16(bs, 5) / 10.0
    return {
        "temperature_c": round(dc_t, 1),
        "radiator_temperature_c": round(rad_t, 1),
    }


def _parse_battery(bs: bytes) -> dict[str, Any]:
    """Reg 586..591, 6 words. Battery temp / V / SoC / power /
    current. Note the per-register scale differences vs single-phase
    — battery_voltage is ÷10 here (not ÷100), battery_power is in
    deci-watts and sign-flipped (×–10)."""
    out: dict[str, Any] = {}
    out["battery_temperature_c"] = round(s16(bs, 3) / 10.0, 1)
    out["battery_voltage_v"] = round(u16(bs, 5) / 10.0, 2)
    out["soc_pct"] = u16(bs, 7)
    # Reg 589: reserved.
    # Reg 590: battery_power in deci-watts, sign-flipped.
    batt_w = -s16(bs, 11) * 10
    out["battery_power_w"] = batt_w
    # Reg 591: battery_current ×-0.01 signed.
    batt_a = -s16(bs, 13) / 100.0
    out["battery_current_a"] = round(batt_a, 2)
    return out


def _parse_grid(bs: bytes) -> dict[str, Any]:
    """Reg 598..624, 27 words. Grid voltages (598-600), grid
    frequency (609), per-leg grid powers (622-624). Three-phase
    voltages stay at 0 on legs not physically wired; suppress
    those so a single-phase install on a 3P-capable inverter
    doesn't paint phantom 0 V tiles."""
    out: dict[str, Any] = {}
    # Regs 598-600: per-leg grid voltage ÷10.
    l1_v = u16(bs, 3)  / 10.0
    l2_v = u16(bs, 5)  / 10.0
    l3_v = u16(bs, 7)  / 10.0
    if l1_v > 0: out["grid_l1_voltage_v"] = round(l1_v, 1)
    if l2_v > 0: out["grid_l2_voltage_v"] = round(l2_v, 1)
    if l3_v > 0: out["grid_l3_voltage_v"] = round(l3_v, 1)
    # Canonical grid_voltage_v = L1 when present, else max-non-zero.
    legs = [v for v in (l1_v, l2_v, l3_v) if v > 0]
    if legs:
        out["grid_voltage_v"] = round(legs[0], 1)
    # Reg 609: grid frequency ÷100. Offset = 3 + (609-598)*2 = 25.
    grid_hz = u16(bs, 25) / 100.0
    if grid_hz > 0:
        out["grid_frequency_hz"] = round(grid_hz, 2)
    # Regs 622-624: per-leg grid power sign-flipped.
    # Offset for 622 = 3 + (622-598)*2 = 51.
    l1_p = -s16(bs, 51)
    l2_p = -s16(bs, 53)
    l3_p = -s16(bs, 55)
    total_grid = l1_p + l2_p + l3_p
    if total_grid > 0:
        out["power_to_grid_w"] = total_grid
    elif total_grid < 0:
        out["power_to_user_w"] = -total_grid
    return out


def _parse_ac_output(bs: bytes) -> dict[str, Any]:
    """Reg 627..653, 27 words. AC output per-leg voltages (627-629),
    output frequency (638), load power (653)."""
    out: dict[str, Any] = {}
    l1 = u16(bs, 3) / 10.0
    l2 = u16(bs, 5) / 10.0
    l3 = u16(bs, 7) / 10.0
    if l1 > 0: out["ac_output_l1_voltage_v"] = round(l1, 1)
    if l2 > 0: out["ac_output_l2_voltage_v"] = round(l2, 1)
    if l3 > 0: out["ac_output_l3_voltage_v"] = round(l3, 1)
    legs = [v for v in (l1, l2, l3) if v > 0]
    if legs:
        out["ac_output_voltage_v"] = round(legs[0], 1)
    # Reg 638: inverter frequency = offset 3 + (638-627)*2 = 25.
    ac_hz = u16(bs, 25) / 100.0
    if ac_hz > 0:
        out["ac_output_frequency_hz"] = round(ac_hz, 2)
    # Reg 653: load_power = offset 3 + (653-627)*2 = 55.
    load_w = -s16(bs, 55)
    if load_w != 0:
        out["ac_output_power_w"] = load_w
        out["load_power_w"] = load_w
    return out


def _parse_pv(bs: bytes) -> dict[str, Any]:
    """Reg 672..682, 11 words. Up to four MPPT inputs. The 3P
    inverters reserve room for PV3+PV4 (the bigger 20K+ chassis
    use all four strings); 12-15K commercial usually stop at two."""
    out: dict[str, Any] = {}
    # PV powers at 672-675 are POSITIVE deci-watts (×10), not
    # sign-flipped watts like the 1P variant.
    pv1_w = max(0, s16(bs, 3))  * 10
    pv2_w = max(0, s16(bs, 5))  * 10
    pv3_w = max(0, s16(bs, 7))  * 10
    pv4_w = max(0, s16(bs, 9))  * 10
    if pv1_w > 0: out["pv1_power_w"] = pv1_w
    if pv2_w > 0: out["pv2_power_w"] = pv2_w
    if pv3_w > 0: out["pv3_power_w"] = pv3_w
    if pv4_w > 0: out["pv4_power_w"] = pv4_w
    out["pv_power_w"] = pv1_w + pv2_w + pv3_w + pv4_w
    # PV voltages at 676, 678, 680, 682 (every other reg).
    pv1_v = u16(bs, 11) / 10.0
    pv2_v = u16(bs, 15) / 10.0
    pv3_v = u16(bs, 19) / 10.0
    pv4_v = u16(bs, 23) / 10.0
    if pv1_v > 0: out["pv1_voltage_v"] = round(pv1_v, 1)
    if pv2_v > 0: out["pv2_voltage_v"] = round(pv2_v, 1)
    if pv3_v > 0: out["pv3_voltage_v"] = round(pv3_v, 1)
    if pv4_v > 0: out["pv4_voltage_v"] = round(pv4_v, 1)
    live = [v for v in (pv1_v, pv2_v, pv3_v, pv4_v) if v > 0]
    if live:
        out["pv_voltage_v"] = round(sum(live) / len(live), 1)
    return out


class DeyeInverter3P(DeviceDriver):
    """Deye / Sunsynk / Sol-Ark three-phase driver. Up to four
    MPPT inputs, three-phase grid + AC output. Experimental until
    first real-customer probe paste."""
    vendor_id = "deye"
    device_kind = "inverter_3p"

    @property
    def sections(self) -> tuple[Section, ...]:
        return (
            Section(register=500, word_count=1,
                    parser=_parse_status, name="status",
                    function_code=3),
            Section(register=540, word_count=2,
                    parser=_parse_temps, name="temps",
                    function_code=3),
            Section(register=586, word_count=6,
                    parser=_parse_battery, name="battery",
                    function_code=3),
            Section(register=598, word_count=27,
                    parser=_parse_grid, name="grid",
                    function_code=3),
            Section(register=627, word_count=27,
                    parser=_parse_ac_output, name="ac_output",
                    function_code=3),
            Section(register=672, word_count=11,
                    parser=_parse_pv, name="pv",
                    function_code=3),
        )
