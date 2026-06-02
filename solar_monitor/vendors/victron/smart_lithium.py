"""Victron Smart Lithium battery driver, read-only.

The Smart Lithium series is Victron's own LFP product line, drop-in
12V/24V/48V LFP batteries with built-in BMS that advertise pack
voltage + per-cell voltages + battery temperature + balancer state
over BLE Instant Readout.

Registered under `device_kind="smart_battery"` so it slots into the
existing Renogy smart-battery dashboard tiles (cell-balance panel,
worst-pack drift trend) unchanged.

For #119 coverage roadmap: this closes the "Victron battery" gap.
LynxSmartBMS gets its own driver (kind="bms") because it's a
distribution-and-BMS-combo product rather than a pure battery.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"SmartLithium"}


class VictronSmartLithium(DeviceDriver):
    vendor_id = "victron"
    device_kind = "smart_battery"

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
            result["_errors"] = ["wrong transport type, requires ble_victron_advertise"]
            return result
        from ._silent import mark_silent, stamp_advertisement_age
        parsed = transport.get_latest()
        if parsed is None:
            return mark_silent(result, transport)
        stamp_advertisement_age(result, transport)
        class_name = getattr(transport, "get_device_class_name", lambda: None)()
        if class_name and class_name not in EXPECTED_DEVICE_CLASSES:
            result["_errors"] = [
                f"configured as 'smart_battery' (Victron) but transport sees a {class_name}"
            ]
            return result

        def _get(m: str) -> Any:
            fn = getattr(parsed, m, None)
            if fn is None or not callable(fn): return None
            try: return fn()
            except Exception: return None

        v_batt   = _get("get_battery_voltage")
        temp_c   = _get("get_battery_temperature")
        cells    = _get("get_cell_voltages")
        balancer = _get("get_balancer_status")
        bms_flags = _get("get_bms_flags")
        err_flags = _get("get_error_flags")
        model    = _get("get_model_name")

        if v_batt is not None: result["voltage_v"]       = v_batt
        if temp_c is not None: result["temperature_c"]   = temp_c
        if model  is not None: result["model"]           = model
        # Surface per-cell voltages under the Renogy-style index names
        # so the dashboard's cell-balance panel renders without touching.
        if isinstance(cells, list):
            result["cell_count"] = len(cells)
            for i, v in enumerate(cells):
                if v is not None:
                    result[f"cell_voltage_{i}_v"] = v
        if balancer is not None:
            result["balancer_status"] = getattr(balancer, "name", str(balancer)).lower()
        # BMS + error flags are integers; surfaced raw so the alert
        # engine can mask + match on specific bits.
        if bms_flags is not None: result["bms_flags"]   = int(bms_flags)
        if err_flags is not None: result["error_flags"] = int(err_flags)

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))
        return result
