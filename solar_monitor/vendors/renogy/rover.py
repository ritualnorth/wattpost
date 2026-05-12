"""Renogy Rover / Wanderer / Adventurer charge-controller driver."""
from __future__ import annotations

from ..base import DeviceDriver, Section
from ._util import bytes_to_int, parse_byte_temperature_c

CHARGING_STATE = {
    0: "deactivated", 1: "activated", 2: "mppt",
    3: "equalizing", 4: "boost", 5: "floating", 6: "current_limiting",
}
LOAD_STATE = {0: "off", 1: "on"}
BATTERY_TYPE = {1: "open", 2: "sealed", 3: "gel", 4: "lithium", 5: "custom"}


def _parse_device_info(bs: bytes) -> dict:
    return {"model": bs[3:19].decode("utf-8", errors="replace").strip()}


def _parse_device_address(bs: bytes) -> dict:
    return {"device_id": int(bytes_to_int(bs, 4, 1))}


def _parse_versions(bs: bytes) -> dict:
    """Reg 20-25 = sw + hw version (4 bytes each, big-endian semver-ish).

    The format Renogy ships is: byte0 reserved, byte1 = major, byte2 = minor,
    byte3 = patch (often 0). We surface a pretty 'X.Y.Z' string and the raw
    bytes for any consumer who wants them.
    """
    # bytes 3..6 = sw_version (4 bytes), bytes 7..10 = hw_version (4 bytes),
    # bytes 11..14 = serial (4 bytes)
    if len(bs) < 15:
        return {}
    sw = bs[3:7]
    hw = bs[7:11]
    serial = bs[11:15]
    return {
        "firmware_version": f"{sw[1]}.{sw[2]}.{sw[3]}",
        "hardware_version": f"{hw[1]}.{hw[2]}.{hw[3]}",
        "serial": serial.hex().upper(),
    }


def _parse_charging_info(bs: bytes) -> dict:
    return {
        "battery_percentage": int(bytes_to_int(bs, 3, 2)),
        "battery_voltage_v": bytes_to_int(bs, 5, 2, scale=0.1),
        "battery_current_a": bytes_to_int(bs, 7, 2, scale=0.01),
        "battery_temperature_c": parse_byte_temperature_c(int(bytes_to_int(bs, 10, 1))),
        "controller_temperature_c": parse_byte_temperature_c(int(bytes_to_int(bs, 9, 1))),
        "load_status": LOAD_STATE.get(int(bytes_to_int(bs, 67, 1)) >> 7),
        "load_voltage_v": bytes_to_int(bs, 11, 2, scale=0.1),
        "load_current_a": bytes_to_int(bs, 13, 2, scale=0.01),
        "load_power_w": int(bytes_to_int(bs, 15, 2)),
        "pv_voltage_v": bytes_to_int(bs, 17, 2, scale=0.1),
        "pv_current_a": bytes_to_int(bs, 19, 2, scale=0.01),
        "pv_power_w": int(bytes_to_int(bs, 21, 2)),
        "max_charging_power_today_w": int(bytes_to_int(bs, 33, 2)),
        "max_discharging_power_today_w": int(bytes_to_int(bs, 35, 2)),
        "charging_ah_today": int(bytes_to_int(bs, 37, 2)),
        "discharging_ah_today": int(bytes_to_int(bs, 39, 2)),
        "energy_today_wh": int(bytes_to_int(bs, 41, 2)),
        "consumption_today_wh": int(bytes_to_int(bs, 43, 2)),
        "energy_total_wh": int(bytes_to_int(bs, 59, 4)),
        "charging_state": CHARGING_STATE.get(int(bytes_to_int(bs, 68, 1))),
    }


def _parse_battery_type(bs: bytes) -> dict:
    return {"battery_type": BATTERY_TYPE.get(int(bytes_to_int(bs, 3, 2)))}


class RenogyRover(DeviceDriver):
    vendor_id = "renogy"
    device_kind = "charge_controller"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=12, word_count=8, parser=_parse_device_info, name="device_info"),
            Section(register=20, word_count=6, parser=_parse_versions, name="versions"),
            Section(register=26, word_count=1, parser=_parse_device_address, name="device_address"),
            Section(register=256, word_count=34, parser=_parse_charging_info, name="charging_info"),
            Section(register=57348, word_count=1, parser=_parse_battery_type, name="battery_type"),
        ]
