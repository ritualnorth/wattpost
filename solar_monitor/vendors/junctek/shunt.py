"""Junctek shunt driver. The transport layer already merges r50 /
r51 / r53 responses into one dict, so the driver just renames /
unit-checks fields onto the canonical dashboard schema."""
from __future__ import annotations

from typing import Any

from ..base import DeviceDriver, Section


class JunctekShunt(DeviceDriver):
    """Junctek KH-F / KG-F shunt. Pending real-hardware validation."""
    vendor_id = "junctek"
    device_kind = "shunt"

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
            result["_errors"] = ["wrong transport type, Junctek requires ble_junctek"]
            return result

        age = getattr(transport, "last_frame_age_s", lambda: None)()
        if age is not None:
            result["advertisement_age_s"] = age

        f = transport.get_latest()
        if f is None:
            return result

        # Pass-through fields the transport already produced in our
        # canonical names. Junctek reports power as a separate
        # integer in r53 so prefer that; fall back to V * I if it
        # arrived first and the r53 burst is delayed.
        for k in ("voltage_v", "current_a", "remaining_ah",
                  "bank_capacity_ah", "temperature_c", "soc_pct",
                  "time_to_go_minutes",
                  "cumulative_charge_ah", "cumulative_discharge_ah"):
            if k in f:
                result[k] = f[k]
        if "power_w" in f:
            result["power_w"] = f["power_w"]
        elif "voltage_v" in f and "current_a" in f:
            result["power_w"] = round(f["voltage_v"] * f["current_a"], 2)
        return result
