"""EPEVER / EPSolar MPPT vendor package (#203).

Tier-1 driver on the coverage roadmap. The Tracer series is the
#1 budget MPPT in DIY van and cabin builds. Same EPSolar protocol
covers Tracer-AN, Tracer-BN, Triron, BN-DR, eTracer, and similar.

Speaks **Modbus RTU**, but with a quirk — live state lives in
**input registers (FC04)**, not holding registers (FC03). FC03
is reserved for configuration setpoints. Our `serial_modbus`
transport handles the wire; `Section.function_code` selects FC04
where needed.

Pending real-hardware validation. See
[[project-no-victron-lab-purchases]].
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .tracer import EpeverTracer


INFO = VendorInfo(
    id="epever",
    display_name="EPEVER MPPT",
    description=(
        "EPSolar / EPEVER charge controllers. Tracer-AN, Tracer-BN, "
        "Triron, BN-DR, and eTracer all share the same Modbus map. "
        "Live state on FC04 (input registers); setpoints on FC03. "
        "USB-RS485 transport, same as Renogy wired. Slave ID 1 by "
        "default."
    ),
)


register_vendor(info=INFO, drivers={"charge_controller": EpeverTracer})


__all__ = ["INFO", "EpeverTracer"]
