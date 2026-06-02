"""Govee thermo-hygrometer driver, passive BLE."""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)


class GoveeAmbient(DeviceDriver):
    """Govee H507x / H510x temperature + humidity sensor."""
    vendor_id = "govee"
    device_kind = "ambient"

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
                "wrong transport type, Govee ambient requires "
                "ble_govee_advertise"
            ]
            return result

        latest = transport.get_latest()
        age_fn = getattr(transport, "last_advertisement_age_s", None)
        age = age_fn() if callable(age_fn) else None
        result["advertisement_age_s"] = 86400 if age is None else int(age)

        if latest is None:
            if age is None:
                result["_errors"] = ["no advertisement received yet"]
            else:
                result["_errors"] = [
                    f"no fresh advertisement (last seen {int(age)}s ago)"
                ]
            return result

        result["hardware_kind"] = latest["hardware_kind"]
        if latest.get("temperature_c") is not None:
            result["temperature_c"] = latest["temperature_c"]
        if latest.get("humidity_pct") is not None:
            result["humidity_pct"] = latest["humidity_pct"]
        if latest.get("battery_pct") is not None:
            result["battery_pct"] = latest["battery_pct"]

        return result
