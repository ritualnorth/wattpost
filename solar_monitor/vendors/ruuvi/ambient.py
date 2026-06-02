"""RuuviTag driver, passive BLE, format 5."""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)


class RuuviAmbient(DeviceDriver):
    """RuuviTag, temp / humidity / pressure / battery."""
    vendor_id = "ruuvi"
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
                "wrong transport type, Ruuvi ambient requires "
                "ble_ruuvi_advertise"
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

        if latest.get("temperature_c") is not None:
            result["temperature_c"] = latest["temperature_c"]
        if latest.get("humidity_pct") is not None:
            result["humidity_pct"] = latest["humidity_pct"]
        if latest.get("pressure_pa") is not None:
            result["pressure_pa"] = latest["pressure_pa"]
            # Hectopascals are what every weather UI shows; pre-compute
            # so the dashboard doesn't have to know about the SI unit.
            result["pressure_hpa"] = round(latest["pressure_pa"] / 100.0, 1)
        if latest.get("battery_mv") is not None:
            result["battery_mv"] = latest["battery_mv"]
        if latest.get("battery_pct") is not None:
            result["battery_pct"] = latest["battery_pct"]

        return result
