"""JBD / Overkill Solar BMS vendor package (#201).

Pending real-hardware validation: ships from public protocol
docs + community reverse engineering (Overkill Solar's open
client, syssi's ESPHome JBD module). First customer report
becomes the real-world confirmation. See
[[project-no-victron-lab-purchases]] for the validation
discipline this follows.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .bms import JbdBms


INFO = VendorInfo(
    id="jbd",
    display_name="JBD / Overkill Solar BMS",
    description=(
        "Jiabaida BMS protocol family. Covers Overkill Solar's "
        "rebadged units in the US, plus the BMS inside most cheap "
        "LFP packs sold under Battle Born, LiTime, Power Queen, "
        "Eco-Worthy and other rebrand labels. Read-only at v1. "
        "BLE GATT — service UUID 0xFF00, request via 0xFF02, "
        "notifications on 0xFF01. The bms transport polls commands "
        "0x03 (basic info) and 0x04 (cell voltages) on a ~1 s "
        "cadence."
    ),
)


register_vendor(info=INFO, drivers={"bms": JbdBms})


__all__ = ["INFO", "JbdBms"]
