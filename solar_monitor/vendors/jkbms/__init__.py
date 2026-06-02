"""JK BMS (JiKong) vendor package.

Covers JK04 (legacy), JK02 24-cell, and JK02 32-cell. Protocol
version is detected from a magic byte in each frame.

BLE GATT, service 0xFFE0 / char 0xFFE1. Transport is in
`solar_monitor/transport/ble_jkbms.py`.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .bms import JkBms

INFO = VendorInfo(
    id="jkbms",
    display_name="JK BMS (JiKong)",
    description=(
        "Battery Management Systems for LFP cells, JK02-24S, "
        "JK02-32S, and JK04 protocol versions, all auto-detected. "
        "Read-only; per-cell voltages, current, SoC, temps, MOS "
        "state, alarms, cycle count. The DIY LFP crowd's default "
        "BMS."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "bms": JkBms,
    },
)

__all__ = ["INFO", "JkBms"]
