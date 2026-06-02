"""Govee ambient sensors (#255).

Cheap, ubiquitous BLE thermo-hygrometers, what every vanlife forum
recommends as the £10 entry point. We support H5074/H5072 (older
display models) and H5075/H5101/H5102/H5104 (newer with packed
encoding), all through the same `ambient` device kind.

Read-only, these models don't accept BLE writes.
"""
from ..base import VendorInfo
from ..registry import register_vendor
from .ambient import GoveeAmbient

INFO = VendorInfo(
    id="govee",
    display_name="Govee",
    description=(
        "BLE thermometer-hygrometer (H5074, H5075, H5101, H5102). "
        "Cheap, plaintext advertisements every 2-3s. Read-only."
    ),
)

register_vendor(
    info=INFO,
    drivers={
        "ambient": GoveeAmbient,
    },
)

__all__ = ["INFO", "GoveeAmbient"]
