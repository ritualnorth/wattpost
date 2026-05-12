"""Renogy vendor package.

Covers Renogy charge controllers (Rover/Wanderer/Adventurer) and Renogy smart
batteries (RBT100LFP12S-G1 and the LFP family). Speaks Modbus RTU over any
transport — BT-1 / BT-2 BLE dongle or direct RS-485.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .smart_battery import RenogySmartBattery
from .rover import RenogyRover

INFO = VendorInfo(
    id="renogy",
    display_name="Renogy",
    description=(
        "Charge controllers (Rover/Wanderer family) and LFP smart batteries. "
        "Speaks Modbus RTU; supports BT-1/BT-2 dongles and direct RS-485."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "charge_controller": RenogyRover,
        "smart_battery": RenogySmartBattery,
    },
)

__all__ = ["INFO", "RenogyRover", "RenogySmartBattery"]
