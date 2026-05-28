"""Renogy Smart Shunt driver, RBM-S100 / RBM-S300 / RBM-S500.

The Renogy Battery Monitor with Shunt is the budget-upgrade entry
point for "I just want to know my real bank state", single device,
clamps the negative terminal, no BMS needed (#115's "shunt-as-truth"
mode is already wired for it). Persona B in the target-customer
memory: someone buying their first shunt to get visibility.

Protocol: Modbus FC03 over BLE (BT-1 / BT-2 dongle) or direct
RS-485, same transports we already ship for the Rover, the DCC50S,
and the inverter family. Slave ID is typically 0x30 on BT-2 (vs
0xFF on Rover), but the user picks it in the setup wizard's scan
step so we don't hardcode here.

Reference register map: cyril/renogy-bt's `BatteryMonitorClient.py`
plus the public Renogy Modbus PDF that ships with the product.
Marked experimental until at least one paying customer has a unit
verified, discovery telemetry (#129) will flag any field-decoding
mismatches faster than waiting for support tickets.
"""
from __future__ import annotations

from ..base import DeviceDriver, Section
from ._util import bytes_to_int


def _parse_device_info(bs: bytes) -> dict:
    """Registers 12-19 (8 words = 16 ASCII bytes). Shared Renogy
    convention, every product reports its model name in this exact
    block, padded with NUL. The shunt usually identifies as
    `RBM-S100` / `RBM-S300` / `RBM-S500` per the model variant."""
    return {"model": bs[3:19].decode("utf-8", errors="replace").rstrip("\x00").strip()}


def _parse_versions(bs: bytes) -> dict:
    """Registers 20-25 (6 words). Same layout as the Rover:
    sw_version + hw_version + serial, 4 bytes each."""
    if len(bs) < 15:
        return {}
    sw = bs[3:7]
    hw = bs[7:11]
    serial = bs[11:15]
    return {
        "firmware_version": f"{sw[1]}.{sw[2]}.{sw[3]}",
        "hardware_version": f"{hw[1]}.{hw[2]}.{hw[3]}",
        "serial":           serial.hex().upper(),
    }


def _parse_device_address(bs: bytes) -> dict:
    return {"device_id": int(bytes_to_int(bs, 4, 1))}


def _parse_live_stats(bs: bytes) -> dict:
    """Registers 0x0100..0x010D (14 words). The live-state block.

    Per cyril/renogy-bt + the Renogy Modbus PDF:
      word 0   voltage_x10     →  V (uint, /10)
      word 1   current_x100    →  A signed (/100)
      word 2   temperature_x10 →  °C signed (/10)
      word 3   soc             →  % (uint)
      words 4-5 remaining_ah_x1000   → Ah (uint32, /1000)
      words 6-7 full_capacity_ah_x1000 → Ah (uint32, /1000)
      words 8-9 cumulative_discharge_ah_x1000 → Ah (uint32, /1000)
      words 10-11 cumulative_charge_ah_x1000 → Ah (uint32, /1000)
      word 12  time_to_empty_min   → minutes (uint, 0xFFFF = unknown)
      word 13  time_to_full_min    → minutes (uint, 0xFFFF = unknown)

    `power_w` is derived (V × I) so the flow tile + bank power
    aggregation get a value without the dashboard needing a vendor-
    specific code path."""
    voltage_v = bytes_to_int(bs, 3, 2, scale=0.1)
    current_a = bytes_to_int(bs, 5, 2, signed=True, scale=0.01)
    out: dict = {
        "voltage_v":            voltage_v,
        "current_a":            current_a,
        "temperature_c":        bytes_to_int(bs, 7, 2, signed=True, scale=0.1),
        "soc_pct":              int(bytes_to_int(bs, 9, 2)),
        "remaining_ah":         bytes_to_int(bs, 11, 4, scale=0.001),
        "full_capacity_ah":     bytes_to_int(bs, 15, 4, scale=0.001),
        "cumulative_discharge_ah":
                                bytes_to_int(bs, 19, 4, scale=0.001),
        "cumulative_charge_ah":
                                bytes_to_int(bs, 23, 4, scale=0.001),
    }
    # Power: positive = charging, negative = discharging (signed
    # current convention matches the rest of WattPost, Bank
    # aggregation reads `power_w` directly).
    if voltage_v and current_a is not None:
        out["power_w"] = round(voltage_v * current_a, 2)

    # Time-to-go fields: the shunt reports 0xFFFF when it doesn't
    # have a useful estimate (no current draw, or recently reset).
    # Surface those as None so the UI's "—" fallback renders rather
    # than "65535 min".
    tte = int(bytes_to_int(bs, 27, 2))
    ttf = int(bytes_to_int(bs, 29, 2))
    if tte and tte != 0xFFFF:
        out["time_to_empty_min"] = tte
    if ttf and ttf != 0xFFFF:
        out["time_to_full_min"]  = ttf
    return out


class RenogyShunt(DeviceDriver):
    """Renogy Battery Monitor + Shunt (RBM-S100 / S300 / S500).

    Read-only. Persona-B unlock: the customer who's never had real
    visibility into their bank buys this $80 device and gets every
    WattPost tile (hero donut, flow strip, Remaining tile, Battery
    health, runtime forecast) without needing a BMS.
    """
    vendor_id = "renogy"
    device_kind = "shunt"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=12, word_count=8,  parser=_parse_device_info,    name="device_info"),
            Section(register=20, word_count=6,  parser=_parse_versions,       name="versions"),
            Section(register=26, word_count=1,  parser=_parse_device_address, name="device_address"),
            Section(register=0x0100, word_count=14, parser=_parse_live_stats, name="live_stats"),
        ]
