"""AiLi smart shunt vendor package (#204).

Pending real-hardware validation.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .shunt import AiliShunt


INFO = VendorInfo(
    id="aili",
    display_name="AiLi smart shunt",
    description=(
        "Cheap BLE shunt that's the first piece of telemetry many "
        "DIY van builders buy. ~£35-40. 20-byte status frames "
        "stream every ~1 s on the FFE1 notify characteristic; no "
        "command required. Read-only."
    ),
)

register_vendor(info=INFO, drivers={"shunt": AiliShunt})

__all__ = ["INFO", "AiliShunt"]
