"""Known-answer tests: Modbus framing and the Renogy Rover charging block.

Pins the exact bytes and the exact scaling so a refactor can't silently change
what a reading means. The CRC vectors are the standard Modbus RTU examples.
"""
from solar_monitor.modbus import (
    build_read_holding, build_read_input, crc16, expected_read_response_len, verify_response,
)
from solar_monitor.vendors.renogy.rover import RenogyRover

import pytest


def test_crc16_standard_vectors():
    # Read holding register 0x0000, count 1, slave 1 -> CRC 84 0A (well-known example)
    assert crc16(bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01])).hex() == "840a"
    # Read 2 registers from 0x006B on slave 0x11 -> 76 87
    assert crc16(bytes([0x11, 0x03, 0x00, 0x6B, 0x00, 0x03])).hex() == "7687"
    assert crc16(b"").hex() == "ffff"


def test_request_frames():
    f = build_read_holding(1, 0x0100, 34)
    assert f[:6].hex() == "010301000022" and f[6:] == crc16(f[:6])
    f = build_read_input(1, 0x3100, 2)
    assert f[:6].hex() == "010431000002" and f[6:] == crc16(f[:6])
    assert expected_read_response_len(34) == 1 + 1 + 1 + 68 + 2


def _reply(slave, fc, data):
    head = bytes([slave, fc, len(data)]) + data
    return head + crc16(head)


def test_verify_response_accepts_exact_and_rejects_everything_else():
    ok = _reply(1, 3, b"\x00\x01\x00\x02")
    verify_response(ok, 1, 3, expected_len=9)
    bad = {
        "wrong slave":        lambda: verify_response(ok, 2, 3, expected_len=9),
        "wrong fc":           lambda: verify_response(ok, 1, 4, expected_len=9),
        "too long expected":  lambda: verify_response(ok, 1, 3, expected_len=11),
        "too short expected": lambda: verify_response(ok, 1, 3, expected_len=7),
        "crc":                lambda: verify_response(ok[:-1] + b"\x00", 1, 3, expected_len=9),
        "too short":          lambda: verify_response(b"\x01\x03", 1, 3),
        "exception":          lambda: verify_response(_reply(1, 0x83, b"\x02"), 1, 3),
    }
    for name, call in bad.items():
        with pytest.raises(ValueError):
            call()


def test_byte_count_lie_is_rejected_even_with_valid_crc_and_length():
    head = bytes([1, 3, 99]) + b"\x00" * 4          # 9 bytes total, byte count says 99
    with pytest.raises(ValueError, match="byte count"):
        verify_response(head + crc16(head), 1, 3, expected_len=9)


def _rover_block(words: dict[int, int]) -> bytes:
    """A 34-register reply with the given register offsets (0-based, from 0x100) set."""
    data = bytearray(68)
    for off, val in words.items():
        data[off * 2:off * 2 + 2] = val.to_bytes(2, "big")
    return _reply(1, 3, bytes(data))


def test_rover_charging_info_scaling_and_fields():
    # offsets are registers from 0x0100: 0 SOC, 1 batt V*10, 2 batt I*100, 3 temps,
    # 4 load V*10, 5 load I*100, 6 load W, 7 PV V*10, 8 PV I*100, 9 PV W,
    # 15 max charge W today, 19 energy today Wh
    block = _rover_block({
        0: 87, 1: 134, 2: 1250, 4: 133, 5: 310, 6: 41, 7: 412, 8: 880, 9: 362,
        15: 540, 19: 1830,
    })
    sec = {s.name: s for s in RenogyRover(slave_id=1).sections}["charging_info"]
    out = sec.parser(block)
    assert out["battery_percentage"] == 87
    assert out["battery_voltage_v"] == pytest.approx(13.4)
    assert out["battery_current_a"] == pytest.approx(12.50)
    assert out["load_voltage_v"] == pytest.approx(13.3)
    assert out["load_current_a"] == pytest.approx(3.10)
    assert out["load_power_w"] == 41
    assert out["pv_voltage_v"] == pytest.approx(41.2)
    assert out["pv_current_a"] == pytest.approx(8.80)
    assert out["pv_power_w"] == 362
    assert out["max_charging_power_today_w"] == 540
    assert out["energy_today_wh"] == 1830


def test_rover_poll_end_to_end_with_a_fake_bus():
    import asyncio
    drv = RenogyRover(slave_id=1)
    drv.inter_section_delay_s = 0

    class Bus:
        def __init__(self):
            self.n = 0

        async def request(self, frame, expected, timeout=5.0):
            sec = drv.sections[self.n]; self.n += 1
            if sec.name == "charging_info":
                return _rover_block({0: 55, 1: 128, 9: 100})
            return _reply(1, 3, b"\x00" * (sec.word_count * 2))

    out = asyncio.run(drv.poll(Bus()))
    assert out["_vendor"] == "renogy" and out["_kind"] == "charge_controller"
    assert out["battery_percentage"] == 55 and out["pv_power_w"] == 100
    assert "_errors" not in out
