"""Daly Smart BMS vendor package (#202).

Pending real-hardware validation. Ships from public protocol
docs + dalybms community projects. See
[[project-no-victron-lab-purchases]].
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .bms import DalyBms


INFO = VendorInfo(
    id="daly",
    display_name="Daly Smart BMS",
    description=(
        "Dongguan Daly Electronics BMS family. Common in budget LFP "
        "packs (sub-£500 100Ah class). BLE GATT, write to 0xFFF2, "
        "notifications on 0xFFF1. Read-only at v1. Frame layout is "
        "13 bytes fixed (header 0xA5 + addr + cmd + 8 data + cks)."
    ),
)

register_vendor(info=INFO, drivers={"bms": DalyBms})

__all__ = ["INFO", "DalyBms"]
