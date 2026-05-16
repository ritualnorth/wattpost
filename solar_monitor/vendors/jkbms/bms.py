"""JK BMS device driver — protocol-version-aware cell-info parser.

Frame layout (from syssi/esphome-jk-bms, validated against every
JK firmware in the wild):

  Byte  Len  Field
  0     4    Header: 0x55 0xAA 0xEB 0x90  (or 0xAA 0x55 0x90 0xEB)
  4     1    Frame type (0x02 = cell info — what we parse)
  5     1    Frame counter
  6     N×2  Cell voltages (N cells, mV little-endian)
              N=24 for JK02_24S, N=32 for JK02_32S
  54    4    Enabled-cells bitmask (24S) — at 70 for 32S
  58    2    Average cell voltage (mV) — at 74 for 32S
  60    2    Delta cell voltage (mV) — at 76 for 32S
  64    N×2  Cell resistances (mΩ) — at 80 for 32S
  112   2    Power-tube temp (32S only) / Unknown (24S) — at 144 for 32S
  118   4    Battery voltage (mV) — at 150 for 32S
  126   4    Charge current (mA, signed) — at 158 for 32S
  130   2    Temp sensor 1 (0.1°C) — at 162 for 32S
  132   2    Temp sensor 2 (0.1°C) — at 164 for 32S
  134   2    MOS temp / errors (32S) — at 166 for 32S
  138   2    Balance current (mA) — at 170 for 32S
  140   1    Balancing action (0=off, 1=charge, 2=discharge) — at 172 for 32S
  141   1    State of charge (%) — at 173 for 32S
  142   4    Remaining capacity (mAh) — at 174 for 32S
  146   4    Nominal capacity (mAh) — at 178 for 32S
  150   4    Cycle count — at 182 for 32S
  154   4    Cumulative cycle capacity (mAh) — at 186 for 32S

Protocol version is detected from frame length: JK02_24S frames
are typically 200-250 bytes; JK02_32S are 280-320 bytes. The
24-vs-32 cell count itself is a tell.
"""
from __future__ import annotations

import logging
import struct
import time
from typing import Any

from ..base import DeviceDriver, Section

log = logging.getLogger(__name__)

BALANCING_ACTION = {
    0: "off",
    1: "charging",
    2: "discharging",
}


def _u16le(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _s16le(data: bytes, off: int) -> int:
    return struct.unpack_from("<h", data, off)[0]


def _u32le(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _s32le(data: bytes, off: int) -> int:
    return struct.unpack_from("<i", data, off)[0]


def _parse_cell_info(frame: bytes) -> dict[str, Any]:
    """Decode a JK02 cell-info frame into our normalised field shape.

    Detects 24S vs 32S from the frame length: 32S frames carry 16
    extra bytes (8 extra cell voltages + 8 extra resistances) plus
    a slightly different trailer layout. We use the "offset"
    variable to switch between the two — same approach syssi's C++
    reference takes."""
    out: dict[str, Any] = {}
    if len(frame) < 150:
        out["_errors"] = [f"frame too short ({len(frame)} bytes)"]
        return out

    # 32S frames are ~280-320 bytes; 24S are ~200-250. The boundary
    # is fuzzy in practice, so use 260 as the cutoff — bigger than
    # any 24S frame, smaller than the smallest 32S.
    is_32s = len(frame) >= 260
    cell_count_max = 32 if is_32s else 24
    # syssi uses `offset = 16` for 32S in cell-array math, then
    # doubles it for everything past the resistance array.
    cell_off = 16 if is_32s else 0
    trailer_off = cell_off * 2   # 32 for 32S, 0 for 24S

    # Cell voltages start at offset 6, 2 bytes each.
    cells: list[float] = []
    for i in range(cell_count_max):
        v_mv = _u16le(frame, 6 + i * 2)
        cells.append(v_mv / 1000.0)
    enabled = [v for v in cells if v > 0.5]   # filter "absent" slots
    out["cell_count"] = len(enabled)
    for i, v in enumerate(cells):
        if v > 0.5:
            out[f"cell_voltage_{i}_v"] = round(v, 3)
    if enabled:
        out["cell_min_v"]  = round(min(enabled), 3)
        out["cell_max_v"]  = round(max(enabled), 3)
        out["cell_mean_v"] = round(sum(enabled) / len(enabled), 4)
        out["cell_drift_v"] = round(max(enabled) - min(enabled), 3)

    # Battery pack metrics.
    try:
        total_v = _u32le(frame, 118 + trailer_off) / 1000.0
        current = _s32le(frame, 126 + trailer_off) / 1000.0
        out["voltage_v"] = round(total_v, 3)
        out["current_a"] = round(current, 3)
        out["power_w"]   = round(total_v * current, 2)
    except struct.error:
        pass

    # Temperatures (signed 16-bit, 0.1 °C). Battery has up to 2;
    # MOS temp is a separate field on 32S (replaces error bitmask
    # at the same offset on 24S).
    try:
        out["temperature_0_c"] = _s16le(frame, 130 + trailer_off) * 0.1
        out["temperature_1_c"] = _s16le(frame, 132 + trailer_off) * 0.1
        out["temperature_sensor_count"] = 2
    except struct.error:
        pass
    if is_32s:
        try:
            out["mos_temperature_c"] = _s16le(frame, 112 + trailer_off) * 0.1
        except struct.error:
            pass

    # Errors bitmask. Position varies: 32S puts it where 24S has
    # MOS temp (offset 134+trailer); 24S puts it at offset 136+trailer.
    try:
        if is_32s:
            errors = _u16le(frame, 134 + trailer_off)
        else:
            errors = _u16le(frame, 136 + trailer_off)
        out["alarm_flags"] = int(errors)
    except struct.error:
        pass

    # Balance + SoC + capacity + cycles.
    try:
        out["balancing_current_a"] = _s16le(frame, 138 + trailer_off) * 0.001
        out["balancing_action"]    = BALANCING_ACTION.get(
            int(frame[140 + trailer_off]), "unknown",
        )
        out["soc_pct"]             = int(frame[141 + trailer_off])
        out["remaining_charge_ah"] = round(
            _u32le(frame, 142 + trailer_off) / 1000.0, 3
        )
        out["capacity_ah"]         = round(
            _u32le(frame, 146 + trailer_off) / 1000.0, 3
        )
        out["cycle_count"]         = _u32le(frame, 150 + trailer_off)
        out["total_charge_ah"]     = round(
            _u32le(frame, 154 + trailer_off) / 1000.0, 1
        )
    except struct.error:
        pass

    out["_protocol_version"] = "JK02_32S" if is_32s else "JK02_24S"
    return out


class JkBms(DeviceDriver):
    """JK BMS read-only driver. Wires into the existing scheduler
    + storage layer; per-cell metrics flow into the cell-balance
    panel without per-vendor UI tweaks."""
    vendor_id = "jkbms"
    device_kind = "bms"

    @property
    def sections(self) -> tuple[Section, ...]:
        return ()  # Not a Modbus driver

    async def poll(self, transport) -> dict[str, Any]:
        result: dict[str, Any] = {
            "_vendor":   self.vendor_id,
            "_kind":     self.device_kind,
            "_label":    self.label,
            "_slave_id": self.slave_id,
        }
        if not hasattr(transport, "get_latest_frame"):
            result["_errors"] = [
                "wrong transport type — JK BMS requires ble_jkbms"
            ]
            return result
        frame = transport.get_latest_frame()
        if frame is None:
            result["_errors"] = [
                "no cell-info frame received yet (or stale) — "
                "the BMS should start streaming within ~2s of connect"
            ]
            return result

        parsed = _parse_cell_info(frame)
        result.update(parsed)

        # Freshness for the dashboard.
        latest_at = getattr(transport, "_latest_at", None)
        if latest_at:
            result["frame_age_s"] = max(0, int(time.time() - latest_at))
        return result
