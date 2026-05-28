"""Voltronic-family vendor package (Axpert / MPP Solar / EG4 rebadges).

ASCII commands (QPI, QPIRI, QPIGS, QMOD, QPIWS) over USB-HID.
XMODEM CRC with a byte-substitution quirk on framing bytes.
Transport is in `solar_monitor/transport/usbhid_voltronic.py`.
Read-only, experimental.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .inverter import VoltronicInverter

INFO = VendorInfo(
    id="voltronic",
    display_name="Voltronic / Axpert / MPP Solar (experimental)",
    description=(
        "Hybrid inverter family covering Axpert (Voltronic), MPP "
        "Solar PIP/LV-MK, EG4 6000XP/6500EX, Mecer, RCT, Infinisolar, "
        "Anenji, Datouboss, HZSolar, Effekta, LVTopSun, PowMr, Easun. "
        "Read-only over USB-HID, live status, mode, warnings. "
        "Marked experimental: firmware variants diverge past QPIGS "
        "column ~17. First-customer reports validate each rebadge."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "inverter": VoltronicInverter,
    },
)

__all__ = ["INFO", "VoltronicInverter"]
