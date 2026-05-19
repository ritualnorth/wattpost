"""JBD BMS driver — read-only, all fields from cmd 0x03 + 0x04.

Field semantics taken from the Overkill Solar reference doc + the
canonical JBD protocol PDF that ships with the BMS. Multi-byte
fields are big-endian unsigned unless flagged signed.

cmd 0x03 (basic info) payload layout:

    offset  size  field
    0       2     pack_voltage   in 10 mV units (uint16)
    2       2     pack_current   in 10 mA units (int16, signed,
                                 +ve = discharge by spec; we
                                 invert so +ve = charge to
                                 match the rest of WattPost)
    4       2     residual_ah    in 10 mAh units
    6       2     nominal_ah     in 10 mAh units
    8       2     cycle_count
    10      2     production_date (packed YYYY-MM-DD bitfield;
                                  we surface the raw int)
    12      2     balance_status_low  (bit per cell)
    14      2     balance_status_high (bit per cell)
    16      2     protection_status   (bit flags — over/under V,
                                       over/under T, over-current,
                                       short, IC error, …)
    18      1     software_version (BCD)
    19      1     soc_percent
    20      1     fet_status       (bit 0 = charge MOSFET on,
                                    bit 1 = discharge MOSFET on)
    21      1     cell_count
    22      1     ntc_count
    23..    2*N   temperatures, uint16 each, value in 0.1 K with
                  offset 2731 (so temp_c = (raw - 2731) / 10).
    last 2 bytes  trailing region varies between firmware revs;
                  we don't parse them at this level.

cmd 0x04 (cell info) payload: just `cell_count * 2` bytes of
uint16 big-endian mV per cell.

Driver emits the same normalised field surface as our other BMS
drivers (JK, Renogy smart battery) so the dashboard renders
identically. See `solar_monitor/vendors/jkbms/bms.py` for the
naming convention.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section


log = logging.getLogger(__name__)

# Protection bits (cmd 0x03 byte 16-17). Bit position → human name.
_PROTECTION_BITS = {
    0: "cell_over_voltage",
    1: "cell_under_voltage",
    2: "pack_over_voltage",
    3: "pack_under_voltage",
    4: "charge_over_temp",
    5: "charge_under_temp",
    6: "discharge_over_temp",
    7: "discharge_under_temp",
    8: "charge_over_current",
    9: "discharge_over_current",
    10: "short_circuit",
    11: "ic_front_error",
    12: "software_lock",
}


def _u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _i16(b: bytes, off: int) -> int:
    v = _u16(b, off)
    return v - 0x10000 if v & 0x8000 else v


class JbdBms(DeviceDriver):
    """JBD-protocol BMS driver. Pending real-hardware validation."""
    vendor_id = "jbd"
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
        if not hasattr(transport, "get_latest_frame"):
            result["_errors"] = [
                "wrong transport type — JBD BMS requires ble_jbd"
            ]
            return result

        basic = transport.get_latest_frame(0x03)
        cells = transport.get_latest_frame(0x04)

        # Stamp freshness so the silent-tile logic on the
        # dashboard fires consistently with other Victron / Renogy
        # drivers. `advertisement_age_s` is the field everyone
        # reads even though "advertisement" is BLE-only language.
        age = getattr(transport, "last_frame_age_s", lambda: None)()
        if age is not None:
            result["advertisement_age_s"] = age

        if basic is not None and len(basic) >= 23:
            self._parse_basic(basic, result)
        if cells is not None:
            self._parse_cells(cells, result)
        return result

    def _parse_basic(self, p: bytes, result: dict[str, Any]) -> None:
        voltage = _u16(p, 0) / 100.0
        # JBD reports +ve current as discharge by spec; the rest of
        # WattPost uses +ve = charging (signed shunt-style). Flip
        # to match. The flip also lets the bank-aggregation flow
        # tile light up green on charge vs amber on discharge
        # without per-vendor branching.
        raw_current = _i16(p, 2)
        current = -(raw_current / 100.0)
        residual_ah = _u16(p, 4) / 100.0
        nominal_ah = _u16(p, 6) / 100.0
        cycles = _u16(p, 8)
        protection = _u16(p, 16)
        soc_pct = p[19] if len(p) > 19 else None
        fet = p[20] if len(p) > 20 else 0
        cell_count = p[21] if len(p) > 21 else 0
        ntc_count = p[22] if len(p) > 22 else 0

        result["voltage_v"] = voltage
        result["current_a"] = current
        result["power_w"] = round(voltage * current, 2)
        result["remaining_charge_ah"] = residual_ah
        result["capacity_ah"] = nominal_ah
        if nominal_ah > 0:
            result["soc_pct_derived"] = round(residual_ah / nominal_ah * 100, 1)
        if soc_pct is not None:
            result["soc_pct"] = float(soc_pct)
        result["cycle_count"] = cycles
        result["cell_count"] = cell_count
        result["charge_mos_on"] = bool(fet & 0x01)
        result["discharge_mos_on"] = bool(fet & 0x02)

        # Per-NTC temperatures. Raw is 0.1 K offset 2731 so temp_c =
        # (raw - 2731) / 10. We surface the first one as the canonical
        # temperature_c field (matches other drivers); additional ones
        # land as temperature_<n>_c so the device-detail page can
        # render a per-sensor list.
        for i in range(ntc_count):
            off = 23 + i * 2
            if off + 2 > len(p):
                break
            raw = _u16(p, off)
            temp_c = (raw - 2731) / 10.0
            key = "temperature_c" if i == 0 else f"temperature_{i}_c"
            result[key] = temp_c

        # Protection bits — surfaced as a comma-joined list of the
        # active conditions plus the raw bitmask for power users.
        active = [name for bit, name in _PROTECTION_BITS.items()
                  if protection & (1 << bit)]
        if active:
            result["protection_active"] = active
        if protection:
            result["protection_raw"] = protection

    def _parse_cells(self, p: bytes, result: dict[str, Any]) -> None:
        cells = []
        for i in range(0, len(p) - 1, 2):
            mv = _u16(p, i)
            if mv == 0:
                # Some firmware pads the trailing region with zeros;
                # treat 0 as "no cell here" rather than an active
                # 0 V reading (which would alarm).
                break
            cells.append(mv / 1000.0)
        # Mirror JK's naming: cell_voltage_<i>_v + cell_count.
        for i, v in enumerate(cells):
            result[f"cell_voltage_{i}_v"] = v
        if cells:
            result.setdefault("cell_count", len(cells))
