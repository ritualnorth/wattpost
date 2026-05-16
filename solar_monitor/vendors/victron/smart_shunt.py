"""Victron SmartShunt driver — read-only via BLE Instant Readout.

The SmartShunt broadcasts an encrypted advertisement ~1/second
carrying voltage, current, SoC, time-to-go, consumed Ah, and an
aux-input reading whose meaning depends on configuration (starter
battery V, midpoint V, or temperature). The `ble_victron_advertise`
transport handles the BLE plumbing + decryption; we just translate
victron-ble's `BatteryMonitorData` into our normalised field names
so the dashboard's hero donut, Remaining tile, and Battery health
tile (#109) light up without per-vendor logic.

The driver doesn't go through Modbus — no `sections`, no FC03. We
override `poll()` and read the latest decoded payload from the
transport.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

# Expected device-class names from victron-ble. SmartShunt + BMV-712
# share the BatteryMonitor parser (they're functionally identical for
# our purposes — both report V/A/SoC/time-to-go).
EXPECTED_DEVICE_CLASSES = {"BatteryMonitor"}


class VictronSmartShunt(DeviceDriver):
    """Victron SmartShunt / BMV-712 battery monitor.

    `slave_id` isn't meaningful for Victron BLE (no bus addressing —
    each device broadcasts independently), but the DeviceDriver base
    requires one. We accept any int and ignore it; the config
    layer's transport_id is what binds the driver to its device's
    MAC + encryption key.
    """
    vendor_id = "victron"
    device_kind = "shunt"

    @property
    def sections(self) -> tuple[Section, ...]:
        # Not a Modbus driver — sections are unused. Return empty
        # rather than None so any code that introspects without
        # calling .poll() (diagnostics, tests) doesn't NPE.
        return ()

    async def poll(self, transport) -> dict[str, Any]:
        result: dict[str, Any] = {
            "_vendor": self.vendor_id,
            "_kind":   self.device_kind,
            "_label":  self.label,
            "_slave_id": self.slave_id,
        }

        # The Victron transport exposes get_latest() returning the
        # most recent victron-ble DeviceData instance (or None when
        # nothing has landed yet / payload is stale). It also exposes
        # get_device_class_name() so we can sanity-check we're
        # talking to a SmartShunt before calling its methods.
        if not hasattr(transport, "get_latest"):
            result["_errors"] = [
                "wrong transport type — Victron SmartShunt requires "
                "ble_victron_advertise"
            ]
            return result

        parsed = transport.get_latest()
        if parsed is None:
            result["_errors"] = ["no advertisement received yet (or stale)"]
            return result

        class_name = getattr(transport, "get_device_class_name", lambda: None)()
        if class_name and class_name not in EXPECTED_DEVICE_CLASSES:
            # We discovered the configured device is actually a
            # SmartSolar / Inverter / etc., not a battery monitor.
            # Surface the mismatch loud — the user picked the wrong
            # device-kind in the config.
            result["_errors"] = [
                f"configured as 'shunt' but transport sees a "
                f"{class_name}; pick the correct device kind"
            ]
            return result

        # Map victron-ble fields → our normalised field names. Each
        # get_*() may return None (the SmartShunt reports None for
        # any aux reading that's not active in current config).
        # We swallow individual attribute errors so a single missing
        # method doesn't blank the whole poll.
        def _get(method_name: str) -> Any:
            fn = getattr(parsed, method_name, None)
            if fn is None or not callable(fn):
                return None
            try:
                return fn()
            except Exception:
                return None

        voltage = _get("get_voltage")
        current = _get("get_current")
        soc     = _get("get_soc")
        rem_min = _get("get_remaining_mins")
        consumed_ah = _get("get_consumed_ah")
        temp_c  = _get("get_temperature")
        starter_v = _get("get_starter_voltage")
        midpoint_v = _get("get_midpoint_voltage")
        model_name = _get("get_model_name")
        alarm = _get("get_alarm")
        aux_mode = _get("get_aux_mode")

        # Compute power so the flow strip + power tiles light up
        # without needing a vendor-specific field. Mirrors how
        # Renogy smart_battery exposes it.
        power_w: float | None = None
        if voltage is not None and current is not None:
            power_w = round(voltage * current, 2)

        if voltage     is not None: result["voltage_v"]            = voltage
        if current     is not None: result["current_a"]            = current
        if soc         is not None: result["soc_pct"]              = soc
        if rem_min     is not None: result["time_to_go_minutes"]   = rem_min
        if consumed_ah is not None: result["consumed_ah"]          = consumed_ah
        if temp_c      is not None: result["temperature_c"]        = temp_c
        if starter_v   is not None: result["starter_voltage_v"]    = starter_v
        if midpoint_v  is not None: result["midpoint_voltage_v"]   = midpoint_v
        if power_w     is not None: result["power_w"]              = power_w
        if model_name  is not None: result["model"]                = model_name
        # alarm + aux_mode are enums; serialise by name so storage +
        # SSE see plain strings, not Python repr.
        if alarm is not None:
            result["alarm"] = getattr(alarm, "name", str(alarm))
        if aux_mode is not None:
            result["aux_mode"] = getattr(aux_mode, "name", str(aux_mode))

        # Surface the freshness of the underlying advertisement so
        # the UI can show "advertised X seconds ago" rather than
        # leaving it ambiguous when polls run faster than ads.
        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))

        return result
