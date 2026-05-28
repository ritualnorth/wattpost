"""Shared helpers + mode-enum for the Deye / Sunsynk / Sol-Ark driver pair."""
from __future__ import annotations


def u16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def s16(b: bytes, off: int) -> int:
    v = u16(b, off)
    return v - 0x10000 if v & 0x8000 else v


def u32(b: bytes, off: int) -> int:
    """Deye 32-bit registers: high-word first, big-endian — opposite
    of Luxpower/EG4's low-high pairing. (Kellerza calls this out
    explicitly; double-check on first probe paste because some
    older Deye firmwares reportedly ship low-first.)"""
    high = u16(b, off)
    low  = u16(b, off + 2)
    return (high << 16) | low


# Device-status enum from holding register 59 (single-phase) /
# 500 (three-phase). Both variants use the same enum. Higher
# transient codes (6/7/...) seen on some older firmwares map to
# "unknown" so the dashboard surfaces the raw code for diagnostics
# without crashing.
MODE_LABELS = {
    0: "standby",
    1: "selfcheck",
    2: "line",          # normal grid-connected / hybrid running
    3: "line",          # alarm (still running, just complaining)
    4: "fault",
    5: "selfcheck",     # activating
    # 6+ : unknown — driver surfaces device_status_code for support
}


def label_for_mode(code: int) -> str:
    return MODE_LABELS.get(code, "unknown")
