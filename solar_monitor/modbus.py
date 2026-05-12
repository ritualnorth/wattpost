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


def expected_read_response_len(word_count: int) -> int:
    """How many bytes function-3 reply should be: id + fc + byte_count + data + crc."""
    return 1 + 1 + 1 + (word_count * 2) + 2


def verify_response(resp: bytes, slave_id: int) -> None:
    """Raise ValueError if the response doesn't look like a clean function-3 reply."""
    if len(resp) < 5:
        raise ValueError(f"short response ({len(resp)} bytes)")
    if resp[0] != slave_id:
        raise ValueError(f"wrong slave id: expected {slave_id}, got {resp[0]}")
    if resp[1] == 0x83:
        raise ValueError(f"modbus error response, code={resp[2] if len(resp) > 2 else '?'}")
    if resp[1] != 3:
        raise ValueError(f"unexpected function code 0x{resp[1]:02x}")
    # CRC verify (optional — most transports already do this implicitly)
    if crc16(resp[:-2]) != resp[-2:]:
        raise ValueError("CRC mismatch")
