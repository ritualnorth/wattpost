"""The heartbeat's bank-level extras feed the cloud API's Home Assistant sensors."""
from solar_monitor.cloud.service import _bank_extras


def test_copies_numeric_bank_readings():
    out = _bank_extras({"voltage_v": 13.2849, "current_a": -4.5, "temperature_c": 21.0,
                        "remaining_ah": 150.126, "soc_pct": 80.0, "capacity_ah": 200})
    assert out == {"voltage_v": 13.285, "current_a": -4.5, "temperature_c": 21.0, "remaining_ah": 150.126}


def test_remaining_ah_is_derived_from_capacity_and_soc_when_missing():
    assert _bank_extras({"capacity_ah": 200, "soc_pct": 50.0})["remaining_ah"] == 100.0


def test_missing_or_bad_values_are_omitted_not_sent_as_junk():
    out = _bank_extras({"voltage_v": None, "current_a": "x", "temperature_c": float("nan"),
                        "remaining_ah": True, "capacity_ah": 0, "soc_pct": 50})
    assert out == {}
