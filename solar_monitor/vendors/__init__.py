"""Vendor + device driver framework.

A Vendor is a namespace (e.g. "renogy", "victron", "jkbms") containing one or
more DeviceDriver classes. Each DeviceDriver knows how to talk to one logical
device type (charge controller, smart battery, BMS, shunt) over a Transport.

Adding a new vendor is dropping a new folder under this package and
registering it in `registry.py`. Core code never needs to change.
"""
from .base import DeviceDriver, Section, VendorInfo
from .registry import VENDORS, register_vendor

# Import each vendor package to trigger register_vendor() side effects.
# Adding a new vendor = create a folder under this directory, then add a line
# below. That's the entire "add a vendor" code change in the core.
from . import renogy  # noqa: F401
from . import victron  # noqa: F401
from . import jkbms  # noqa: F401
from . import jbd  # noqa: F401
from . import daly  # noqa: F401
from . import epever  # noqa: F401
from . import aili  # noqa: F401
from . import junctek  # noqa: F401
from . import mopeka  # noqa: F401
from . import govee  # noqa: F401
from . import ruuvi  # noqa: F401
from . import voltronic  # noqa: F401

__all__ = [
    "DeviceDriver",
    "Section",
    "VendorInfo",
    "VENDORS",
    "register_vendor",
]
