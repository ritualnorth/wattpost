"""Victron Blue Smart AC Charger driver — read-only.

The Blue Smart AC Charger is Victron's mains-input battery charger
(common when a van/cabin has occasional mains hookup, or as a backup
charger in a hybrid solar install). 3-output models (IP65 12/15-3,
12/25-3, etc.) charge three separate banks; we surface all three
output voltage + current pairs.

Registered under `device_kind="ac_charger"`.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"AcCharger"}


class VictronAcCharger(DeviceDriver):
    vendor_id = "victron"
    device_kind = "ac_charger"

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
            result["_errors"] = ["wrong transport type — requires ble_victron_advertise"]
            return result
        from ._silent import mark_silent, stamp_advertisement_age
        parsed = transport.get_latest()
        if parsed is None:
            return mark_silent(result, transport)
        stamp_advertisement_age(result, transport)
        class_name = getattr(transport, "get_device_class_name", lambda: None)()
        if class_name and class_name not in EXPECTED_DEVICE_CLASSES:
            result["_errors"] = [
                f"configured as 'ac_charger' but transport sees a {class_name}"
            ]
            return result

        def _get(m: str) -> Any:
            fn = getattr(parsed, m, None)
            if fn is None or not callable(fn): return None
            try: return fn()
            except Exception: return None

        ac_i  = _get("get_ac_current")
        temp  = _get("get_temperature")
        state = _get("get_charge_state")
        err   = _get("get_charger_error")
        model = _get("get_model_name")

        if ac_i  is not None: result["ac_input_current_a"] = ac_i
        if temp  is not None: result["temperature_c"]      = temp
        if model is not None: result["model"]              = model
        if state is not None:
            result["charging_state"] = getattr(state, "name", str(state)).lower()
        if err is not None:
            result["charger_error"]  = getattr(err, "name", str(err))

        # Three output channels — surface each as a separate output_N
        # field so the per-device detail page can render them in a
        # 3-column layout.
        for n in (1, 2, 3):
            v = _get(f"get_output_voltage{n}")
            i = _get(f"get_output_current{n}")
            if v is not None: result[f"output_{n}_voltage_v"] = v
            if i is not None: result[f"output_{n}_current_a"] = i
            if v is not None and i is not None:
                result[f"output_{n}_power_w"] = round(v * i, 1)

        return result
