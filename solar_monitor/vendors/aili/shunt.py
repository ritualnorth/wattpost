"""AiLi shunt driver. Emits the same shunt-shaped field surface as
the Victron SmartShunt + Renogy RBM-S100/300/500 drivers so the
hero donut + bank aggregation render identically.

The wire-level parser lives in transport/ble_aili.py because some
of the field offsets need to be re-validated when real hardware
shows up. The driver here is just unit conversion + naming.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section


log = logging.getLogger(__name__)


class AiliShunt(DeviceDriver):
    """AiLi BLE smart-shunt driver. Pending real-hardware validation."""
    vendor_id = "aili"
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
            result["_errors"] = ["wrong transport type, AiLi shunt requires ble_aili"]
            return result

        age = getattr(transport, "last_frame_age_s", lambda: None)()
        if age is not None:
            result["advertisement_age_s"] = age

        f = transport.get_latest()
        if f is None:
            # No frame received yet, the dashboard's silent-tile
            # logic kicks in via the age field above.
            return result

        # Unit-converted fields land under the canonical names the
        # bank-aggregation layer reads.
        voltage_v = float(f.get("voltage_v") or 0)
        current_a = float(f.get("current_a") or 0)
        result["voltage_v"] = voltage_v
        result["current_a"] = current_a
        result["power_w"] = round(voltage_v * current_a, 2)
        # AiLi puts the bank size in remaining_ah (running counter)
        # and gives us soc_pct as a separate byte. WattPost prefers
        # full_capacity_ah for the dashboard's Capacity tile; we
        # derive it from (remaining_ah / soc_pct) when both are
        # known, falling back to remaining_ah alone otherwise.
        if "remaining_ah" in f:
            result["remaining_ah"] = float(f["remaining_ah"])
        soc = f.get("soc_pct")
        if soc is not None:
            result["soc_pct"] = float(soc)
            if soc > 0 and "remaining_ah" in result:
                result["bank_capacity_ah"] = round(
                    result["remaining_ah"] / (soc / 100.0), 2,
                )
        if "temperature_c" in f:
            result["temperature_c"] = float(f["temperature_c"])
        ttg = f.get("time_to_go_minutes")
        if ttg is not None:
            result["time_to_go_minutes"] = int(ttg)
        if "cumulative_ah" in f:
            result["cumulative_ah"] = float(f["cumulative_ah"])
        return result
