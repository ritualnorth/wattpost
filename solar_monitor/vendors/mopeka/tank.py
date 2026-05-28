"""Mopeka tank-level driver, passive BLE.

Reads the latest decoded Mopeka advertisement off
`ble_mopeka_advertise` transport, normalises into the snapshot keys
the dashboard + storage layer expect.

What we emit, and why:

  * ``hardware_kind``, "pro_check" etc., for the device detail page
    to label the sensor model honestly. Mopeka sells five hardware
    revisions and they're not interchangeable when ordering replacement
    sensors, so users want this surfaced.
  * ``battery_pct``, coin cell SoC. Mopeka sensors are CR2032-powered
    and customer support emails will be 30% "my sensor died", show
    the battery on the dashboard so users replace it proactively.
  * ``temperature_c``, sensor's own onboard thermometer. Not the
    fluid; the ambient temp of the tank shell. Useful for "winter
    propane gel" + correlating with battery-bank temp.
  * ``signal_quality``, 0..3. 0 means the ultrasonic ping had no
    clean reflection; we expose it so the device detail page can show
    "Poor reflection, check sensor mount" rather than a fake reading.
  * ``raw_distance_mm``, ultrasonic time-of-flight as distance. The
    raw value, no calibration. Fluid level % needs per-install tank
    geometry that we don't ship until #257.
  * ``tilted``, bool derived from accelerometer X/Y. If the tank
    isn't level (van moving, cylinder being swapped) the distance
    reading is unreliable regardless of quality.

Read-only, Mopekas don't accept BLE writes.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)


# Threshold for the tilted flag. Accelerometer reports ±127 across
# roughly ±2g; ~25 corresponds to ~12° of tilt from vertical, the
# point at which Mopeka's own app starts flagging readings as
# unreliable.
_TILT_THRESHOLD = 25


class MopekaTank(DeviceDriver):
    """Mopeka Pro / Pro Plus / Pro Check / Universal tank sensor."""
    vendor_id = "mopeka"
    device_kind = "tank"

    @property
    def sections(self) -> tuple[Section, ...]:
        return ()

    async def poll(self, transport) -> dict[str, Any]:
        result: dict[str, Any] = {
            "_vendor":   self.vendor_id,
            "_kind":     self.device_kind,
            "_label":    self.label,
            "_slave_id": self.slave_id,
        }

        if not hasattr(transport, "get_latest"):
            result["_errors"] = [
                "wrong transport type, Mopeka tank requires "
                "ble_mopeka_advertise"
            ]
            return result

        latest = transport.get_latest()
        age_fn = getattr(transport, "last_advertisement_age_s", None)
        age = age_fn() if callable(age_fn) else None

        # Stamp the age every poll (fresh OR stale) so the dashboard
        # silent-detector and the latest table both reflect real
        # current freshness, not the moment of last decode. Mirrors
        # the Victron _silent.stamp pattern but inlined to avoid
        # cross-vendor import.
        if age is None:
            # Sentinel: never seen. 24h reads as "Silent" to the UI
            # without claiming we know the actual silence window.
            result["advertisement_age_s"] = 86400
        else:
            result["advertisement_age_s"] = int(age)

        if latest is None:
            if age is None:
                result["_errors"] = ["no advertisement received yet"]
            else:
                result["_errors"] = [
                    f"no fresh advertisement (last seen {int(age)}s ago)"
                ]
            return result

        result["hardware_kind"] = latest["hardware_kind"]
        if latest.get("battery_pct") is not None:
            result["battery_pct"] = latest["battery_pct"]
        if latest.get("temperature_c") is not None:
            result["temperature_c"] = latest["temperature_c"]
        result["signal_quality"] = latest["quality"]
        if latest.get("raw_distance_mm") is not None:
            result["raw_distance_mm"] = latest["raw_distance_mm"]

        ax = latest.get("accel_x", 0)
        ay = latest.get("accel_y", 0)
        result["tilted"] = abs(ax) > _TILT_THRESHOLD or abs(ay) > _TILT_THRESHOLD

        return result
