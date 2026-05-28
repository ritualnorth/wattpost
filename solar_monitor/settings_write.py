"""Per-device settings write path (#111 phase 2).

Wraps Modbus FC06 with the same BT-2 ack-swallowing fallback the
Rover load-output adapter has been using in production since #104,
the BT-2 BLE dongle silently swallows the FC06 ack on Rover
firmware 3.x, so we always fall through to an explicit FC03 read-
back of the register we just wrote to confirm the change landed.

Public surface is one async function:

    await write_setting_register(transport, slave_id, register, value)
        -> dict with ok / confirmed_value / detail
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .modbus import (
    EXPECTED_WRITE_SINGLE_RESPONSE_LEN,
    build_read_holding,
    build_write_single,
    expected_read_response_len,
    verify_response,
)
from .transport.base import TransportTimeout


log = logging.getLogger(__name__)


async def write_setting_register(
    transport: Any, slave_id: int, register: int, value: int,
    *, read_back: bool = True,
) -> dict[str, Any]:
    """FC06 write + optional FC03 read-back. Returns:
        {"ok": bool, "confirmed_value": int | None, "detail": str | None}

    `ok=True` means we confirmed the new value via read-back (or the
    FC06 ack arrived AND read-back timed out, same fallback the
    Rover load-output uses to tolerate the BT-2 ack-swallowing
    firmware quirk).

    `confirmed_value` is the register's current value as read back
    after the write, or None if the read-back timed out.
    """
    frame = build_write_single(slave_id, register, value)
    log.info(
        "settings_write: slave=%d reg=0x%04X val=%d frame=%s",
        slave_id, register, value, frame.hex(),
    )

    # Pass 1: write. Tolerate ack timeout because of the BT-2 quirk.
    ack_seen = False
    try:
        resp = await transport.request(
            frame, EXPECTED_WRITE_SINGLE_RESPONSE_LEN, timeout=3.0,
        )
        try:
            verify_response(resp, slave_id, expected_fc=6)
            ack_seen = True
        except ValueError as e:
            log.info(
                "settings_write: FC06 ack malformed (%s), falling "
                "through to read-back", e,
            )
    except TransportTimeout:
        # Ack timeout has two distinct causes:
        #   1. BT-2 firmware 3.x silently swallows FC06 acks on Rover
        #      writes (the original reason this fallback exists).
        #   2. A real RS-485 read-back timeout on a noisy bus.
        # Both resolve the same way: try the FC03 read-back below.
        # Used to log "BT-2 quirk" unconditionally which misled
        # USB-RS485 users in support tickets (#116).
        log.info(
            "settings_write: FC06 ack timed out, falling through to read-back"
        )
    except Exception as e:
        log.warning("settings_write: FC06 send failed: %s", e)
        return {"ok": False, "confirmed_value": None,
                "detail": f"{type(e).__name__}: {e}"}

    if not read_back:
        # Caller opted out (e.g. write-only register that returns
        # garbage to FC03). Trust the ack.
        return {
            "ok": ack_seen,
            "confirmed_value": None,
            "detail": None if ack_seen
                      else "no ack and read-back disabled, write may have landed",
        }

    # Pass 2: read back the register we just wrote.
    await asyncio.sleep(0.3)
    read_frame = build_read_holding(slave_id, register, 1)
    try:
        rb = await transport.request(
            read_frame, expected_read_response_len(1), timeout=3.0,
        )
        verify_response(rb, slave_id, expected_fc=3)
        confirmed = (int(rb[3]) << 8) | int(rb[4])
        ok = (confirmed == value)
        if not ok:
            return {
                "ok": False,
                "confirmed_value": confirmed,
                "detail": (
                    f"read-back returned {confirmed} after writing "
                    f"{value}, device may have clamped to a safe range"
                ),
            }
        return {"ok": True, "confirmed_value": confirmed, "detail": None}
    except TransportTimeout:
        if ack_seen:
            return {
                "ok": True, "confirmed_value": None,
                "detail": "ack ok; read-back timed out, next poll will confirm",
            }
        return {
            "ok": False, "confirmed_value": None,
            "detail": "no ack and read-back timed out",
        }
    except Exception as e:
        return {
            "ok": False, "confirmed_value": None,
            "detail": f"read-back failed: {type(e).__name__}: {e}",
        }
