"""EG4 inverter vendor package.

EG4 XP / kPV / FlexBOSS line, Luxpower protocol. (The
Voltronic-derived EG4 6500EX is in the `voltronic` package.)

Modbus RTU over RS485 on the CT1 RJ45 connector (485A pin 8,
485B pin 7, 9600 8N1, slave ID 1). The 12000XP has split-phase
EPS L1/L2 at registers 127/128 and grid L1/L2 at 193/194;
hybrid models leave those zero.

Read-only, experimental.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .xp import EG4XpInverter


INFO = VendorInfo(
    id="eg4",
    display_name="EG4 XP / kPV (experimental)",
    description=(
        "EG4 hybrid + off-grid inverters in the Luxpower-derived "
        "line: 12000XP, 6000XP, 18kPV, 12kPV, FlexBOSS21/18, and "
        "Luxpower-branded LXP siblings. Read-only over USB-RS485 "
        "Modbus RTU, battery state, PV input (PV1+PV2 summed), "
        "AC + EPS output, grid V/Hz, internal + battery temps, "
        "operating mode. Marked experimental: register addresses "
        "are confirmed across three sources but scale-factor "
        "quirks vary by firmware. (The Voltronic-derived EG4 "
        "6500EX lives in the `voltronic` vendor instead.)"
    ),
)


register_vendor(info=INFO, drivers={"inverter": EG4XpInverter})


__all__ = ["INFO", "EG4XpInverter"]
