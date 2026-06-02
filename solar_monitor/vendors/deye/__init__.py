"""Deye / Sunsynk / Sol-Ark hybrid inverter vendor package.

Deye is the Chinese OEM; Sunsynk and Sol-Ark are rebrands. Same
Modbus RTU register map across all three brands.

Two variants:

  * `inverter_1p` covers single-phase + split-phase domestic
    (register base 59..194). SUN-5K/8K-SG04LP1, Sunsynk SG01LP1
    3.6-16K, Sol-Ark 5K/8K/12K-1P, Sol-Ark 12K-2P US.

  * `inverter_3p` covers three-phase commercial (register base
    500..689). SUN-12K/15K/20K/25K-SG01HP3, Sunsynk Max-15K/20K,
    Sol-Ark 15K-3P.

Customer picks the variant via `kind:` in config.yaml.

Modbus RTU over RS485, 9600 8N1, slave ID 1. Non-standard RJ45
pinout: pin 1 = RS485-B, pin 2 = RS485-A, pin 3 = GND. Uses the
existing `serial_modbus` transport.

Read-only, experimental.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .inverter_1p import DeyeInverter1P
from .inverter_3p import DeyeInverter3P


INFO = VendorInfo(
    id="deye",
    display_name="Deye / Sunsynk / Sol-Ark (experimental)",
    description=(
        "Hybrid inverter family, three brand names, one Chinese "
        "OEM. Deye, Sunsynk (UK), Sol-Ark (US) all share the same "
        "Modbus RTU register map. Single-phase: SUN-5K/8K-SG04LP1, "
        "Sunsynk 3.6/5.5/8/16K SG01LP1, Sol-Ark 5K/8K/12K-1P. "
        "Three-phase: SUN-12K/15K/20K/25K-SG01HP3, Sunsynk Max-15K/"
        "20K, Sol-Ark 15K-3P. Read-only over USB-RS485. Pick the "
        "right variant in `kind:` (inverter_1p vs inverter_3p)."
    ),
)


register_vendor(
    info=INFO,
    drivers={
        "inverter_1p": DeyeInverter1P,
        "inverter_3p": DeyeInverter3P,
    },
)


__all__ = ["INFO", "DeyeInverter1P", "DeyeInverter3P"]
