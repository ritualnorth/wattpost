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

INFO = VendorInfo(
    id="victron",
    display_name="Victron Energy",
    description=(
        "Read-only via BLE Instant Readout. Currently supports the "
        "SmartShunt battery monitor; SmartSolar and others to follow."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "shunt": VictronSmartShunt,
    },
)

__all__ = ["INFO", "VictronSmartShunt"]
