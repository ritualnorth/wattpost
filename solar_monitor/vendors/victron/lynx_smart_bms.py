"""Victron Lynx Smart BMS driver — read-only.

The Lynx Smart BMS is a distribution-and-BMS combo: it sits between
a Smart Lithium battery bank and the rest of the system, providing
shunt-style metrics (V, A, SoC, consumed Ah, time-to-go) PLUS a
disconnect contactor controlled by the BMS protection logic.

Registered under `device_kind="bms"` to distinguish it from a pure
battery (which goes under `smart_battery`).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"LynxSmartBMS"}


class VictronLynxSmartBMS(DeviceDriver):
    vendor_id = "victron"
    device_kind = "bms"

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
        parsed = transport.get_latest()
        if parsed is None:
            result["_errors"] = ["no advertisement received yet (or stale)"]
            return result
        class_name = getattr(transport, "get_device_class_name", lambda: None)()
        if class_name and class_name not in EXPECTED_DEVICE_CLASSES:
            result["_errors"] = [
                f"configured as 'bms' (Victron Lynx) but transport sees a {class_name}"
            ]
            return result

        def _get(m: str) -> Any:
            fn = getattr(parsed, m, None)
            if fn is None or not callable(fn): return None
            try: return fn()
            except Exception: return None

        v          = _get("get_voltage")
        i          = _get("get_current")
        soc        = _get("get_soc")
        consumed   = _get("get_consumed_ah")
        rem_min    = _get("get_remaining_mins")
        temp_c     = _get("get_battery_temperature")
        io_status  = _get("get_io_status")
        alarm_fl   = _get("get_alarm_flags")
        err_fl     = _get("get_error_flags")
        model      = _get("get_model_name")

        if v        is not None: result["voltage_v"]          = v
        if i        is not None: result["current_a"]          = i
        if soc      is not None: result["soc_pct"]            = soc
        if consumed is not None: result["consumed_ah"]        = consumed
        if rem_min  is not None: result["time_to_go_minutes"] = rem_min
        if temp_c   is not None: result["temperature_c"]      = temp_c
        if model    is not None: result["model"]              = model
        # Derived power so the flow strip lights up.
        if v is not None and i is not None:
            result["power_w"] = round(v * i, 2)
        # IO status carries contactor state + system flags; surfaced
        # raw for the alert engine + audit log.
        if io_status is not None: result["io_status"]   = int(io_status)
        if alarm_fl  is not None: result["alarm_flags"] = int(alarm_fl)
        if err_fl    is not None: result["error_flags"] = int(err_fl)

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))
        return result
