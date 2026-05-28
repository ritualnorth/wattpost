"""Voltronic-family inverter driver (Axpert / MPP Solar / EG4 rebadges).

Read-only. Parses the four ASCII commands every Voltronic-derived
firmware supports:

  * QPI    — protocol version (sanity check on first poll)
  * QID    — device serial number (one-shot, cached)
  * QMOD   — current mode (Power on / Standby / Line / Battery / Fault / eco)
  * QPIGS  — live status, ~21 space-separated fields
  * QPIWS  — 32-bit warning bitmap (optional, surfaced as alarm_flags)

This covers the union of fields the rebadges expose — Axpert,
MPP Solar PIP/LV-MK, EG4 6000XP/6500EX, Mecer, RCT, Infinisolar,
Anenji, Datouboss, HZSolar, Effekta, LVTopSun, PowMr, Easun. Pure
Sine Inverter Plus and the larger three-phase units use QPIGS2 /
QPIGS3 for additional phases — out of scope for v1, we read the
first phase only.

Marked experimental because individual firmware variants diverge
on field ordering past column ~17. The first-customer reports
become the validation set; we ship with the safest common subset
parsed and the raw response logged at DEBUG.
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)


MODE_NAMES = {
    "P": "power_on",
    "S": "standby",
    "L": "line",
    "B": "battery",
    "F": "fault",
    "H": "eco",
    "D": "shutdown",
    "Y": "bypass",
}


def _float(s: str) -> float | None:
    """Parse a Voltronic numeric field, tolerating empty / 'NAK' / 'ERR'."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def parse_qpigs(payload: bytes) -> dict[str, Any]:
    """Decode a QPIGS response payload into normalised fields.

    Schema follows the Voltronic Inverter Communication Protocol v3.6.
    Fields past column 16 vary by firmware so we treat them as best-
    effort and never raise on a short response — partial state is
    better than no state.
    """
    text = payload.decode("ascii", errors="replace")
    fields = text.split(" ")
    out: dict[str, Any] = {"_raw_qpigs": text}

    def take(idx: int, fn=_float) -> Any:
        if idx >= len(fields):
            return None
        return fn(fields[idx])

    grid_v        = take(0)
    grid_hz       = take(1)
    ac_out_v      = take(2)
    ac_out_hz     = take(3)
    ac_out_va     = take(4)
    ac_out_w      = take(5)
    load_pct      = take(6)
    bus_v         = take(7)
    batt_v        = take(8)
    charge_a      = take(9)
    soc           = take(10, _int)
    inverter_c    = take(11)
    pv_a          = take(12)
    pv_v          = take(13)
    batt_v_scc    = take(14)
    discharge_a   = take(15)
    status_flags  = fields[16] if len(fields) > 16 else ""
    pv_w          = take(19)

    if grid_v       is not None: out["grid_voltage_v"]            = round(grid_v, 1)
    if grid_hz      is not None: out["grid_frequency_hz"]         = round(grid_hz, 1)
    if ac_out_v     is not None: out["ac_output_voltage_v"]       = round(ac_out_v, 1)
    if ac_out_hz    is not None: out["ac_output_frequency_hz"]    = round(ac_out_hz, 1)
    if ac_out_va    is not None: out["ac_output_apparent_power_va"] = int(ac_out_va)
    if ac_out_w     is not None: out["ac_output_power_w"]         = int(ac_out_w)
    if load_pct     is not None: out["ac_output_load_pct"]        = int(load_pct)
    if bus_v        is not None: out["bus_voltage_v"]             = round(bus_v, 1)
    if batt_v       is not None: out["battery_voltage_v"]         = round(batt_v, 2)
    if soc          is not None: out["soc_pct"]                   = soc
    if inverter_c   is not None: out["temperature_c"]             = round(inverter_c, 1)
    if pv_a         is not None: out["pv_current_a"]              = round(pv_a, 1)
    if pv_v         is not None: out["pv_voltage_v"]              = round(pv_v, 1)
    if batt_v_scc   is not None: out["battery_voltage_scc_v"]     = round(batt_v_scc, 2)

    # Net battery current: positive = charging into the bank, negative =
    # discharging out through the inverter. QPIGS reports both directions
    # as unsigned and only one is non-zero in practice, so the difference
    # is the signed truth.
    if charge_a is not None and discharge_a is not None:
        out["battery_charging_current_a"]    = round(charge_a, 1)
        out["battery_discharging_current_a"] = round(discharge_a, 1)
        out["battery_current_a"]             = round(charge_a - discharge_a, 1)
        if batt_v is not None:
            out["battery_power_w"] = round(batt_v * (charge_a - discharge_a), 1)

    # PV power: most firmware emits field 19. When absent (older
    # variants), derive from PV V × PV A so the dashboard isn't blank.
    if pv_w is not None:
        out["pv_power_w"] = int(pv_w)
    elif pv_v is not None and pv_a is not None:
        out["pv_power_w"] = int(pv_v * pv_a)

    if status_flags:
        out["device_status_flags"] = status_flags

    return out


def parse_qmod(payload: bytes) -> dict[str, Any]:
    """QMOD payload is a single ASCII character identifying mode."""
    text = payload.decode("ascii", errors="replace").strip()
    code = text[:1] if text else ""
    return {
        "inverter_mode_code": code,
        "inverter_mode": MODE_NAMES.get(code, "unknown"),
    }


def parse_qpiws(payload: bytes) -> dict[str, Any]:
    """QPIWS payload is a 32- or 36-char string of '0'/'1' bits, one
    per documented warning. We surface the count of active warnings
    and the raw bitmap; the diagnostics engine (#342) translates
    individual bits to human text when we have a customer mapping."""
    bitmap = payload.decode("ascii", errors="replace").strip()
    active = bitmap.count("1")
    return {
        "warning_bitmap": bitmap,
        "warning_count": active,
        "alarm_flags": int(bitmap, 2) if bitmap and set(bitmap) <= {"0", "1"} else 0,
    }


class VoltronicInverter(DeviceDriver):
    """Voltronic-family hybrid inverter (read-only, experimental).

    Compatible rebadges (community-reported, not all lab-validated):
    Axpert (Voltronic), MPP Solar PIP/LV-MK, EG4 6000XP/6500EX,
    Mecer SOL-I-AX, RCT Axpert, Infinisolar V/E, Anenji, Datouboss,
    HZSolar, Effekta KS, LVTopSun, PowMr, Easun ISolar.
    """
    vendor_id = "voltronic"
    device_kind = "inverter"

    def __init__(self, slave_id: int = 1, label: str | None = None) -> None:
        super().__init__(slave_id, label)
        self._serial_cached: str | None = None
        self._protocol_cached: str | None = None

    @property
    def sections(self) -> tuple[Section, ...]:
        return ()  # non-Modbus protocol

    async def poll(self, transport) -> dict[str, Any]:
        result: dict[str, Any] = {
            "_vendor":   self.vendor_id,
            "_kind":     self.device_kind,
            "_label":    self.label,
            "_slave_id": self.slave_id,
        }
        if not hasattr(transport, "query"):
            result["_errors"] = [
                "wrong transport type — Voltronic inverter requires usbhid_voltronic"
            ]
            return result

        # One-shot identification (protocol + serial) on first successful
        # poll. Cached so we don't burn cycles on QPI/QID every tick.
        if self._protocol_cached is None:
            try:
                pi = await transport.query("QPI", timeout=3.0)
                self._protocol_cached = pi.decode("ascii", errors="replace")
            except Exception as e:
                log.debug("[%s] QPI failed: %s", self.label, e)
        if self._serial_cached is None:
            try:
                qid = await transport.query("QID", timeout=3.0)
                self._serial_cached = qid.decode("ascii", errors="replace").strip()
            except Exception as e:
                log.debug("[%s] QID failed: %s", self.label, e)
        if self._protocol_cached:
            result["protocol_id"] = self._protocol_cached
        if self._serial_cached:
            result["serial_number"] = self._serial_cached

        errors: list[str] = []

        try:
            qpigs = await transport.query("QPIGS", timeout=5.0)
            result.update(parse_qpigs(qpigs))
        except Exception as e:
            errors.append(f"QPIGS: {e}")

        try:
            qmod = await transport.query("QMOD", timeout=3.0)
            result.update(parse_qmod(qmod))
        except Exception as e:
            errors.append(f"QMOD: {e}")

        try:
            qpiws = await transport.query("QPIWS", timeout=3.0)
            result.update(parse_qpiws(qpiws))
        except Exception as e:
            # Warning bitmap is informational; failure here is not fatal.
            log.debug("[%s] QPIWS failed: %s", self.label, e)

        if errors:
            result["_errors"] = errors
        return result
