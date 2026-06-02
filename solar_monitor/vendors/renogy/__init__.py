"""Renogy vendor package.

Covers Renogy charge controllers (Rover/Wanderer/Adventurer) and Renogy smart
batteries (RBT100LFP12S-G1 and the LFP family). Speaks Modbus RTU over any
transport, BT-1 / BT-2 BLE dongle or direct RS-485.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .smart_battery import RenogySmartBattery
from .rover import RenogyRover
from .dcc50s import RenogyDcc
from .inverter import RenogyInverter
from .shunt import RenogyShunt

INFO = VendorInfo(
    id="renogy",
    display_name="Renogy",
    description=(
        "Full Renogy product line over Modbus RTU: Rover/Wanderer/"
        "Adventurer/Voyager charge controllers, LFP smart batteries, "
        "DCC50S/DCC30S DC-DC + MPPT combo chargers (the van-build "
        "staple), 1000W/2000W/3000W pure-sine inverter/chargers, and "
        "the RBM-S100/300/500 Battery Monitor with Shunt. Works over "
        "BT-1/BT-2 dongles + direct USB-RS485."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "charge_controller": RenogyRover,
        "smart_battery":     RenogySmartBattery,
        "dcdc":              RenogyDcc,
        "inverter":          RenogyInverter,
        "shunt":             RenogyShunt,
    },
)

__all__ = [
    "INFO",
    "RenogyRover", "RenogySmartBattery", "RenogyDcc", "RenogyInverter",
    "RenogyShunt",
]
