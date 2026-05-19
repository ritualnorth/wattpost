"""Junctek KH-F / KG-F shunt vendor package (#205).

Pending real-hardware validation. See [[project-no-victron-lab-purchases]].
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .shunt import JunctekShunt


INFO = VendorInfo(
    id="junctek",
    display_name="Junctek shunt (KH-F / KG-F)",
    description=(
        "ASCII-protocol BLE shunt — second-most-common cheap shunt "
        "after AiLi. Reads from r50 (V/I/capacity), r51 "
        "(temperature + cumulative counters), r53 (SoC + TTG + W) "
        "merged into one shunt-shaped output. Read-only."
    ),
)

register_vendor(info=INFO, drivers={"shunt": JunctekShunt})

__all__ = ["INFO", "JunctekShunt"]
