"""Victron SmartSolar MPPT driver — read-only via BLE Instant Readout.

Covers every SmartSolar model: 75/15, 75/10, 100/20, 100/30, 100/50,
150/35, 150/45, 150/60, 150/70, 150/85, 150/100, 250/60, 250/85,
250/100. They all decode through the same `SolarCharger` model in
victron-ble — one driver, the whole family.

Reuses #112's `ble_victron_advertise` transport. The biggest single
Victron unlock in our coverage roadmap: every Victron solar install
becomes a WattPost-monitorable install.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

EXPECTED_DEVICE_CLASSES = {"SolarCharger"}


class VictronSmartSolar(DeviceDriver):
    vendor_id = "victron"
    device_kind = "charge_controller"

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
                "wrong transport type — Victron SmartSolar requires "
                "ble_victron_advertise"
            ]
            return result
        parsed = transport.get_latest()
        if parsed is None:
            result["_errors"] = ["no advertisement received yet (or stale)"]
            return result
        class_name = getattr(transport, "get_device_class_name", lambda: None)()
        if class_name and class_name not in EXPECTED_DEVICE_CLASSES:
            result["_errors"] = [
                f"configured as 'charge_controller' (Victron SmartSolar) "
                f"but transport sees a {class_name}; pick the correct device kind"
            ]
            return result

        def _get(method_name: str) -> Any:
            fn = getattr(parsed, method_name, None)
            if fn is None or not callable(fn):
                return None
            try:
                return fn()
            except Exception:
                return None

        v_batt   = _get("get_battery_voltage")
        i_charge = _get("get_battery_charging_current")
        pv_w     = _get("get_solar_power")
        ext_load = _get("get_external_device_load")
        yield_kwh = _get("get_yield_today")
        state    = _get("get_charge_state")
        err      = _get("get_charger_error")
        model    = _get("get_model_name")

        # Map to the same field names the Renogy Rover uses so the
        # dashboard's charge-controller tile reads from one schema.
        if v_batt   is not None: result["battery_voltage_v"]    = v_batt
        if i_charge is not None: result["battery_current_a"]    = i_charge
        if pv_w     is not None: result["pv_power_w"]           = pv_w
        if ext_load is not None: result["load_current_a"]       = ext_load
        # yield_today is kWh in victron-ble; convert to Wh to match Rover.
        if yield_kwh is not None: result["energy_today_wh"]     = round(yield_kwh * 1000.0, 1)
        if model    is not None: result["model"]                = model
        if state is not None:
            result["charging_state"] = getattr(state, "name", str(state)).lower()
        if err is not None:
            result["charger_error"]  = getattr(err, "name", str(err))

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))
        return result
