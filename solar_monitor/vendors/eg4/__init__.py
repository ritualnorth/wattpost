"""EG4 inverter vendor package (#364).

Covers the EG4 XP / kPV / FlexBOSS line — the Luxpower-derived
side of EG4's catalogue. (The Voltronic-derived EG4 6500EX is in
the `voltronic` vendor package; same brand, different protocol
family.)

Speaks **Modbus RTU over RS485** on the CT1 RJ45 connector (485A
on pin 8, 485B on pin 7, 9600 8N1, slave ID 1 by default). Same
USB-RS485 dongle and `serial_modbus` transport Renogy and EPEVER
customers already use.

Read-only at v1. The register map is documented across three
independent public sources cross-referenced for the driver:

  * EG4 18kPV-12LV Modbus Communication Protocol PDF (official,
    mirrored at eg4electronics.com/wp-content/uploads/2023/06/
    EG4-18KPV-12LV-Modbus-Protocol.pdf).
  * joyfulhouse/eg4_web_monitor (MIT) — canonical Python field
    names + register addresses for the whole XP / kPV / FlexBOSS
    family.
  * celsworth/lxp-bridge (MIT) — Luxpower-LXP semantics, used
    as a tie-breaker for what each register physically means.

All three see the same input-register base; the 12000XP's
split-phase L1/L2 voltages live in extra registers (127/128 for
EPS, 193/194 for grid) that the hybrid models leave at zero.

Marked experimental until a customer with real hardware
confirms scale factors land in sensible ranges on their
firmware — Luxpower firmwares occasionally ship
battery_temperature ÷10 vs ÷1, and the device-status enum at
register 0 has a handful of vendor-specific intermediate
states.
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
        "Modbus RTU — battery state, PV input (PV1+PV2 summed), "
        "AC + EPS output, grid V/Hz, internal + battery temps, "
        "operating mode. Marked experimental: register addresses "
        "are confirmed across three sources but scale-factor "
        "quirks vary by firmware. (The Voltronic-derived EG4 "
        "6500EX lives in the `voltronic` vendor instead.)"
    ),
)


register_vendor(info=INFO, drivers={"inverter": EG4XpInverter})


__all__ = ["INFO", "EG4XpInverter"]
