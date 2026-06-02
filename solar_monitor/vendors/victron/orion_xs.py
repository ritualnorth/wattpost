"""Victron Orion XS DC-DC driver, read-only via BLE Instant Readout.

The Orion XS is Victron's newer DC-DC range, slowly replacing the
Orion-Tr Smart family. Adds proper output current measurement (which
the smaller Orion-Tr models lack). Same BLE Instant Readout
transport, different device class (`OrionXS` in victron-ble).

Registered under `device_kind="dcdc_xs"` so it doesn't collide with
the existing Orion-Tr driver (`device_kind="dcdc"`). The wizard
surfaces the right kind during pairing based on the decoded model.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"OrionXS"}


class VictronOrionXS(DeviceDriver):
    vendor_id = "victron"
    device_kind = "dcdc_xs"

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
                f"configured as 'dcdc_xs' but transport sees a {class_name}"
            ]
            return result

        def _get(m: str) -> Any:
            fn = getattr(parsed, m, None)
            if fn is None or not callable(fn): return None
            try: return fn()
            except Exception: return None

        in_v   = _get("get_input_voltage")
        in_a   = _get("get_input_current")
        out_v  = _get("get_output_voltage")
        out_a  = _get("get_output_current")
        state  = _get("get_charge_state")
        err    = _get("get_charger_error")
        off    = _get("get_off_reason")
        model  = _get("get_model_name")

        if in_v   is not None: result["input_voltage_v"]  = in_v
        if in_a   is not None: result["input_current_a"]  = in_a
        if out_v  is not None: result["output_voltage_v"] = out_v
        if out_a  is not None: result["output_current_a"] = out_a
        # Derived output power so the dashboard's per-device tile
        # gets a single power number without needing per-vendor math.
        if out_v is not None and out_a is not None:
            result["output_power_w"] = round(out_v * out_a, 1)
        if model is not None: result["model"] = model
        if state is not None:
            result["charging_state"] = getattr(state, "name", str(state)).lower()
        if err is not None:
            result["charger_error"]  = getattr(err, "name", str(err))
        if off is not None:
            result["off_reason"]     = getattr(off, "name", str(off))

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))
        return result
