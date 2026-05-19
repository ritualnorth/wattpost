"""VE.Direct device drivers — wired alternative to BLE Instant Readout (#197).

Three device-kind drivers covering the consumer VE.Direct surface:

  - `VictronVeDirectShunt`   — SmartShunt / BMV-7xx
  - `VictronVeDirectMppt`    — SmartSolar MPPT
  - `VictronVeDirectPhoenix` — Phoenix Inverter VE.Direct

Each one consumes the dict that `VeDirectTransport.get_latest()`
returns (label → string value, plus `_pid_int` when available) and
emits the *same* normalised result fields as its BLE counterpart.
The dashboard, exporters, and bank-aggregation code don't see which
transport produced the readings — both paths flow into the same
field names with the same units.

Read-only. VE.Direct doesn't expose writes for normal settings
(those are VictronConnect / VRM / Cerbo only) and we wouldn't
expose them if it did — Victron is read-only by deliberate scope
(project_victron_scope memo).

Field-mapping references: Victron VE.Direct Protocol PDF (public).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)


def _to_int(s: str | None) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _mark_silent_vedirect(result: dict[str, Any], transport) -> dict[str, Any]:
    """Same shape the BLE Victron drivers use, adjusted for VE.Direct.
    The flow strip's stale-tile logic reads `advertisement_age_s`, so
    we reuse that field name even though the value is really "seconds
    since the last VE.Direct frame.\""""
    age = getattr(transport, "last_frame_age_s", lambda: None)()
    if age is not None:
        result["advertisement_age_s"] = age
    return result


def _stamp_age(result: dict[str, Any], transport) -> None:
    age = getattr(transport, "last_frame_age_s", lambda: None)()
    if age is not None:
        result["advertisement_age_s"] = age


def _require_ve_direct(transport, result, kind_name: str) -> bool:
    """Sanity-check that this driver got pointed at a VE.Direct
    transport, not a BLE one. Returns True iff things look right;
    otherwise stamps `_errors` and tells the caller to bail."""
    if not hasattr(transport, "get_latest"):
        result["_errors"] = [
            f"wrong transport type — VE.Direct {kind_name} requires "
            "the ve_direct transport"
        ]
        return False
    return True


# ----------------------------------------------------------------- shunt

class VictronVeDirectShunt(DeviceDriver):
    """SmartShunt + BMV-7xx over VE.Direct. Mirrors the field surface
    of the BLE SmartShunt driver so the dashboard tile renders the
    same way regardless of transport."""
    vendor_id = "victron_vedirect"
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
        if not _require_ve_direct(transport, result, "shunt"):
            return result
        f = transport.get_latest()
        if f is None:
            return _mark_silent_vedirect(result, transport)
        _stamp_age(result, transport)

        v_mv = _to_int(f.get("V"))
        i_ma = _to_int(f.get("I"))
        p_w  = _to_int(f.get("P"))
        soc_per_mille = _to_int(f.get("SOC"))
        ttg_min = _to_int(f.get("TTG"))
        ce_mah = _to_int(f.get("CE"))
        t_c    = _to_int(f.get("T"))

        if v_mv is not None:
            voltage = v_mv / 1000.0
            result["voltage_v"] = voltage
        else:
            voltage = None
        if i_ma is not None:
            current = i_ma / 1000.0
            result["current_a"] = current
        else:
            current = None
        # Prefer device-reported power; derive from V*I when missing.
        if p_w is not None:
            result["power_w"] = float(p_w)
        elif voltage is not None and current is not None:
            result["power_w"] = round(voltage * current, 2)
        if soc_per_mille is not None:
            result["soc_pct"] = soc_per_mille / 10.0
        if ttg_min is not None and ttg_min >= 0:
            result["time_to_go_minutes"] = ttg_min
        if ce_mah is not None:
            # CE is "consumed energy" in mAh, signed (negative = drained).
            result["consumed_ah"] = ce_mah / 1000.0
        if t_c is not None:
            result["temperature_c"] = float(t_c)
        if "BMV" in f:
            result["model"] = f["BMV"]
        if "Alarm" in f and f["Alarm"] != "OFF":
            result["alarm"] = f["Alarm"]
        return result


# ----------------------------------------------------------------- mppt

# VE.Direct CS field is the charger state enum. Same numbers as
# Victron's BLE encoding so we map onto the same names the BLE
# driver emits.
_CHARGE_STATES = {
    0: "off", 2: "fault", 3: "bulk", 4: "absorption", 5: "float",
    6: "storage", 7: "equalize", 9: "inverting", 11: "power_supply",
    245: "starting_up", 247: "auto_equalize",
}


class VictronVeDirectMppt(DeviceDriver):
    """SmartSolar MPPT over VE.Direct."""
    vendor_id = "victron_vedirect"
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
        if not _require_ve_direct(transport, result, "MPPT"):
            return result
        f = transport.get_latest()
        if f is None:
            return _mark_silent_vedirect(result, transport)
        _stamp_age(result, transport)

        v_mv  = _to_int(f.get("V"))         # battery V
        i_ma  = _to_int(f.get("I"))         # battery I (out of MPPT)
        vpv_mv = _to_int(f.get("VPV"))      # panel V
        ppv_w  = _to_int(f.get("PPV"))      # panel W
        cs     = _to_int(f.get("CS"))       # charger state enum
        err    = _to_int(f.get("ERR"))      # error code
        h19_cwh = _to_int(f.get("H19"))     # total yield, 10 Wh units
        h20_cwh = _to_int(f.get("H20"))     # today yield, 10 Wh units
        h21_w   = _to_int(f.get("H21"))     # today max power, W
        load_str = f.get("LOAD")

        if v_mv is not None:
            result["voltage_v"] = v_mv / 1000.0
        if i_ma is not None:
            result["current_a"] = i_ma / 1000.0
        if vpv_mv is not None:
            result["pv_voltage_v"] = vpv_mv / 1000.0
        if ppv_w is not None:
            result["pv_power_w"]   = ppv_w
            result["power_w"]      = ppv_w
        if h20_cwh is not None:
            # H20 in centi-Wh (10-Wh units per Victron docs).
            result["today_yield_wh"] = h20_cwh * 10
        if h19_cwh is not None:
            result["total_yield_wh"] = h19_cwh * 10
        if h21_w is not None:
            result["today_max_power_w"] = h21_w
        if cs is not None:
            result["charging_state"] = _CHARGE_STATES.get(cs, f"cs_{cs}")
        if err is not None and err != 0:
            result["error_code"] = err
        if load_str:
            result["load_output"] = load_str.upper() == "ON"
        return result


# ------------------------------------------------------- phoenix inverter

class VictronVeDirectPhoenix(DeviceDriver):
    """Phoenix Inverter VE.Direct. Smaller pure-sine inverters that
    expose a serial port. Read-only — AC side state, battery side,
    alarms. Doesn't cover MultiPlus / Quattro (those need VE.Bus
    + an MK3 interface and are explicitly out of scope)."""
    vendor_id = "victron_vedirect"
    device_kind = "inverter"

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
        if not _require_ve_direct(transport, result, "Phoenix inverter"):
            return result
        f = transport.get_latest()
        if f is None:
            return _mark_silent_vedirect(result, transport)
        _stamp_age(result, transport)

        v_mv     = _to_int(f.get("V"))      # battery V
        ac_v     = _to_int(f.get("AC_OUT_V"))    # 0.01 V units
        ac_i     = _to_int(f.get("AC_OUT_I"))    # 0.1 A units
        ac_s     = _to_int(f.get("AC_OUT_S"))    # AC out apparent power, VA
        mode_s   = f.get("MODE")
        err      = _to_int(f.get("ERR"))
        warn     = _to_int(f.get("WARN"))

        if v_mv is not None:
            result["battery_voltage_v"] = v_mv / 1000.0
        if ac_v is not None:
            result["ac_output_voltage_v"] = ac_v / 100.0
        if ac_i is not None:
            result["ac_output_current_a"] = ac_i / 10.0
        if ac_s is not None:
            result["ac_output_apparent_va"] = ac_s
        if mode_s:
            result["mode"] = mode_s
        if err is not None and err != 0:
            result["error_code"] = err
        if warn is not None and warn != 0:
            result["warning_code"] = warn
        return result
