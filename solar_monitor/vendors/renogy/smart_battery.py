"""Renogy LFP Smart Battery driver (RBT100LFP12S-G1 and family)."""
from __future__ import annotations

from ..base import DeviceDriver, Section
from ._util import bytes_to_int


def _parse_cell_voltages(bs: bytes) -> dict:
    count = int(bytes_to_int(bs, 3, 2))
    out: dict = {"cell_count": count}
    for i in range(count):
        out[f"cell_voltage_{i}_v"] = bytes_to_int(bs, 5 + i * 2, 2, scale=0.1)
    return out


def _parse_cell_temperatures_c(bs: bytes) -> dict:
    count = int(bytes_to_int(bs, 3, 2))
    out: dict = {"temperature_sensor_count": count}
    for i in range(count):
        out[f"temperature_{i}_c"] = bytes_to_int(bs, 5 + i * 2, 2, signed=True, scale=0.1)
    return out


def _parse_battery_info(bs: bytes) -> dict:
    return {
        "current_a": bytes_to_int(bs, 3, 2, signed=True, scale=0.01),
        "voltage_v": bytes_to_int(bs, 5, 2, scale=0.1),
        "remaining_charge_ah": bytes_to_int(bs, 7, 4, scale=0.001),
        "capacity_ah": bytes_to_int(bs, 11, 4, scale=0.001),
    }


def _parse_device_info(bs: bytes) -> dict:
    return {"model": bs[3:19].decode("utf-8", errors="replace").rstrip("\x00").strip()}


def _parse_device_address(bs: bytes) -> dict:
    return {"device_id": int(bytes_to_int(bs, 3, 2))}


def _parse_firmware(bs: bytes) -> dict:
    """Reg 5117-5120: 4 words. First word often 0xFFFF (unused), trailing
    bytes are an ASCII firmware string like '010001' → v01.00.01."""
    if len(bs) < 11:
        return {}
    # Strip raw 0xFF / 0x00 padding *before* decode so they don't become �.
    trimmed = bs[3:11].lstrip(b"\xff").rstrip(b"\x00")
    fw_ascii = trimmed.decode("ascii", errors="replace").strip()
    out: dict = {"firmware_version_raw": fw_ascii}
    # If the trimmed string is exactly 6 ASCII digits, format as X.Y.Z.
    if len(fw_ascii) == 6 and fw_ascii.isdigit():
        out["firmware_version"] = f"{fw_ascii[0:2]}.{fw_ascii[2:4]}.{fw_ascii[4:6]}"
    return out


def _parse_serial(bs: bytes) -> dict:
    """Reg 5120-5127: 8 words = 16 ASCII chars of serial number."""
    if len(bs) < 19:
        return {}
    s = bs[3:19].decode("ascii", errors="replace").rstrip("\x00").strip()
    return {"serial": s}


class RenogySmartBattery(DeviceDriver):
    vendor_id = "renogy"
    device_kind = "smart_battery"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=5000, word_count=17, parser=_parse_cell_voltages, name="cell_voltages"),
            Section(register=5017, word_count=17, parser=_parse_cell_temperatures_c, name="cell_temperatures"),
            Section(register=5042, word_count=6,  parser=_parse_battery_info, name="battery_info"),
            Section(register=5117, word_count=4,  parser=_parse_firmware, name="firmware"),
            Section(register=5120, word_count=8,  parser=_parse_serial, name="serial"),
            Section(register=5122, word_count=8,  parser=_parse_device_info, name="device_info"),
            Section(register=5223, word_count=1,  parser=_parse_device_address, name="device_address"),
        ]
