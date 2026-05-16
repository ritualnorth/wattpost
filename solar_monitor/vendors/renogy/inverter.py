"""Renogy pure-sine inverter driver — 1000W / 2000W / 3000W.

Common in van + cabin builds for converting bank DC to mains AC.
Some models are inverter-only; others are inverter/chargers that
also accept AC input and either pass-through or charge the bank
from mains. Register map covers both: AC input + AC output +
charging side + load side.

Reference register map from cyril/renogy-bt's `InverterClient.py`,
validated against real Renogy gear in production at multiple
installers. Modbus FC03 over the same BT-2 / USB-RS485 transports
we already ship — no new transport needed.

Registered under `(vendor=renogy, kind=inverter)`. The dashboard's
device-detail page already renders generic devices via
`buildGenericDetail()`; a dedicated inverter tile (load %, AC IN
vs AC OUT) can come in a follow-up once we have customer data on
which fields actually matter to vanlife / cabin operators.
"""
from __future__ import annotations

from ..base import DeviceDriver, Section
from ._util import bytes_to_int

# Charging-state enum specific to Renogy inverters (the inverter
# charger side). Note these values DIFFER from the Rover's
# CHARGING_STATE — Renogy reused the field name but assigned
# different integers per device class.
INVERTER_CHARGING_STATE = {
    0: "deactivated",
    1: "constant_current",  # bulk
    2: "constant_voltage",  # absorption
    4: "floating",
    6: "battery_activation",
    7: "battery_disconnecting",
}


def _parse_inverter_stats(bs: bytes) -> dict:
    """Register 4000, 10 words — the live AC-side state.
    Volts/amps at 0.1 scale; frequency at 0.01."""
    return {
        "ac_input_voltage_v":    bytes_to_int(bs, 3, 2, scale=0.1),
        "ac_input_current_a":    bytes_to_int(bs, 5, 2, scale=0.01),
        "ac_output_voltage_v":   bytes_to_int(bs, 7, 2, scale=0.1),
        "ac_output_current_a":   bytes_to_int(bs, 9, 2, scale=0.01),
        "ac_output_frequency_hz": bytes_to_int(bs, 11, 2, scale=0.01),
        "battery_voltage_v":     bytes_to_int(bs, 13, 2, scale=0.1),
        "temperature_c":         bytes_to_int(bs, 15, 2, scale=0.1),
        "ac_input_frequency_hz": bytes_to_int(bs, 21, 2, scale=0.01),
    }


def _parse_device_id(bs: bytes) -> dict:
    return {"device_id": int(bytes_to_int(bs, 3, 2))}


def _parse_inverter_model(bs: bytes) -> dict:
    return {"model": bs[3:19].decode("utf-8", errors="replace").rstrip("\x00").strip()}


def _parse_charging_info(bs: bytes) -> dict:
    """Register 4327, 7 words — the integrated MPPT side. Models
    that don't have built-in solar return zeros across this block;
    we surface every field anyway so downstream consumers can
    detect "this inverter has no solar wired up" rather than
    guessing at a missing field."""
    return {
        "battery_percentage":  int(bytes_to_int(bs, 3, 2)),
        "charging_current_a":  bytes_to_int(bs, 5, 2, scale=0.1, signed=True),
        "pv_voltage_v":        bytes_to_int(bs, 7, 2, scale=0.1),
        "pv_current_a":        bytes_to_int(bs, 9, 2, scale=0.1),
        "pv_power_w":          int(bytes_to_int(bs, 11, 2)),
        "charging_state":      INVERTER_CHARGING_STATE.get(
            int(bytes_to_int(bs, 13, 2))
        ),
        "charging_power_w":    int(bytes_to_int(bs, 15, 2)),
    }


def _parse_load_info(bs: bytes) -> dict:
    """Register 4408, 6 words — what the inverter is currently
    pushing to its AC output terminals. `load_percentage` is
    relative to the inverter's nameplate W rating — useful for
    the user to spot "I'm running close to limit, time to turn
    something off."""
    return {
        "load_current_a":          bytes_to_int(bs, 3, 2, scale=0.1),
        "load_active_power_w":     int(bytes_to_int(bs, 5, 2)),
        "load_apparent_power_va":  int(bytes_to_int(bs, 7, 2)),
        "line_charging_current_a": bytes_to_int(bs, 11, 2, scale=0.1),
        "load_percentage":         int(bytes_to_int(bs, 13, 2)),
    }


class RenogyInverter(DeviceDriver):
    """Renogy pure-sine inverter / inverter-charger driver.

    Covers the 1000W / 2000W / 3000W product family. Speaks Modbus
    RTU over BT-2 BLE or USB-RS485; same transports as the Rover.
    """
    vendor_id = "renogy"
    device_kind = "inverter"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=4000, word_count=10, parser=_parse_inverter_stats, name="inverter_stats"),
            Section(register=4109, word_count=1,  parser=_parse_device_id,      name="device_id"),
            Section(register=4311, word_count=8,  parser=_parse_inverter_model, name="model"),
            Section(register=4327, word_count=7,  parser=_parse_charging_info,  name="charging_info"),
            Section(register=4408, word_count=6,  parser=_parse_load_info,      name="load_info"),
        ]
