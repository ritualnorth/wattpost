"""Voltronic-family vendor package (Axpert / MPP Solar / EG4 rebadges).

Single shared ASCII protocol over USB-HID covers a long tail of
rebadges. The driver itself is read-only and marked experimental
until first-customer reports confirm field ordering on each
firmware variant.

Why this vendor matters: every off-grid hybrid inverter Solar
Assistant covers traces back to two protocol families — Voltronic
and Deye. The Voltronic family alone accounts for ~11 of the 45
manufacturers SA lists (Axpert, MPP Solar, EG4, Mecer, RCT,
Infinisolar, Anenji, Datouboss, HZSolar, Effekta, LVTopSun).
Shipping a Voltronic driver closes the largest single coverage
gap with one driver. See `project_solar_assistant_competitive`
for the wider framing.

Protocol: ASCII commands (QPI, QPIRI, QPIGS, QMOD, QPIWS) over
USB-HID. CRC is XMODEM with a byte-substitution quirk on framing
bytes. Decoded in `solar_monitor/transport/usbhid_voltronic.py`;
this package only owns the device-driver shape that maps parsed
fields onto our normalised metric names.
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
        "Read-only over USB-HID — live status, mode, warnings. "
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
