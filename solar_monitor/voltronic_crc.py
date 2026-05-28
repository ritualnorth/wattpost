"""XMODEM CRC-16 with the Voltronic byte-substitution quirk.

Voltronic inverters (Axpert / MPP Solar / EG4 family) frame every
command and response with a two-byte XMODEM CRC (poly 0x1021,
initial 0x0000) appended big-endian, followed by a trailing 0x0D.

Quirk: if either CRC byte falls on 0x28 ('('), 0x0D ('\\r'), or
0x0A ('\\n') the firmware increments it by one to keep those bytes
free for framing. We have to replicate the bump on both encode and
decode, otherwise valid frames look corrupt.
"""
from __future__ import annotations

_FRAMING_BYTES = (0x28, 0x0D, 0x0A)


def crc16_xmodem(data: bytes) -> int:
    """Standard XMODEM CRC-16 (poly 0x1021, init 0x0000, no reflect)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _bump_framing(b: int) -> int:
    """Voltronic quirk: collide-with-framing byte becomes that byte + 1."""
    return b + 1 if b in _FRAMING_BYTES else b


def voltronic_crc(data: bytes) -> bytes:
    """Return the two CRC bytes Voltronic firmware expects, big-endian,
    with the framing-byte bump applied to each half."""
    crc = crc16_xmodem(data)
    hi = _bump_framing((crc >> 8) & 0xFF)
    lo = _bump_framing(crc & 0xFF)
    return bytes([hi, lo])


def frame_command(cmd: str) -> bytes:
    """Encode an ASCII command (e.g. 'QPIGS') into the on-wire frame:
    payload + CRC bytes + 0x0D."""
    payload = cmd.encode("ascii")
    return payload + voltronic_crc(payload) + b"\r"


def verify_and_strip(frame: bytes) -> bytes:
    """Validate a received frame and return the payload between the
    leading '(' and the trailing CRC. Raises ValueError on a CRC or
    framing mismatch."""
    if not frame:
        raise ValueError("empty frame")
    # Trim trailing 0x0D if present — the HID reader usually returns
    # the bytes up to and including the CR, but be tolerant.
    if frame.endswith(b"\r"):
        frame = frame[:-1]
    if len(frame) < 3:
        raise ValueError(f"frame too short ({len(frame)} bytes)")
    payload, supplied = frame[:-2], frame[-2:]
    expected = voltronic_crc(payload)
    if supplied != expected:
        raise ValueError(
            f"CRC mismatch: payload {payload!r} expected {expected.hex()} "
            f"got {supplied.hex()}"
        )
    return payload
