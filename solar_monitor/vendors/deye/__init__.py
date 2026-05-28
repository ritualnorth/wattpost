"""Deye / Sunsynk / Sol-Ark hybrid inverter vendor package (#359).

Three brand names, one OEM. Deye is the Chinese manufacturer;
Sunsynk is the UK rebrand; Sol-Ark is the US rebrand. They share
the same Modbus RTU register map on the same RS485 RJ45 port —
one driver family covers all three brands.

The register map splits into two variants by chassis size:

  * Single-phase / split-phase (5–16 kW domestic) — register
    base around 59..194. SUN-5K/8K-SG04LP1, Sunsynk 3.6/5.5/8/16K
    SG01LP1, Sol-Ark 5K/8K/12K-1P, Sol-Ark 12K-2P (US split-phase).
    `deye.inverter_1p` driver.

  * Three-phase (12–25 kW commercial) — completely different
    register base around 500..689. SUN-12K/15K/20K/25K-SG01HP3,
    Sunsynk Max-15K/20K, Sol-Ark 15K-3P. `deye.inverter_3p`
    driver.

Customer picks the variant via `kind:` in `config.yaml`
(`inverter_1p` vs `inverter_3p`). A future auto-detect can probe
register 59 first and fall back to 500 — but for v1 the customer
picks.

Speaks **Modbus RTU over RS485**, 9600 8N1, slave ID 1 by
default. RJ45 pinout is non-standard:

    Pin 1 → RS485-B (D-)
    Pin 2 → RS485-A (D+)
    Pin 3 → GND

Same `serial_modbus` transport every other wired inverter uses.

Read-only at v1. References (all Apache-2.0 — see NOTICE):

  * kellerza/sunsynk — canonical Python register-map source.
  * StephanJoubert/home_assistant_solarman — cross-reference,
    also covers the Solarman WiFi-dongle path (out of scope here).
  * Deye Modbus protocol PDF (semi-public, mirrored at
    domotica.solar/wp-content/uploads/2023/03/sunsynk%20modbus.pdf).

Marked experimental until a customer with real hardware
confirms the per-firmware scale factors line up. The known
gotchas the driver catches up-front are the 1P-vs-3P scale
flips on battery_voltage (÷100 vs ÷10), battery_power (×–1 vs
×–10), and PV power (×–1 vs ×10).
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .inverter_1p import DeyeInverter1P
from .inverter_3p import DeyeInverter3P


INFO = VendorInfo(
    id="deye",
    display_name="Deye / Sunsynk / Sol-Ark (experimental)",
    description=(
        "Hybrid inverter family — three brand names, one Chinese "
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
