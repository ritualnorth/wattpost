"""Victron Smart BatteryProtect driver, read-only.

The BatteryProtect is a load-disconnect device: cuts the load circuit
when the battery drops below a configured threshold, reconnects when
it recovers. Common in van installs to stop the fridge from killing
the bank overnight. Reports input + output voltage, output state
(connected/disconnected), and alarm/warning reasons.

Registered under `device_kind="load_disconnect"`, distinct from
`dcdc` and `ac_charger` because the dashboard rendering is different
(no power flow; the interesting state is on/off + why).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"SmartBatteryProtect"}


class VictronSmartBatteryProtect(DeviceDriver):
    vendor_id = "victron"
    device_kind = "load_disconnect"

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
                f"configured as 'load_disconnect' but transport sees a {class_name}"
            ]
            return result

        def _get(m: str) -> Any:
            fn = getattr(parsed, m, None)
            if fn is None or not callable(fn): return None
            try: return fn()
            except Exception: return None

        in_v    = _get("get_input_voltage")
        out_v   = _get("get_output_voltage")
        dev_state = _get("get_device_state")
        out_state = _get("get_output_state")
        off       = _get("get_off_reason")
        alarm     = _get("get_alarm_reason")
        warn      = _get("get_warning_reason")
        err       = _get("get_error_code")
        model     = _get("get_model_name")

        if in_v    is not None: result["input_voltage_v"]  = in_v
        if out_v   is not None: result["output_voltage_v"] = out_v
        if model   is not None: result["model"]            = model
        # The headline state is whether the load is currently connected,
        # mirror the Rover/Renogy "load_status" semantics: "on" / "off".
        if out_state is not None:
            name = getattr(out_state, "name", str(out_state)).lower()
            result["load_status"]   = "on" if name == "on" else "off"
            result["output_state"]  = name
        if dev_state is not None:
            result["device_state"]  = getattr(dev_state, "name", str(dev_state)).lower()
        if off   is not None: result["off_reason"]    = getattr(off, "name", str(off))
        if alarm is not None: result["alarm"]         = getattr(alarm, "name", str(alarm))
        if warn  is not None: result["warning"]       = getattr(warn, "name", str(warn))
        if err   is not None: result["error_code"]    = getattr(err, "name", str(err))

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))
        return result
