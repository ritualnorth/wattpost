"""RuuviTag environmental sensors (#255).

Open-hardware ambient + accelerometer. Pricier than Govee (~£25)
but better battery life and adds barometric pressure (which the
forecast tile + storm-warning rules want). Format 5 (RAWv2) only;
the older format 3 and encrypted format 8 are documented as
unsupported.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .ambient import RuuviAmbient

INFO = VendorInfo(
    id="ruuvi",
    display_name="Ruuvi",
    description=(
        "Open-hardware BLE environmental sensor. Temperature, humidity, "
        "barometric pressure. Read-only via RAWv2 (format 5) "
        "advertisements."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "ambient": RuuviAmbient,
    },
)

__all__ = ["INFO", "RuuviAmbient"]
