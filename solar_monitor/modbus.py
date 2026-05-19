"""Modbus RTU framing primitives.

Drivers build frames here; transports ship them. Keeping framing out of both
sides means a serial transport can verify CRC on the way in for free, and BLE
transports can rely on the device's built-in checksum if needed.
"""
from __future__ import annotations


def crc16(data: bytes) -> bytes:
    """Modbus RTU CRC16 (poly 0xA001), little-endian output."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_read_holding(slave_id: int, register: int, count: int) -> bytes:
    """Build a Modbus 'Read Holding Registers' (function 3) request frame."""
    head = bytes([
        slave_id, 3,
        (register >> 8) & 0xFF, register & 0xFF,
        (count >> 8) & 0xFF, count & 0xFF,
    ])
    return head + crc16(head)


def build_read_input(slave_id: int, register: int, count: int) -> bytes:
    """Build a Modbus 'Read Input Registers' (function 4) request frame.

    Distinct from FC03 (holding registers). EPEVER Tracer-family MPPTs
    use FC04 for live state (V/I/W) and FC03 only for setpoints, while
    Renogy uses FC03 for everything. expected_read_response_len() and
    verify_response(expected_fc=4) finish the round-trip."""
    head = bytes([
        slave_id, 4,
        (register >> 8) & 0xFF, register & 0xFF,
        (count >> 8) & 0xFF, count & 0xFF,
    ])
    return head + crc16(head)


def build_write_single(slave_id: int, register: int, value: int) -> bytes:
    """Build a Modbus 'Write Single Register' (function 6) request frame.

    Used for one-register writes like the Renogy Rover load-control
    register (0x010A: 0=off, 1=on). FC06 echoes the request on success;
    a successful response is the same 8 bytes back. Multi-register
    writes (FC16) aren't supported here yet — add when a vendor needs
    them."""
    if not (0 <= value <= 0xFFFF):
        raise ValueError(f"value {value} out of range for FC06 (must fit in 16 bits)")
    head = bytes([
        slave_id, 6,
        (register >> 8) & 0xFF, register & 0xFF,
        (value >> 8) & 0xFF, value & 0xFF,
    ])
    return head + crc16(head)


def expected_read_response_len(word_count: int) -> int:
    """How many bytes function-3 reply should be: id + fc + byte_count + data + crc."""
    return 1 + 1 + 1 + (word_count * 2) + 2


# FC06 success response echoes the 8-byte request verbatim.
EXPECTED_WRITE_SINGLE_RESPONSE_LEN = 8


def verify_response(resp: bytes, slave_id: int, expected_fc: int = 3) -> None:
    """Raise ValueError if the response doesn't look like a clean reply.

    `expected_fc` defaults to 3 (read holding) for backwards compat with
    every existing caller; pass 6 for FC06 write-single. Exception
    responses have the high bit set on the function code (0x83 for
    FC03 errors, 0x86 for FC06 errors)."""
    if len(resp) < 5:
        raise ValueError(f"short response ({len(resp)} bytes)")
    if resp[0] != slave_id:
        raise ValueError(f"wrong slave id: expected {slave_id}, got {resp[0]}")
    err_code = expected_fc | 0x80
    if resp[1] == err_code:
        raise ValueError(f"modbus error response, code={resp[2] if len(resp) > 2 else '?'}")
    if resp[1] != expected_fc:
        raise ValueError(f"unexpected function code 0x{resp[1]:02x} "
                         f"(expected 0x{expected_fc:02x})")
    # CRC verify (optional — most transports already do this implicitly)
    if crc16(resp[:-2]) != resp[-2:]:
        raise ValueError("CRC mismatch")
