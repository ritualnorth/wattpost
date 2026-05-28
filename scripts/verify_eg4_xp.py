#!/usr/bin/env python3
"""Pure-Python verification for the EG4 XP-family driver (#364).

No hardware required. Builds synthetic Modbus FC04 response
frames matching the Luxpower register layout, runs them
through the parser, and confirms canonical-metric mapping.

When wastral1978 (or another customer) sends a probe paste,
add their raw response payload here as a fixture so the
firmware-quirk catches survive regression.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.modbus import crc16
from solar_monitor.vendors.eg4.xp import (
    _MODE_LABELS, _parse_block_a, _parse_block_b, _parse_split_phase,
    _parse_temps, EG4XpInverter,
)


def build_fc04_response(slave_id: int, words: list[int]) -> bytes:
    """Build a Modbus RTU FC04 response frame:
      slave + 0x04 + byte_count + payload (BE 16-bit words) + CRC16.
    Returns just the payload bytes (slave + fc + bytecount + words)
    matching how `Transport.request()` hands the frame to parsers."""
    byte_count = len(words) * 2
    header = bytes([slave_id, 0x04, byte_count])
    payload = b"".join(w.to_bytes(2, "big", signed=False) if w >= 0
                       else (w & 0xFFFF).to_bytes(2, "big") for w in words)
    frame = header + payload
    return frame + crc16(frame)


def words_block_a_sunny_offgrid() -> list[int]:
    """A 12000XP running sunny off-grid: mode 0x88, both PV strings
    producing ~1.5 kW each, charging the bank at +30 A."""
    w = [0] * 16
    w[0]  = 0x0088               # device_status low byte: PV charging off-grid
    w[1]  = 1450                 # pv1 voltage: 145.0 V
    w[2]  = 1420                 # pv2 voltage: 142.0 V
    w[3]  = 0
    w[4]  = 546                  # battery voltage: 54.6 V
    w[5]  = (98 << 8) | 95       # SoH=98, SoC=95
    w[6]  = 0
    w[7]  = 1480                 # pv1_power
    w[8]  = 1520                 # pv2_power
    w[9]  = 0
    w[10] = 1620                 # battery_charging_power
    w[11] = 0                    # battery_discharging_power
    w[12] = 0                    # grid V (off-grid)
    w[13] = 0
    w[14] = 0
    w[15] = 0                    # grid Hz (off-grid)
    return w


def words_block_b_sunny_offgrid() -> list[int]:
    """Reg 16..27. AC output ~1100 W, EPS 240 V split-phase."""
    w = [0] * 12
    w[0]  = 1100                 # ac_output_power (= reg 16)
    w[1]  = 0                    # rectifier_power
    # 18, 19 reserved
    w[4]  = 2400                 # eps_voltage: 240.0 V (= reg 20)
    # 21, 22 reserved
    w[7]  = 6000                 # eps_frequency: 60.00 Hz
    w[8]  = 1100                 # eps_power
    # 25 reserved
    w[10] = 0                    # power_to_grid (off-grid)
    w[11] = 0                    # power_to_user
    return w


def words_temps() -> list[int]:
    """Reg 64..70. Internal 42 °C, battery 28 °C, running 3.5h."""
    w = [0] * 7
    w[0] = 42                    # inverter_temperature (reg 64)
    w[1] = 38                    # radiator_temp_1
    w[2] = 41                    # radiator_temp_2
    w[3] = 28                    # battery_temperature
    # reg 68 reserved
    # regs 69-70: running_time uint32 low-high. 12600 s.
    w[5] = 12600 & 0xFFFF        # low word
    w[6] = (12600 >> 16) & 0xFFFF  # high word
    return w


def words_split_phase() -> list[int]:
    """Reg 127..128: split-phase EPS L1/L2 on the 12000XP."""
    return [2400, 2398]          # L1 240.0 V, L2 239.8 V


class _T:
    """Tiny test-context that prints PASS/FAIL inline."""
    def __init__(self) -> None:
        self.ran = 0
        self.failed = 0

    def expect(self, ok: bool, msg: str) -> None:
        self.ran += 1
        tag = "PASS" if ok else "FAIL"
        print(f"  {tag}  {msg}")
        if not ok:
            self.failed += 1


def main() -> int:
    ctx = _T()

    print("\n[case] block_a parser, sunny off-grid 12000XP")
    frame_a = build_fc04_response(1, words_block_a_sunny_offgrid())
    r = _parse_block_a(frame_a)
    ctx.expect(r.get("inverter_mode") == "battery", f"mode=battery (got {r.get('inverter_mode')})")
    ctx.expect(r.get("device_status_code") == 0x88, f"status_code=0x88 (got {r.get('device_status_code')})")
    ctx.expect(r.get("soc_pct") == 95, f"soc=95 (got {r.get('soc_pct')})")
    ctx.expect(r.get("soh_pct") == 98, f"soh=98 (got {r.get('soh_pct')})")
    ctx.expect(r.get("battery_voltage_v") == 54.6, f"batt V=54.6 (got {r.get('battery_voltage_v')})")
    ctx.expect(r.get("pv1_voltage_v") == 145.0, f"pv1 V=145.0 (got {r.get('pv1_voltage_v')})")
    ctx.expect(r.get("pv2_voltage_v") == 142.0, f"pv2 V=142.0 (got {r.get('pv2_voltage_v')})")
    ctx.expect(r.get("pv_voltage_v") == 143.5, f"pv V mean=143.5 (got {r.get('pv_voltage_v')})")
    ctx.expect(r.get("pv1_power_w") == 1480, f"pv1 W=1480 (got {r.get('pv1_power_w')})")
    ctx.expect(r.get("pv2_power_w") == 1520, f"pv2 W=1520 (got {r.get('pv2_power_w')})")
    ctx.expect(r.get("pv_power_w") == 3000, f"pv W sum=3000 (got {r.get('pv_power_w')})")
    ctx.expect(r.get("battery_charging_power_w") == 1620, "charging W=1620")
    ctx.expect(r.get("battery_discharging_power_w") == 0, "discharging W=0")
    ctx.expect(r.get("battery_power_w") == 1620, "battery_power_w=+1620 (charging)")
    ctx.expect(r.get("battery_current_a") is not None and abs(r["battery_current_a"] - 29.67) < 0.05,
               f"battery_current_a≈+29.7 (got {r.get('battery_current_a')})")
    ctx.expect("grid_voltage_v" not in r, "no grid_voltage_v when off-grid")
    ctx.expect("grid_frequency_hz" not in r, "no grid_frequency_hz when off-grid")

    print("\n[case] block_b parser, sunny off-grid 12000XP")
    frame_b = build_fc04_response(1, words_block_b_sunny_offgrid())
    r = _parse_block_b(frame_b)
    ctx.expect(r.get("ac_output_power_w") == 1100, f"ac_output W=1100 (got {r.get('ac_output_power_w')})")
    ctx.expect(r.get("ac_output_voltage_v") == 240.0, f"ac_output V=240.0 (got {r.get('ac_output_voltage_v')})")
    ctx.expect(r.get("ac_output_frequency_hz") == 60.0, f"ac_output Hz=60.0 (got {r.get('ac_output_frequency_hz')})")
    ctx.expect(r.get("eps_power_w") == 1100, "eps W=1100")
    ctx.expect("power_to_grid_w" not in r, "no grid export when off-grid")
    ctx.expect("power_to_user_w" not in r, "no grid import when off-grid")

    print("\n[case] temps parser, normal idle")
    frame_t = build_fc04_response(1, words_temps())
    r = _parse_temps(frame_t)
    ctx.expect(r.get("temperature_c") == 42, f"inverter temp=42 (got {r.get('temperature_c')})")
    ctx.expect(r.get("radiator_temperature_1_c") == 38, "radiator 1=38")
    ctx.expect(r.get("radiator_temperature_2_c") == 41, "radiator 2=41")
    ctx.expect(r.get("battery_temperature_c") == 28, "battery temp=28")
    ctx.expect(r.get("running_time_s") == 12600, f"running time=12600 (got {r.get('running_time_s')})")

    print("\n[case] split_phase parser, 12000XP only")
    frame_sp = build_fc04_response(1, words_split_phase())
    r = _parse_split_phase(frame_sp)
    ctx.expect(r.get("eps_l1_voltage_v") == 240.0, f"L1 V=240.0 (got {r.get('eps_l1_voltage_v')})")
    ctx.expect(r.get("eps_l2_voltage_v") == 239.8, f"L2 V=239.8 (got {r.get('eps_l2_voltage_v')})")

    print("\n[case] split-phase parser, hybrid model with zeros (should be empty)")
    frame_sp_zero = build_fc04_response(1, [0, 0])
    r = _parse_split_phase(frame_sp_zero)
    ctx.expect("eps_l1_voltage_v" not in r, "L1 absent on zeros")
    ctx.expect("eps_l2_voltage_v" not in r, "L2 absent on zeros")

    print("\n[case] block_a, grid-tied PV+battery+grid (mode 0x14)")
    w = words_block_a_sunny_offgrid()
    w[0]  = 0x0014               # PV + battery + grid
    w[12] = 2415                 # grid 241.5 V
    w[15] = 5000                 # 50.00 Hz
    frame = build_fc04_response(1, w)
    r = _parse_block_a(frame)
    ctx.expect(r.get("inverter_mode") == "line", f"mode=line (got {r.get('inverter_mode')})")
    ctx.expect(r.get("grid_voltage_v") == 241.5, "grid V populated")
    ctx.expect(r.get("grid_frequency_hz") == 50.0, "grid Hz populated")

    print("\n[case] block_a, discharging (mode 0x40)")
    w = words_block_a_sunny_offgrid()
    w[0]  = 0x0040               # battery off-grid
    w[7]  = 0                    # PV gone
    w[8]  = 0
    w[10] = 0                    # not charging
    w[11] = 850                  # discharging 850 W
    frame = build_fc04_response(1, w)
    r = _parse_block_a(frame)
    ctx.expect(r.get("inverter_mode") == "battery", "mode=battery")
    ctx.expect(r.get("battery_power_w") == -850, f"net W=-850 (got {r.get('battery_power_w')})")
    ctx.expect(r.get("battery_current_a") is not None and r["battery_current_a"] < 0,
               "battery_current_a negative when discharging")

    print("\n[case] block_a, unknown mode code → 'unknown'")
    w = words_block_a_sunny_offgrid()
    w[0] = 0x00FF                # garbage status
    frame = build_fc04_response(1, w)
    r = _parse_block_a(frame)
    ctx.expect(r.get("inverter_mode") == "unknown", "unknown mode passes through")
    ctx.expect(r.get("inverter_mode_code") == 0xFF, "raw code preserved for diagnostics")

    print("\n[case] driver registration: vendor 'eg4' kind 'inverter'")
    drv = EG4XpInverter(slave_id=1, label="eg4.inverter.1")
    ctx.expect(drv.vendor_id == "eg4", "vendor_id=eg4")
    ctx.expect(drv.device_kind == "inverter", "device_kind=inverter")
    section_specs = [(s.name, s.register, s.word_count, s.function_code) for s in drv.sections]
    ctx.expect(("block_a", 0, 16, 4) in section_specs, "block_a section")
    ctx.expect(("block_b", 16, 12, 4) in section_specs, "block_b section")
    ctx.expect(("temps", 64, 7, 4) in section_specs, "temps section")
    ctx.expect(("split_phase", 127, 2, 4) in section_specs, "split_phase section")

    print(f"\n=== {ctx.ran - ctx.failed}/{ctx.ran} passed, {ctx.failed} failed")
    return 0 if ctx.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
