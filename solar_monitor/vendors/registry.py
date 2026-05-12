"""Vendor registry.

Each vendor package registers itself with `register_vendor()`. The orchestrator
discovers what's available via `VENDORS`.
"""
from __future__ import annotations

from typing import Callable

from .base import DeviceDriver, VendorInfo


class VendorRegistration:
    def __init__(
        self,
        info: VendorInfo,
        drivers: dict[str, Callable[..., DeviceDriver]],
    ) -> None:
        self.info = info
        # device_kind -> factory(slave_id, label=None) -> DeviceDriver
        self.drivers = drivers


VENDORS: dict[str, VendorRegistration] = {}


def register_vendor(
    info: VendorInfo,
    drivers: dict[str, Callable[..., DeviceDriver]],
) -> None:
    if info.id in VENDORS:
        raise ValueError(f"vendor {info.id!r} already registered")
    VENDORS[info.id] = VendorRegistration(info=info, drivers=drivers)
