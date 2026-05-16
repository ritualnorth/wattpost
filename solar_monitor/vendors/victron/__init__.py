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

__all__ = [
    "INFO",
    "VictronSmartShunt", "VictronDcDc", "VictronSmartSolar",
    "VictronOrionXS", "VictronSmartBatteryProtect", "VictronAcCharger",
    "VictronSmartLithium", "VictronLynxSmartBMS",
]
