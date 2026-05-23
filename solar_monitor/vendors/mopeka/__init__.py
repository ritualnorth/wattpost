"""Mopeka tank-level sensors (#254).

Mopeka makes the dominant after-market tank monitors for vanlife
propane / water tanks: a magnetic puck on the bottom of the tank,
ultrasonic ToF measurement of the fluid above it, BLE advertisement
every ~10s. Plaintext, no encryption key.

We ship a single device kind today:

  * tank — Pro Check / Pro Plus / Pro Check H2O / Pro Universal,
    all decoded through the same parser. Hardware-id byte tells us
    which model the sensor reports as; the driver emits raw distance
    + signal quality + battery + temperature. Fluid level % is per-
    install calibration deferred to #257 (tank height + fluid speed-
    of-sound → percentage) so the user can wire the same sensor to a
    horizontal cylindrical propane bottle vs a rectangular freshwater
    tote without us guessing.

Strategic context: this driver is the entry point of the sensor wave
([[project_van_mode]]). Persona A van-builders want propane + water
visibility next to the battery; offering it natively (vs Mopeka's own
app that doesn't talk to anything else) is the wedge. See
[[project_target_customer]] for why we lean into Persona A here even
though it widens the driver count.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .tank import MopekaTank

INFO = VendorInfo(
    id="mopeka",
    display_name="Mopeka",
    description=(
        "BLE tank-level sensors (Pro Check, Pro Plus, H2O, Universal). "
        "Magnetically mount to the bottom of a propane or water tank "
        "and broadcast ultrasonic fluid-level readings every ~10s. "
        "Plaintext advertisements — no encryption key required. "
        "Read-only."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "tank": MopekaTank,
    },
)

__all__ = ["INFO", "MopekaTank"]
