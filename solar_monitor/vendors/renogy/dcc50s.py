"""Renogy DCC50S / DCC30S driver — DC-DC + MPPT combo charger.

The DCC50S (50A) and DCC30S (30A) are dual-input chargers: they take
both an alternator feed AND a solar feed, and intelligently route
both to the house battery. Very popular in van builds — one device
replaces a separate DC-DC charger + a separate MPPT.

Protocol: Modbus RTU over Renogy BT-2 / USB-RS485 (FC03 reads). Same
transport as the Rover; only the register layout differs. Reference
register map from cyril/renogy-bt's `DCChargerClient.py`.

Vs the Rover (charge_controller):
  * Same SoC + battery side (V/A/temp/SoC).
  * Adds alternator_voltage/current/power readings (the engine feed).
  * pv_* fields are still there — DCC50S has its own MPPT built in.
  * No load output (it's a charger, not a controller with an L
    terminal) — load_status is absent here.

For #119 coverage roadmap: this is the second DC-DC driver after
Victron Orion-Tr (#124). The Renogy DCC50S takes a different shape
from Victron's because it integrates solar — our driver normalises
both alternator + PV into the same field names the dashboard
already understands.
"""
from __future__ import annotations

from ..base import DeviceDriver, Section
from ._util import bytes_to_int, parse_byte_temperature_c

# Charging state byte. The DCC50S adds value 8 ("alternator direct")
# which the Rover doesn't have — that's the explicit "engine running,
# alternator is sole charge source" state.
CHARGING_STATE = {
    0: "deactivated", 1: "activated", 2: "mppt", 3: "equalizing",
    4: "boost", 5: "floating", 6: "current_limiting",
    8: "alternator_direct",
}

BATTERY_TYPE = {
    1: "open", 2: "sealed", 3: "gel", 4: "lithium", 5: "custom",
}


def _parse_device_info(bs: bytes) -> dict:
    return {"model": bs[3:19].decode("utf-8", errors="replace").strip()}


def _parse_device_address(bs: bytes) -> dict:
    return {"device_id": int(bytes_to_int(bs, 4, 1))}


def _parse_charging_info(bs: bytes) -> dict:
    """Big section at register 256, 30 words. Carries everything the
    dashboard cares about for a charging device — battery state +
    alternator side + PV side + daily/lifetime totals."""
    return {
        # Battery side — what's actually going INTO the house bank.
        "battery_percentage":         int(bytes_to_int(bs, 3, 2)),
        "battery_voltage_v":          bytes_to_int(bs, 5, 2, scale=0.1),
        "battery_current_a":          bytes_to_int(bs, 7, 2, scale=0.01),
        "controller_temperature_c":   parse_byte_temperature_c(int(bytes_to_int(bs, 9, 1))),
        "battery_temperature_c":      parse_byte_temperature_c(int(bytes_to_int(bs, 10, 1))),
        # Alternator side — the engine feed. Zero when engine's off
        # (DCC50S detects ignition automatically and idles).
        "alternator_voltage_v":       bytes_to_int(bs, 11, 2, scale=0.1),
        "alternator_current_a":       bytes_to_int(bs, 13, 2, scale=0.01),
        "alternator_power_w":         int(bytes_to_int(bs, 15, 2)),
        # Solar side — the built-in MPPT. Zero when no sun / no panels.
        "pv_voltage_v":               bytes_to_int(bs, 17, 2, scale=0.1),
        "pv_current_a":               bytes_to_int(bs, 19, 2, scale=0.01),
        "pv_power_w":                 int(bytes_to_int(bs, 21, 2)),
        # Daily extremes + counters. The "battery_max_current_today"
        # combines BOTH input sources at peak — not just PV like on
        # the Rover.
        "battery_min_voltage_today_v":  bytes_to_int(bs, 25, 2, scale=0.1),
        "battery_max_voltage_today_v":  bytes_to_int(bs, 27, 2, scale=0.1),
        "battery_max_current_today_a":  bytes_to_int(bs, 29, 2, scale=0.01),
        "max_charging_power_today_w":   int(bytes_to_int(bs, 33, 2)),
        "charging_ah_today":            int(bytes_to_int(bs, 37, 2)),
        "energy_today_wh":              int(bytes_to_int(bs, 41, 2)),
        # Lifetime — useful for the battery-cycle tile (#109) once
        # that lands.
        "total_working_days":           int(bytes_to_int(bs, 45, 2)),
        "count_battery_overdischarged": int(bytes_to_int(bs, 47, 2)),
        "count_battery_fully_charged":  int(bytes_to_int(bs, 49, 2)),
        "battery_ah_total":             int(bytes_to_int(bs, 51, 4)),
        "energy_total_wh":              int(bytes_to_int(bs, 59, 4)),
    }


def _parse_state(bs: bytes) -> dict:
    """3-word section at register 288. Charging-mode enum + 16 bits
    of alarm flags across two registers. We surface the human-
    readable charging_state plus a single `error` field set to the
    first active alarm (matches cyril/renogy-bt's UX choice — a
    multi-alarm tile is overkill; the first one is usually the
    root cause)."""
    out: dict = {}
    state_byte = int(bytes_to_int(bs, 2, 1))
    out["charging_state"] = CHARGING_STATE.get(state_byte)
    # Alarms split across two register pairs. Bit positions per
    # cyril/renogy-bt's DCChargerClient.py. We try to flatten to a
    # single `error` string — first active alarm wins.
    alarms: dict[str, int] = {}
    byte1 = int(bytes_to_int(bs, 4, 1))
    alarms["low_temp_shutdown"]          = (byte1 >> 11) & 1
    alarms["bms_overcharge_protection"]  = (byte1 >> 10) & 1
    alarms["starter_reverse_polarity"]   = (byte1 >> 9)  & 1
    alarms["alternator_over_voltage"]    = (byte1 >> 8)  & 1
    alarms["alternator_over_current"]    = (byte1 >> 4)  & 1
    alarms["controller_over_temp_2"]     = (byte1 >> 3)  & 1
    byte2 = int(bytes_to_int(bs, 6, 1))
    alarms["solar_reverse_polarity"]     = (byte2 >> 12) & 1
    alarms["solar_over_voltage"]         = (byte2 >> 9)  & 1
    alarms["solar_over_current"]         = (byte2 >> 7)  & 1
    alarms["battery_over_temperature"]   = (byte2 >> 6)  & 1
    alarms["controller_over_temp"]       = (byte2 >> 5)  & 1
    alarms["battery_low_voltage"]        = (byte2 >> 2)  & 1
    alarms["battery_over_voltage"]       = (byte2 >> 1)  & 1
    alarms["battery_over_discharge"]     = (byte2 >> 0)  & 1
    first_active = next((k for k, v in alarms.items() if v), None)
    if first_active is not None:
        out["error"] = first_active
    return out


def _parse_battery_type(bs: bytes) -> dict:
    return {"battery_type": BATTERY_TYPE.get(int(bytes_to_int(bs, 3, 2)))}


class RenogyDcc(DeviceDriver):
    """Renogy DCC50S / DCC30S DC-DC + MPPT combo charger."""
    vendor_id = "renogy"
    device_kind = "dcdc"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=12,    word_count=8,  parser=_parse_device_info,    name="device_info"),
            Section(register=26,    word_count=1,  parser=_parse_device_address, name="device_address"),
            Section(register=256,   word_count=30, parser=_parse_charging_info,  name="charging_info"),
            Section(register=288,   word_count=3,  parser=_parse_state,          name="state"),
            Section(register=57348, word_count=1,  parser=_parse_battery_type,   name="battery_type"),
        ]
