#!/usr/bin/env python3
"""Pure-Python verification for the Deye / Sunsynk / Sol-Ark
driver pair (#366).

No hardware required. Builds synthetic Modbus FC03 response
frames for both register-map variants, runs them through the
parsers, and confirms the canonical-metric mapping plus the
known footguns:

  * 1P battery_voltage ÷100 (NOT ÷10)
  * 1P battery_power, PV powers, grid powers — sign-flipped
  * 3P battery_voltage ÷10 (matches every other 3P voltage)
  * 3P battery_power in deci-watts × sign-flipped (×–10)
  * 3P PV powers in positive deci-watts (×10, NOT sign-flipped)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solar_monitor.modbus import crc16
from solar_monitor.vendors.deye._common import label_for_mode
from solar_monitor.vendors.deye.inverter_1p import (
    DeyeInverter1P,
    _parse_battery_and_pv_power, _parse_grid_and_ac,
    _parse_pv_voltages, _parse_status as _parse_status_1p,
    _parse_temps_and_grid_hz,
)
from solar_monitor.vendors.deye.inverter_3p import (
    DeyeInverter3P,
    _parse_ac_output, _parse_battery, _parse_grid, _parse_pv,
    _parse_status as _parse_status_3p, _parse_temps,
)


def fc03_response(slave_id: int, words: list[int]) -> bytes:
    """Build a Modbus RTU FC03 (Read Holding Registers) frame.
    Driver parsers receive the bytes starting with slave + fc +
    bytecount header, so we hand the full frame back."""
    byte_count = len(words) * 2
    header = bytes([slave_id, 0x03, byte_count])
    payload = b"".join(
        (w & 0xFFFF).to_bytes(2, "big") for w in words
    )
    frame = header + payload
    return frame + crc16(frame)


class _T:
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

    # ============================================================
    # 1P fixtures — Sunsynk 5K / Sol-Ark 5K sunny off-grid pattern
    # ============================================================
    print("\n[1P] status parser, mode=2 (line)")
    r = _parse_status_1p(fc03_response(1, [2]))
    ctx.expect(r.get("inverter_mode") == "line", "mode=line")
    ctx.expect(r.get("device_status_code") == 2, "code=2")

    print("\n[1P] temps + grid Hz block (reg 79..91)")
    w = [0] * 13
    w[0]  = 5000           # reg 79: grid frequency 50.00 Hz
    w[1]  = 0; w[2] = 1500 # regs 80-81: lifetime import 150 Wh
    w[3]  = 0; w[4] = 4200 # regs 82-83: lifetime export 420 Wh
    w[11] = 425            # reg 90: dc transformer temp 42.5 °C
    w[12] = 380            # reg 91: radiator temp 38.0 °C
    r = _parse_temps_and_grid_hz(fc03_response(1, w))
    ctx.expect(r.get("grid_frequency_hz") == 50.0, "grid_hz=50.0")
    ctx.expect(r.get("temperature_c") == 42.5, f"inv temp=42.5 (got {r.get('temperature_c')})")
    ctx.expect(r.get("radiator_temperature_c") == 38.0, "radiator=38.0")
    # u32 high-first; we wrote raw = 1500 in the low word → 1500 × 100 Wh = 150 kWh.
    ctx.expect(r.get("lifetime_grid_import_wh") == 150_000, "lifetime import = 150 kWh")

    print("\n[1P] PV voltages (reg 109..111)")
    r = _parse_pv_voltages(fc03_response(1, [1450, 0, 1420]))
    ctx.expect(r.get("pv1_voltage_v") == 145.0, "pv1 V=145.0")
    ctx.expect(r.get("pv2_voltage_v") == 142.0, "pv2 V=142.0")
    ctx.expect(r.get("pv_voltage_v") == 143.5, "pv V mean=143.5")

    print("\n[1P] grid + AC output block (reg 150..175)")
    w = [0] * 26
    w[0]  = 2410   # reg 150: grid_voltage_l1 241.0 V
    w[4]  = 2350   # reg 154: ac_output_voltage 235.0 V
    w[17] = (-1100) & 0xFFFF   # reg 167: load_l1_power, wire says -1100 → flip = +1100
    w[19] = (-500)  & 0xFFFF   # reg 169: ct/grid power, wire -500 → flip +500 (exporting)
    w[25] = (-1080) & 0xFFFF   # reg 175: ac_output_power, wire -1080 → flip +1080
    r = _parse_grid_and_ac(fc03_response(1, w))
    ctx.expect(r.get("grid_voltage_v") == 241.0, f"grid V=241.0 (got {r.get('grid_voltage_v')})")
    ctx.expect(r.get("ac_output_voltage_v") == 235.0, "AC out V=235.0")
    ctx.expect(r.get("load_power_w") == 1100, f"load_w=+1100 (got {r.get('load_power_w')})")
    ctx.expect(r.get("power_to_grid_w") == 500, "exporting 500 W")
    ctx.expect("power_to_user_w" not in r, "not importing simultaneously")
    ctx.expect(r.get("ac_output_power_w") == 1080, "ac_output_power=+1080")

    print("\n[1P] battery + PV power block (reg 182..194) — the footgun block")
    w = [0] * 12
    w[0]  = 220                    # reg 182: battery temp 22.0 °C
    w[1]  = 5420                   # reg 183: battery V — ÷100 → 54.20 V
    w[2]  = 95                     # reg 184: SoC=95
    w[3]  = 0                      # reg 185: reserved
    w[4]  = (-1480) & 0xFFFF       # reg 186: pv1_power, wire -1480 → flip +1480
    w[5]  = (-1520) & 0xFFFF       # reg 187: pv2_power → +1520
    w[6]  = 0; w[7] = 0            # regs 188, 189: reserved
    w[8]  = (-1600) & 0xFFFF       # reg 190: battery_power, wire -1600 → flip +1600 (charging)
    w[9]  = (-2950) & 0xFFFF       # reg 191: battery_current ×-0.01 → +29.50 A
    w[10] = 0                      # reg 192: reserved
    w[11] = 5001                   # reg 193: ac_output_freq ÷100 → 50.01 Hz
    r = _parse_battery_and_pv_power(fc03_response(1, w))
    ctx.expect(r.get("battery_temperature_c") == 22.0, "batt temp=22.0")
    ctx.expect(r.get("battery_voltage_v") == 54.20,
               f"battery_voltage_v÷100=54.20 (got {r.get('battery_voltage_v')})  — Deye 1P footgun")
    ctx.expect(r.get("soc_pct") == 95, "soc=95")
    ctx.expect(r.get("pv1_power_w") == 1480, "pv1_w=+1480 (sign-flip applied)")
    ctx.expect(r.get("pv2_power_w") == 1520, "pv2_w=+1520")
    ctx.expect(r.get("pv_power_w") == 3000, "pv_w sum=3000")
    ctx.expect(r.get("battery_power_w") == 1600,
               f"battery_power_w=+1600 charging (got {r.get('battery_power_w')})")
    ctx.expect(r.get("battery_current_a") == 29.50, "battery_current_a=+29.50")
    ctx.expect(r.get("ac_output_frequency_hz") == 50.01, "ac_hz=50.01")

    print("\n[1P] discharge: wire battery_power = +800 → flipped to -800 (discharging)")
    w[8]  = 800                    # discharging
    w[9]  = 1480                   # +14.80 A on wire → -14.80 A flipped (discharging)
    r = _parse_battery_and_pv_power(fc03_response(1, w))
    ctx.expect(r.get("battery_power_w") == -800,
               f"battery_power_w=-800 discharging (got {r.get('battery_power_w')})")
    ctx.expect(r.get("battery_current_a") == -14.80,
               f"battery_current_a=-14.80 (got {r.get('battery_current_a')})")

    # ============================================================
    # 3P fixtures — Sol-Ark 15K-3P / Sunsynk Max-15K
    # ============================================================
    print("\n[3P] status parser, mode=4 (fault)")
    r = _parse_status_3p(fc03_response(1, [4]))
    ctx.expect(r.get("inverter_mode") == "fault", "mode=fault")

    print("\n[3P] temps (reg 540..541)")
    r = _parse_temps(fc03_response(1, [555, 480]))
    ctx.expect(r.get("temperature_c") == 55.5, "inv temp=55.5")
    ctx.expect(r.get("radiator_temperature_c") == 48.0, "radiator=48.0")

    print("\n[3P] battery block (reg 586..591) — the OTHER footgun block")
    w = [0] * 6
    w[0] = 280                       # reg 586: batt temp 28.0 °C
    w[1] = 4820                      # reg 587: batt V — ÷10 → 482.0 V (HV battery)
    w[2] = 78                        # reg 588: SoC=78
    w[3] = 0                         # reg 589: reserved
    w[4] = (-450) & 0xFFFF           # reg 590: batt_power in DECI-watts, wire -450 ×10 ×-1 → +4500 W (charging)
    w[5] = (-933) & 0xFFFF           # reg 591: batt current ×-0.01 → +9.33 A
    r = _parse_battery(fc03_response(1, w))
    ctx.expect(r.get("battery_voltage_v") == 482.0,
               f"battery_voltage_v÷10=482.0 (got {r.get('battery_voltage_v')}) — HV battery, ÷10 not ÷100")
    ctx.expect(r.get("soc_pct") == 78, "soc=78")
    ctx.expect(r.get("battery_power_w") == 4500,
               f"battery_power_w=+4500 (got {r.get('battery_power_w')}) — deci-watts × sign-flip")
    ctx.expect(r.get("battery_current_a") == 9.33, "batt_current=+9.33")
    ctx.expect(r.get("battery_temperature_c") == 28.0, "batt temp=28.0")

    print("\n[3P] grid block (reg 598..624)")
    w = [0] * 27
    w[0]  = 2415               # L1 voltage 241.5 V
    w[1]  = 2418               # L2 voltage 241.8 V
    w[2]  = 2412               # L3 voltage 241.2 V
    w[11] = 5000               # reg 609: grid freq 50.00 Hz
    w[24] = (-2100) & 0xFFFF   # reg 622: L1 grid power, wire -2100 → flip +2100 (exporting)
    w[25] = (-1900) & 0xFFFF   # reg 623: L2 → +1900
    w[26] = (-2000) & 0xFFFF   # reg 624: L3 → +2000
    r = _parse_grid(fc03_response(1, w))
    ctx.expect(r.get("grid_l1_voltage_v") == 241.5, "L1=241.5")
    ctx.expect(r.get("grid_l2_voltage_v") == 241.8, "L2=241.8")
    ctx.expect(r.get("grid_l3_voltage_v") == 241.2, "L3=241.2")
    ctx.expect(r.get("grid_voltage_v") == 241.5, "canonical grid_v=L1")
    ctx.expect(r.get("grid_frequency_hz") == 50.0, "grid_hz=50.0")
    ctx.expect(r.get("power_to_grid_w") == 6000, "exporting 2100+1900+2000=6000 W")

    print("\n[3P] AC output block (reg 627..653)")
    w = [0] * 27
    w[0]  = 2300; w[1]  = 2302; w[2]  = 2298   # L1/L2/L3 AC output
    w[11] = 5001                                # reg 638: inverter freq
    w[26] = (-3500) & 0xFFFF                    # reg 653: load_power
    r = _parse_ac_output(fc03_response(1, w))
    ctx.expect(r.get("ac_output_l1_voltage_v") == 230.0, "L1=230.0")
    ctx.expect(r.get("ac_output_voltage_v") == 230.0, "canonical = L1")
    ctx.expect(r.get("ac_output_frequency_hz") == 50.01, "ac_hz=50.01")
    ctx.expect(r.get("ac_output_power_w") == 3500, "load=+3500 W")

    print("\n[3P] PV block (reg 672..682) — positive deci-watts, NOT sign-flipped")
    w = [0] * 11
    w[0] = 250            # reg 672: pv1 power, wire 250 ×10 → 2500 W
    w[1] = 280            # reg 673: pv2 → 2800 W
    w[2] = 0              # PV3 not connected
    w[3] = 0              # PV4 not connected
    w[4] = 3850           # reg 676: pv1 V 385.0 V
    w[6] = 3920           # reg 678: pv2 V 392.0 V
    r = _parse_pv(fc03_response(1, w))
    ctx.expect(r.get("pv1_power_w") == 2500,
               f"pv1_w=2500 (got {r.get('pv1_power_w')}) — 3P deci-watts, NOT sign-flipped")
    ctx.expect(r.get("pv2_power_w") == 2800, "pv2_w=2800")
    ctx.expect("pv3_power_w" not in r, "pv3 suppressed when 0")
    ctx.expect("pv4_power_w" not in r, "pv4 suppressed when 0")
    ctx.expect(r.get("pv_power_w") == 5300, "pv_w sum=5300")
    ctx.expect(r.get("pv1_voltage_v") == 385.0, "pv1 V=385.0")
    ctx.expect(r.get("pv2_voltage_v") == 392.0, "pv2 V=392.0")

    # ============================================================
    # Driver-registration smoke checks
    # ============================================================
    print("\n[both] Driver classes register correctly")
    d1 = DeyeInverter1P(slave_id=1, label="deye.1p")
    d3 = DeyeInverter3P(slave_id=1, label="deye.3p")
    ctx.expect(d1.vendor_id == "deye" and d1.device_kind == "inverter_1p", "1P kind=inverter_1p")
    ctx.expect(d3.vendor_id == "deye" and d3.device_kind == "inverter_3p", "3P kind=inverter_3p")
    s1 = [(s.name, s.register, s.word_count, s.function_code) for s in d1.sections]
    s3 = [(s.name, s.register, s.word_count, s.function_code) for s in d3.sections]
    ctx.expect(any(s[0] == "status" and s[1] == 59 for s in s1), "1P status section at reg 59")
    ctx.expect(any(s[0] == "battery_pv_power" and s[1] == 182 for s in s1), "1P battery block at reg 182")
    ctx.expect(any(s[0] == "status" and s[1] == 500 for s in s3), "3P status section at reg 500")
    ctx.expect(any(s[0] == "battery" and s[1] == 586 for s in s3), "3P battery block at reg 586")
    ctx.expect(all(s[3] == 3 for s in s1), "all 1P sections use FC03")
    ctx.expect(all(s[3] == 3 for s in s3), "all 3P sections use FC03")

    # Mode enum sanity — codes should map consistently.
    print("\n[both] Mode enum coverage")
    for code, expected in [(0, "standby"), (1, "selfcheck"), (2, "line"),
                           (3, "line"), (4, "fault"), (5, "selfcheck"),
                           (99, "unknown")]:
        ctx.expect(label_for_mode(code) == expected, f"code {code} → {expected}")

    print(f"\n=== {ctx.ran - ctx.failed}/{ctx.ran} assertions passed, {ctx.failed} failed")
    return 0 if ctx.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
