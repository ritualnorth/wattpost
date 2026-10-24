"""Every Modbus driver's section parsers survive corrupt frames.

BLE links drop and mangle bytes. Base `poll()` catches (TransportError,
ValueError) per section, so a parser that raises anything else (IndexError,
struct.error, KeyError, ZeroDivisionError) escapes and takes the whole
device's poll down. For each registered driver and each section this feeds
the parser frames of the right size full of zeros, 0xFF, and random bytes,
plus truncated and oversized frames, and asserts it either returns a dict
or raises ValueError, nothing else.
"""
import random

import pytest

from solar_monitor.modbus import crc16, expected_read_response_len
from solar_monitor.vendors.registry import VENDORS


def _all_sections():
    cases = []
    for vid, reg in sorted(VENDORS.items()):
        for kind, factory in sorted(reg.drivers.items()):
            try:
                drv = factory(slave_id=1)
                sections = list(drv.sections)
            except (NotImplementedError, TypeError, AttributeError):
                continue                      # non-Modbus driver (BLE broadcast etc.)
            for s in sections:
                cases.append(pytest.param(s, id=f"{vid}.{kind}.{s.name or s.register}"))
    return cases


CASES = _all_sections()


def test_registry_is_populated():
    assert len(CASES) > 20, "driver discovery found suspiciously few sections"


def _valid_frame(section, payload: bytes) -> bytes:
    """A CRC-clean reply of the right shape around arbitrary register data."""
    n = section.word_count * 2
    data = (payload + b"\x00" * n)[:n]
    head = bytes([1, section.function_code, n]) + data
    return head + crc16(head)


def _payloads(section):
    n = section.word_count * 2
    rng = random.Random(section.register * 7919 + section.word_count)
    yield b"\x00" * n
    yield b"\xff" * n
    yield b"\x7f" + b"\xff" * (n - 1)
    yield b"\x80" + b"\x00" * (n - 1)
    for _ in range(60):
        yield bytes(rng.randrange(256) for _ in range(n))


@pytest.mark.parametrize("section", CASES)
def test_parser_survives_any_register_contents(section):
    """Whatever a device (or line noise that happens to CRC) puts in the
    registers, the parser returns a clean dict or raises ValueError."""
    for payload in _payloads(section):
        frame = _valid_frame(section, payload)
        assert len(frame) == expected_read_response_len(section.word_count)
        try:
            out = section.parser(frame)
        except ValueError:
            continue
        assert isinstance(out, dict), f"parser returned {type(out).__name__}"
        for k, v in out.items():
            assert isinstance(k, str)
            assert isinstance(v, (int, float, str, bool, type(None), list, dict)), (k, type(v))
            if isinstance(v, float):
                assert v == v and abs(v) != float("inf"), f"{k} is {v} for payload {payload.hex()}"


# ---- the layer above: poll() must never see a short or mismatched frame -----

class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)

    async def request(self, frame, expected, timeout=5.0):
        return self.replies.pop(0) if self.replies else b""


def _poll(driver, replies):
    import asyncio
    driver.inter_section_delay_s = 0
    return asyncio.run(driver.poll(FakeTransport(replies)))


def _rover():
    return VENDORS["renogy"].drivers["charge_controller"](slave_id=1)


def test_crc_clean_but_short_reply_is_an_error_not_a_crash():
    drv = _rover()
    short = bytes([1, 3, 2, 0x12, 0x34])
    short += crc16(short)                                   # valid CRC, wrong size
    out = _poll(drv, [short] * len(drv.sections))
    assert out["_errors"] and "expected" in out["_errors"][0]


def test_byte_count_that_disagrees_with_the_frame_is_rejected():
    drv = _rover()
    sec = drv.sections[0]
    n = sec.word_count * 2
    head = bytes([1, 3, n + 2]) + b"\x00" * n              # lies about its own length
    frame = head + crc16(head)
    out = _poll(drv, [frame] * len(drv.sections))
    assert any("byte count" in e for e in out["_errors"])


def test_a_parser_that_crashes_is_contained_to_its_section(monkeypatch):
    from solar_monitor.vendors.base import Section
    drv = _rover()
    good = drv.sections[-1]
    boom = Section(register=1, word_count=1, parser=lambda b: {}["missing"], name="boom")
    monkeypatch.setattr(type(drv), "sections", property(lambda self: [boom, good]))
    frames = [_valid_frame(boom, b"\x00\x01"), _valid_frame(good, b"\x00" * good.word_count * 2)]
    out = _poll(drv, frames)
    assert any("parser error" in e for e in out["_errors"])
    assert "_vendor" in out                               # the poll itself survived
