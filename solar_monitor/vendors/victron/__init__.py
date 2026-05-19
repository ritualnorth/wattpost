"""Victron Energy vendor package.

Read-only by design — Victron BLE Instant Readout is broadcast-only,
and our project_victron_scope memory locks in that we won't chase
write capabilities via VE.Direct / Cerbo GX dbus (heavy-Victron
users live in VRM and aren't switching to WattPost).

Currently ships:
  * SmartShunt (BatteryMonitor model) — battery V/A/SoC/time-to-go.
    The single highest-leverage Victron driver; see
    project_target_customer for the "budget upgrader buys a shunt"
    persona this opens.

Coming later, slotting in via the same `ble_victron_advertise`
transport — same code path, just a different driver per device kind:
  * SmartSolar MPPT (SolarCharger model)
  * Orion-Tr Smart DC-DC (DcDcConverter)
  * Smart BatteryProtect, Smart Lithium, BMV / VE.Bus
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .smart_shunt import VictronSmartShunt
from .dcdc import VictronDcDc
from .smart_solar import VictronSmartSolar
from .orion_xs import VictronOrionXS
from .smart_battery_protect import VictronSmartBatteryProtect
from .ac_charger import VictronAcCharger
from .smart_lithium import VictronSmartLithium
from .lynx_smart_bms import VictronLynxSmartBMS
# VE.Direct (wired) drivers, separate kinds so the user can pick
# "Victron SmartShunt over BLE" vs "Victron SmartShunt over VE.Direct"
# in the setup flow when both are possible.
from .ve_direct import (
    VictronVeDirectShunt, VictronVeDirectMppt, VictronVeDirectPhoenix,
)

INFO = VendorInfo(
    id="victron",
    display_name="Victron Energy",
    description=(
        "Read-only via BLE Instant Readout. Covers SmartShunt + BMV "
        "battery monitors, Orion-Tr Smart + Orion XS DC-DC chargers, "
        "SmartSolar MPPTs (every model), Smart BatteryProtect load "
        "disconnects, Blue Smart AC Chargers, Smart Lithium batteries, "
        "and the Lynx Smart BMS. MultiPlus / Phoenix inverters use "
        "VE.Bus and need a different transport — deferred."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "shunt":            VictronSmartShunt,
        "dcdc":             VictronDcDc,
        "dcdc_xs":          VictronOrionXS,
        "charge_controller": VictronSmartSolar,
        "load_disconnect":  VictronSmartBatteryProtect,
        "ac_charger":       VictronAcCharger,
        "smart_battery":    VictronSmartLithium,
        "bms":              VictronLynxSmartBMS,
    },
)


# VE.Direct (wired) is registered as a sibling vendor so users
# distinguish the transport in config.yaml. Output dict shape is
# identical to the BLE drivers above so the dashboard, bank
# aggregation and exporters don't see which path the data came in
# on. See #197.
VEDIRECT_INFO = VendorInfo(
    id="victron_vedirect",
    display_name="Victron (VE.Direct, wired)",
    description=(
        "Wired alternative to BLE Instant Readout. Reads VE.Direct "
        "text frames over a USB-to-TTL adapter on the device's 4-pin "
        "JST port. Use this for metal-van installs where BLE is "
        "unreliable, or when you'd rather not deal with per-device "
        "encryption keys. Read-only. Covers SmartShunt / BMV-7xx "
        "(shunt), SmartSolar MPPT (charge_controller), and the "
        "Phoenix Inverter VE.Direct family (inverter)."
    ),
)

register_vendor(
    info=VEDIRECT_INFO,
    drivers={
        "shunt":             VictronVeDirectShunt,
        "charge_controller": VictronVeDirectMppt,
        "inverter":          VictronVeDirectPhoenix,
    },
)

__all__ = [
    "INFO",
    "VEDIRECT_INFO",
    "VictronSmartShunt", "VictronDcDc", "VictronSmartSolar",
    "VictronOrionXS", "VictronSmartBatteryProtect", "VictronAcCharger",
    "VictronSmartLithium", "VictronLynxSmartBMS",
    "VictronVeDirectShunt", "VictronVeDirectMppt", "VictronVeDirectPhoenix",
]
