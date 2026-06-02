"""Renogy Rover / Wanderer / Adventurer charge-controller driver."""
from __future__ import annotations

from ..base import DeviceDriver, Section, WritableSetting
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


def _parse_charge_voltages(bs: bytes) -> dict:
    """Registers 0xE007..0xE00C (6 words), the user-tunable charge
    profile. Voltages stored as deci-volts (X.X V × 10) on Rovers;
    LFP customers commonly need to tune these from the open-lead-acid
    defaults so the bank actually reaches absorption."""
    return {
        "boost_voltage_v":      bytes_to_int(bs, 3, 2, scale=0.1),
        "float_voltage_v":      bytes_to_int(bs, 5, 2, scale=0.1),
        "boost_recovery_v":     bytes_to_int(bs, 7, 2, scale=0.1),
        "equalize_voltage_v":   bytes_to_int(bs, 9, 2, scale=0.1),
        "low_voltage_disconnect_v":  bytes_to_int(bs, 11, 2, scale=0.1),
        "low_voltage_reconnect_v":   bytes_to_int(bs, 13, 2, scale=0.1),
    }


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
            Section(register=0xE007, word_count=6, parser=_parse_charge_voltages, name="charge_voltages"),
        ]

    def writable_settings(self) -> list[WritableSetting]:
        # Conservative ranges: a bit tighter than the Rover's nominal
        # absolute limits, with the goal of "operator error doesn't
        # cycle the bank to death". Real upper bounds are vendor-doc'd
        # at +0.5 V or so above each cap; we trim those to avoid the
        # foot-gun. The low-voltage-disconnect minimum (10.0 V) is
        # below where a flooded lead-acid bank starts taking permanent
        # damage; we expose it for completeness but warn in help_text.
        return [
            WritableSetting(
                key="battery_type",
                label="Battery type",
                kind="enum",
                register=57348,    # 0xE004
                read_from="battery_type",
                choices=(
                    (1, "Flooded / open lead-acid"),
                    (2, "Sealed lead-acid (AGM)"),
                    (3, "Gel"),
                    (4, "Lithium"),
                    (5, "Custom"),
                ),
                help_text=(
                    "Picks the default charge profile. Set to "
                    "Lithium for any LFP / Li-ion bank, open-lead "
                    "defaults will under-charge it."
                ),
            ),
            WritableSetting(
                key="boost_voltage",
                label="Absorption (boost) voltage",
                kind="float",
                register=0xE008,
                read_from="boost_voltage_v",
                units="V",
                min=12.0, max=16.0, step=0.1, scale=0.1,
                help_text=(
                    "Target voltage during the absorption stage. "
                    "Typical LFP: 14.2–14.4 V. Lead-acid: 14.4–14.8 V."
                ),
            ),
            WritableSetting(
                key="float_voltage",
                label="Float voltage",
                kind="float",
                register=0xE009,
                read_from="float_voltage_v",
                units="V",
                min=12.0, max=15.0, step=0.1, scale=0.1,
                help_text=(
                    "Voltage the charger holds at after absorption "
                    "completes. LFP: 13.5 V. Lead-acid: 13.6–13.8 V."
                ),
            ),
            WritableSetting(
                key="low_voltage_disconnect",
                label="Low-voltage disconnect",
                kind="float",
                register=0xE00B,
                read_from="low_voltage_disconnect_v",
                units="V",
                min=10.0, max=12.8, step=0.1, scale=0.1,
                help_text=(
                    "Load output cuts off below this voltage. "
                    "Set too low and lead-acid banks take permanent "
                    "damage; 11.0 V is a safe lower bound."
                ),
            ),
            WritableSetting(
                key="low_voltage_reconnect",
                label="Low-voltage reconnect",
                kind="float",
                register=0xE00C,
                read_from="low_voltage_reconnect_v",
                units="V",
                min=10.5, max=13.5, step=0.1, scale=0.1,
                help_text=(
                    "Load output re-enables once the bank rises to "
                    "this voltage. Must be above the disconnect."
                ),
            ),
        ]
