"""Victron Orion-Tr Smart DC-DC charger driver — read-only.

The Orion-Tr Smart family (12/12-18, 12/12-30, 12/24-15, 24/12-30 etc.)
broadcasts Instant Readout advertisements just like the SmartShunt;
we reuse the `ble_victron_advertise` transport (#112) and only need
to register a new driver that knows how to map the `DcDcConverter`
payload onto our standard fields.

Worth noting: the smaller Orion-Tr models don't have an output-current
sensor. We expose every field victron-ble surfaces; downstream UI
treats missing values the same way it does for any optional metric.

Pairs with #119's coverage roadmap — Victron Orion-Tr is the most
common DC-DC charger in vans alongside the Renogy DCC50S, which
gets its own driver in #123.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

# Expected device-class name from victron-ble. The Orion-Tr Smart
# family all decodes through `DcDcConverter`; if a transport ever
# sees a different class while configured as 'dcdc' we surface a
# clear mismatch error rather than rendering garbage fields.
EXPECTED_DEVICE_CLASSES = {"DcDcConverter"}


class VictronDcDc(DeviceDriver):
    """Victron Orion-Tr Smart DC-DC charger."""
    vendor_id = "victron"
    device_kind = "dcdc"

    @property
    def sections(self) -> tuple[Section, ...]:
        return ()  # Not a Modbus driver — see SmartShunt for the same pattern

    async def poll(self, transport) -> dict[str, Any]:
        result: dict[str, Any] = {
            "_vendor": self.vendor_id,
            "_kind":   self.device_kind,
            "_label":  self.label,
            "_slave_id": self.slave_id,
        }

        if not hasattr(transport, "get_latest"):
            result["_errors"] = [
                "wrong transport type — Victron DC-DC requires "
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
                f"configured as 'dcdc' but transport sees a "
                f"{class_name}; pick the correct device kind"
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

        input_v   = _get("get_input_voltage")
        output_v  = _get("get_output_voltage")
        state     = _get("get_charge_state")
        err       = _get("get_charger_error")
        off_rsn   = _get("get_off_reason")
        model     = _get("get_model_name")

        # Map onto normalised fields. We borrow `pv_voltage_v` /
        # `battery_voltage_v` semantics from the Rover so the device-
        # detail dashboard tile lights up without per-vendor templates,
        # even though the Orion isn't a PV charger — input is whatever
        # is on the input side (alternator typically) and output is the
        # battery being charged.
        if input_v   is not None: result["input_voltage_v"]   = input_v
        if output_v  is not None: result["output_voltage_v"]  = output_v
        if model     is not None: result["model"]             = model

        # Enums serialise by name (.name) so storage + SSE see plain
        # strings, not Python repr. The charger-state field is what the
        # dashboard pill renders — same convention as Rover's
        # `charging_state` so existing UI code reads it correctly.
        if state is not None:
            result["charging_state"] = getattr(state, "name", str(state)).lower()
        if err is not None:
            result["charger_error"]  = getattr(err, "name", str(err))
        if off_rsn is not None:
            # OFF_REASON is informative — surfaces "ENGINE_SHUTDOWN"
            # so the UI can show "waiting for ignition" rather than
            # leaving the user wondering why their DC-DC is idle.
            result["off_reason"]     = getattr(off_rsn, "name", str(off_rsn))

        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["advertisement_age_s"] = max(0, int(time.time() - latest_at))

        return result
