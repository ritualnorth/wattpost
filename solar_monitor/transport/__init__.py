"""Pluggable transports for speaking Modbus to devices.

Every transport accepts a Modbus RTU request frame (already CRC-checked by the
caller) and returns a response frame, regardless of whether the bytes travelled
over BLE, RS-485, or TCP. Driver code is identical across transports.
"""
from .base import Transport, TransportError, TransportTimeout
from .registry import TRANSPORTS, register_transport

# Import implementations to trigger @register_transport side effects.
# Imports are guarded so a missing optional dep (e.g. pyserial on Windows)
# doesn't break the whole package.
try:
    from . import ble_modbus  # noqa: F401
except ImportError:
    pass
try:
    from . import serial_modbus  # noqa: F401
except ImportError:
    pass
try:
    from . import ble_victron_advertise  # noqa: F401
except ImportError:
    pass
try:
    from . import ble_jkbms  # noqa: F401
except ImportError:
    pass
try:
    from . import ve_direct  # noqa: F401
except ImportError:
    pass

__all__ = [
    "Transport",
    "TransportError",
    "TransportTimeout",
    "TRANSPORTS",
    "register_transport",
]
