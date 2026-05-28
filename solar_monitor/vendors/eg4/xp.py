"""EG4 XP / kPV / FlexBOSS family driver, Luxpower-derived Modbus.

Read-only. Polls Modbus RTU input registers (FC04) over the
inverter's CT1 RJ45 with a standard USB-RS485 dongle. Default
slave ID 1, 9600 8N1.

Register layout (all input registers, FC04, addresses in
decimal, scaling as noted):

    Reg  Field                       Scale   Unit
    ---  --------------------------- ------- -----
      0  device_status (low byte)    enum   ,
      1  pv1_voltage                 ÷10     V
      2  pv2_voltage                 ÷10     V
      4  battery_voltage             ÷10     V
      5  battery_soc (low byte)      1       %
      5  battery_soh (high byte)     1       %
      7  pv1_power                   1       W
      8  pv2_power                   1       W
     10  battery_charge_power        1       W
     11  battery_discharge_power     1       W
     12  grid_voltage_r              ÷10     V
     15  grid_frequency              ÷100    Hz
     16  ac_output_power             1       W
     17  rectifier_power             1       W
     20  eps_voltage (off-grid out)  ÷10     V
     23  eps_frequency               ÷100    Hz
     24  eps_power                   1       W
     26  power_to_grid (export)      1       W
     27  power_to_user (import)      1       W
     64  inverter_temperature        1       °C
     65  radiator_temp_1             1       °C
     66  radiator_temp_2             1       °C
     67  battery_temperature         1       °C
     69  running_time (uint32, LH)   1       s

The 12000XP carries split-phase L1/L2 readings at additional
register positions (127/128 for EPS, 193/194 for grid). Read
in a separate optional section, silently degrades when the
inverter doesn't populate them (every hybrid model leaves
them zero).

Mode enum at register 0's low byte (cross-referenced against
galets/eg4-modbus-monitor's registers-18kpv.yaml, values
documented but field names paraphrased to avoid GPL contamination):

    0x00  Standby
    0x01  Fault
    0x02  Programming / firmware update
    0x04  PV mode (solar through, no charge)
    0x08  PV charging the bank, grid-tied
    0x0C  PV charging + grid feeding loads
    0x10  Battery-assisted + grid
    0x14  PV + battery + grid (peak shaving)
    0x20  AC charging the bank
    0x40  Battery only, off-grid
    0x80  PV only, off-grid (rare; daytime full-bank)
    0x88  PV charging the bank, off-grid (the normal sunny
          off-grid case, what wastral1978's 12000XP runs)
    0xC0  PV + battery, off-grid

Mapped onto WattPost's canonical inverter_mode vocabulary so
the dashboard mode pill renders the same as a Voltronic install.

References (all cross-checked, see NOTICE):

  * EG4 18kPV-12LV Modbus Communication Protocol PDF (EG4
    Electronics, public mirror at eg4electronics.com).
  * joyfulhouse/eg4_web_monitor (MIT), register addresses +
    field names.
  * celsworth/lxp-bridge (MIT), Luxpower-LXP semantics.

Marked experimental at v1. First customer probe paste flips
to stable.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section


log = logging.getLogger(__name__)


def _u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _i16(b: bytes, off: int) -> int:
    v = _u16(b, off)
    return v - 0x10000 if v & 0x8000 else v


def _u32_lh(b: bytes, off: int) -> int:
    """Luxpower / EG4 32-bit fields: low word first, high word
    second. Each 16-bit word is big-endian on the wire, the word
    order is little-endian. Identical convention to EPEVER."""
    low = _u16(b, off)
    high = _u16(b, off + 2)
    return (high << 16) | low


# Device-status enum to canonical inverter_mode label. The Luxpower
# protocol packs sub-states into a single byte so the mapping is
# many-to-few: every "X with grid" goes to "line", every "off-grid"
# goes to "battery", standby/fault stand alone. Anything we don't
# recognise falls through to "unknown" so the dashboard surfaces
# the raw code for diagnostics.
_MODE_LABELS = {
    0x00: "standby",
    0x01: "fault",
    0x02: "programming",
    0x04: "line",       # PV pass-through, grid present
    0x08: "line",       # PV charging, grid present
    0x0C: "line",       # PV charging + grid loads
    0x10: "line",       # battery-assisted + grid
    0x14: "line",       # PV + battery + grid
    0x20: "line",       # AC (grid) charging the bank
    0x40: "battery",    # battery off-grid
    0x80: "battery",    # PV off-grid
    0x88: "battery",    # PV charging off-grid (the common sunny case)
    0xC0: "battery",    # PV + battery off-grid
}


def _parse_block_a(bs: bytes) -> dict[str, Any]:
    """Registers 0..15, 16 words = 32 bytes payload starting at
    response offset 3 (after slave + fc + bytecount header).

    Holds operating mode, both PV strings, bank voltage + SoC,
    PV powers, battery charge/discharge powers, and grid V/Hz.
    """
    out: dict[str, Any] = {}
    # Reg 0: device_status enum in low byte. High byte is
    # vendor-internal (fault code on the hybrid models; spare on
    # the XP off-grid line). Surface the code so support has
    # something to work with on unknown values.
    raw0 = _u16(bs, 3)
    code = raw0 & 0xFF
    out["device_status_code"] = code
    out["inverter_mode_code"] = code
    out["inverter_mode"] = _MODE_LABELS.get(code, "unknown")

    # Regs 1, 2: PV string voltages. EG4 splits PV into two
    # MPPT inputs on the XP family; we sum the powers below
    # so the dashboard shows total PV cleanly.
    pv1_v = _u16(bs, 5) / 10.0
    pv2_v = _u16(bs, 7) / 10.0
    if pv1_v > 0: out["pv1_voltage_v"] = round(pv1_v, 1)
    if pv2_v > 0: out["pv2_voltage_v"] = round(pv2_v, 1)
    # Canonical pv_voltage_v: whichever string is producing. If
    # both are live, average; if only one, use it directly.
    if pv1_v > 0 and pv2_v > 0:
        out["pv_voltage_v"] = round((pv1_v + pv2_v) / 2.0, 1)
    elif pv1_v > 0 or pv2_v > 0:
        out["pv_voltage_v"] = round(max(pv1_v, pv2_v), 1)

    # Reg 3: reserved on hybrid models. Skip.

    # Reg 4: battery voltage.
    batt_v = _u16(bs, 11) / 10.0
    out["battery_voltage_v"] = round(batt_v, 2)

    # Reg 5: SoC (low byte) + SoH (high byte).
    raw5 = _u16(bs, 13)
    out["soc_pct"] = raw5 & 0xFF
    soh = (raw5 >> 8) & 0xFF
    if soh > 0:
        out["soh_pct"] = soh

    # Reg 6: reserved.

    # Regs 7, 8: per-string PV power. Sum into the canonical
    # pv_power_w so the Power Flow Solar node renders without
    # the dashboard caring about string count.
    pv1_w = _u16(bs, 17)
    pv2_w = _u16(bs, 19)
    if pv1_w > 0: out["pv1_power_w"] = pv1_w
    if pv2_w > 0: out["pv2_power_w"] = pv2_w
    out["pv_power_w"] = pv1_w + pv2_w

    # Reg 9: reserved.

    # Regs 10, 11: charge vs discharge power. Only one is
    # non-zero at any moment; the difference is the signed
    # battery_power_w + battery_current_a the bank tile
    # wants. Same shape as the Voltronic driver.
    charge_w = _u16(bs, 23)
    discharge_w = _u16(bs, 25)
    out["battery_charging_power_w"]    = charge_w
    out["battery_discharging_power_w"] = discharge_w
    net_w = charge_w - discharge_w
    out["battery_power_w"] = net_w
    if batt_v > 0:
        out["battery_current_a"] = round(net_w / batt_v, 2)

    # Reg 12: grid_voltage_r (the R phase on hybrid; or just
    # "grid" on split-phase XP). Three-phase customers would
    # also want regs 13/14, out of scope for v1.
    grid_v = _u16(bs, 27) / 10.0
    if grid_v > 0:
        out["grid_voltage_v"] = round(grid_v, 1)

    # Reg 15: grid frequency, ÷100.
    grid_hz = _u16(bs, 33) / 100.0
    if grid_hz > 0:
        out["grid_frequency_hz"] = round(grid_hz, 2)
    return out


def _parse_block_b(bs: bytes) -> dict[str, Any]:
    """Registers 16..27, 12 words. AC output, EPS output (the
    off-grid side), and grid sell/import meters."""
    out: dict[str, Any] = {}
    # Reg 16: ac_output_power, inverter output across all phases.
    # On a grid-tied install this is what the inverter pushes
    # through to the AC bus; on off-grid it'll typically match eps_power.
    out["ac_output_power_w"] = _u16(bs, 3)
    # Reg 17: rectifier_power, grid->loads pass-through, only
    # meaningful when grid is present.
    rect_w = _u16(bs, 5)
    if rect_w > 0:
        out["rectifier_power_w"] = rect_w
    # Regs 18-19: reserved.

    # Reg 20: EPS voltage (off-grid output bus). On the 12000XP
    # this is the L1 leg's voltage; L2 lives in reg 128.
    eps_v = _u16(bs, 11) / 10.0
    if eps_v > 0:
        out["ac_output_voltage_v"] = round(eps_v, 1)
        out["eps_voltage_v"] = round(eps_v, 1)
    # Regs 21-22: reserved.
    # Reg 23: EPS frequency.
    eps_hz = _u16(bs, 17) / 100.0
    if eps_hz > 0:
        out["ac_output_frequency_hz"] = round(eps_hz, 2)
    # Reg 24: EPS power, the off-grid load power.
    eps_w = _u16(bs, 19)
    if eps_w > 0:
        out["eps_power_w"] = eps_w
    # Reg 25: reserved.
    # Reg 26: power to grid (export). Hybrid models only.
    to_grid = _u16(bs, 23)
    if to_grid > 0:
        out["power_to_grid_w"] = to_grid
    # Reg 27: power to user (grid import).
    to_user = _u16(bs, 25)
    if to_user > 0:
        out["power_to_user_w"] = to_user
    return out


def _parse_temps(bs: bytes) -> dict[str, Any]:
    """Registers 64..70, 7 words. Internal temp, two radiator
    temps, battery temp, and a 32-bit running time pair."""
    out: dict[str, Any] = {}
    inv_c = _i16(bs, 3)
    out["temperature_c"] = inv_c
    # Reg 65, 66: radiator temps. Some firmwares ship 0 on both
    # when the inverter is idle; surface only when meaningful.
    r1 = _i16(bs, 5)
    r2 = _i16(bs, 7)
    if r1 != 0: out["radiator_temperature_1_c"] = r1
    if r2 != 0: out["radiator_temperature_2_c"] = r2
    # Reg 67: battery temperature. The known firmware-quirk
    # field, some Luxpower revs ship this scaled ÷10. We
    # publish the raw value; the validation step on first
    # customer poll catches an out-of-range reading and the
    # follow-up release applies a per-firmware scale.
    batt_c = _i16(bs, 9)
    out["battery_temperature_c"] = batt_c
    # Reg 68: reserved.
    # Regs 69-70: running time in seconds, uint32 low-high.
    try:
        rt = _u32_lh(bs, 13)
        if rt > 0:
            out["running_time_s"] = rt
    except IndexError:
        pass
    return out


def _parse_split_phase(bs: bytes) -> dict[str, Any]:
    """Optional 12000XP / 6000XP split-phase section. Hybrid kPV
    models return zeros from these registers, which we filter out
    so the dashboard doesn't render misleading "0 V" tiles for
    legs that don't physically exist on a single-phase install."""
    out: dict[str, Any] = {}
    # Block read covers regs 127..128 (EPS L1, EPS L2). We pull
    # 2 words.
    l1 = _u16(bs, 3) / 10.0
    l2 = _u16(bs, 5) / 10.0
    if l1 > 0: out["eps_l1_voltage_v"] = round(l1, 1)
    if l2 > 0: out["eps_l2_voltage_v"] = round(l2, 1)
    return out


class EG4XpInverter(DeviceDriver):
    """EG4 XP / kPV / FlexBOSS family driver. Luxpower Modbus,
    read-only, experimental until a real-hardware customer
    confirms firmware-specific scale factors."""
    vendor_id = "eg4"
    device_kind = "inverter"

    @property
    def sections(self) -> tuple[Section, ...]:
        return (
            # Mode + PV + battery + grid V/Hz. 0..15, 16 words.
            Section(register=0, word_count=16,
                    parser=_parse_block_a, name="block_a",
                    function_code=4),
            # AC output + EPS + grid sell/import. 16..27, 12 words.
            Section(register=16, word_count=12,
                    parser=_parse_block_b, name="block_b",
                    function_code=4),
            # Temps + running time. 64..70, 7 words.
            Section(register=64, word_count=7,
                    parser=_parse_temps, name="temps",
                    function_code=4),
            # Split-phase EPS legs (12000XP / 6000XP only). The
            # base class merges _errors so a hybrid model that
            # NAKs this section keeps the rest of the snapshot
            # clean.
            Section(register=127, word_count=2,
                    parser=_parse_split_phase, name="split_phase",
                    function_code=4),
        )
