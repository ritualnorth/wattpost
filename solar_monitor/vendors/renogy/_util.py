"""Shared parser helpers for Renogy Modbus responses.

All values are normalized to SI units (V, A, °C, Ah, W, %). The UI handles
unit conversion for display.
"""
from __future__ import annotations


def bytes_to_int(bs: bytes, offset: int, length: int, *, signed: bool = False, scale: float = 1.0) -> float:
    """Big-endian int decode with scale, mirroring upstream renogy-bt semantics."""
    if len(bs) < offset + length:
        return 0.0
    return round(int.from_bytes(bs[offset : offset + length], byteorder="big", signed=signed) * scale, 4)


def parse_byte_temperature_c(raw: int) -> float:
    """Decode the Rover's quirky 1-byte temperature (bit 7 = sign)."""
    sign = raw >> 7
    return -(raw - 128) if sign == 1 else raw
