"""Daly BMS driver — emits the same normalised field surface as
the JK and JBD drivers so the dashboard renders consistently.

Per-command payload layout (each is exactly 8 bytes):

  0x90 — SoC + total V + total I + current SoC
    bytes 0-1   total_voltage (0.1 V units)
    bytes 2-3   reserved (older firmware: gathered voltage)
    bytes 4-5   current = raw - 30000 (0.1 A units, signed via offset)
    bytes 6-7   soc * 10 (per-mille → soc_pct = / 10)

  0x91 — min/max cell V
    bytes 0-1   max cell voltage (mV)
    byte  2     max cell index (1-based)
    bytes 3-4   min cell voltage (mV)
    byte  5     min cell index
    bytes 6-7   padding

  0x92 — min/max temp
    byte 0      max_temp_c = raw - 40
    byte 1      max_temp_index
    byte 2      min_temp_c = raw - 40
    byte 3      min_temp_index
    bytes 4-7   padding

  0x93 — charge / discharge MOS state + cycle count
    byte  0     0=stationary, 1=charging, 2=discharging
    byte  1     charge_mos (0/1)
    byte  2     discharge_mos (0/1)
    byte  3     bms_life (cycles? Some firmwares report life-pct here)
    bytes 4-7   remaining_capacity_mah (uint32 BE)

  0x94 — counts + status
    byte 0      cell_count
    byte 1      temp_sensor_count
    byte 2      charger_status (0/1)
    byte 3      load_status (0/1)
    byte 4      di/do state bits
    bytes 5-6   cycles (uint16 BE)
    byte 7      padding

  0x96 — temperatures, one byte per sensor (offset 40)
    bytes 0-6   temp_n_c = raw - 40 (7 sensors max per frame)

  0x95 — handled in the transport accumulator (per-cell mV list)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..base import DeviceDriver, Section


log = logging.getLogger(__name__)


def _u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _u32(b: bytes, off: int) -> int:
    return (b[off] << 24) | (b[off + 1] << 16) | (b[off + 2] << 8) | b[off + 3]


class DalyBms(DeviceDriver):
    """Daly BMS driver. Pending real-hardware validation."""
    vendor_id = "daly"
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
            result["_errors"] = ["wrong transport type — Daly BMS requires ble_daly"]
            return result

        age = getattr(transport, "last_frame_age_s", lambda: None)()
        if age is not None:
            result["advertisement_age_s"] = age

        # 0x90 — V / I / SoC
        f = transport.get_latest_frame(0x90)
        if f is not None and len(f) >= 8:
            voltage = _u16(f, 0) / 10.0
            current = (_u16(f, 4) - 30000) / 10.0   # +ve = charge in this format
            soc = _u16(f, 6) / 10.0
            result["voltage_v"] = voltage
            result["current_a"] = current
            result["power_w"] = round(voltage * current, 2)
            result["soc_pct"] = soc

        # 0x91 — min/max cell V
        f = transport.get_latest_frame(0x91)
        if f is not None and len(f) >= 6:
            result["cell_max_mv"] = _u16(f, 0)
            result["cell_max_index"] = f[2]
            result["cell_min_mv"] = _u16(f, 3)
            result["cell_min_index"] = f[5]

        # 0x92 — min/max temperature
        f = transport.get_latest_frame(0x92)
        if f is not None and len(f) >= 4:
            result["temperature_c"] = f[0] - 40
            result["max_temp_index"] = f[1]
            result["temp_min_c"] = f[2] - 40
            result["min_temp_index"] = f[3]

        # 0x93 — MOS state + remaining capacity
        f = transport.get_latest_frame(0x93)
        if f is not None and len(f) >= 8:
            result["charging_state"] = {0: "idle", 1: "charging", 2: "discharging"}.get(f[0], f"state_{f[0]}")
            result["charge_mos_on"] = bool(f[1])
            result["discharge_mos_on"] = bool(f[2])
            remaining_mah = _u32(f, 4)
            if remaining_mah > 0:
                result["remaining_charge_ah"] = remaining_mah / 1000.0

        # 0x94 — counts
        f = transport.get_latest_frame(0x94)
        if f is not None and len(f) >= 7:
            result["cell_count"] = f[0]
            result["ntc_count"] = f[1]
            result["charger_connected"] = bool(f[2])
            result["load_connected"] = bool(f[3])
            result["cycle_count"] = _u16(f, 5)

        # 0x96 — per-sensor temperatures
        f = transport.get_latest_frame(0x96)
        if f is not None:
            for i in range(min(7, len(f))):
                raw = f[i]
                if raw == 0:
                    continue
                key = "temperature_c" if i == 0 and "temperature_c" not in result \
                                       else f"temperature_{i}_c"
                result[key] = raw - 40

        # Per-cell voltages from the accumulated 0x95 frames.
        cells = transport.get_cell_voltages_mv() if hasattr(transport, "get_cell_voltages_mv") else []
        for i, mv in enumerate(cells):
            result[f"cell_voltage_{i}_v"] = mv / 1000.0
        if cells:
            result.setdefault("cell_count", len(cells))

        return result
