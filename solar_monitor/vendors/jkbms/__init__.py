"""JK BMS (JiKong) vendor package.

Covers JK's full BMS range across all three protocol generations:
JK04 (legacy), JK02 24-cell, and JK02 32-cell (modern). Each frame
identifies its own protocol version by a magic byte; the driver
auto-detects and applies the right parser.

Why this matters: JK is the dominant BMS choice in the DIY LFP
crowd (16x EVE 280Ah builds, 48V house banks, vanlife). Adding
support brings that whole segment into WattPost's addressable
market. See [[project-target-customer]] for the Persona A/B
context, [[project-coverage-commitment]] for why driver count is
the moat.

Protocol: BLE GATT, service 0xFFE0 / char 0xFFE1. Decoded in
`solar_monitor/transport/ble_jkbms.py`; this package only owns the
device-driver shape that maps raw frames onto our normalised
field names.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .bms import JkBms

INFO = VendorInfo(
    id="jkbms",
    display_name="JK BMS (JiKong)",
    description=(
        "Battery Management Systems for LFP cells — JK02-24S, "
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
