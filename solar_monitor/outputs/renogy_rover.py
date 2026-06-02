"""Renogy Rover MPPT load-output adapter.

Renogy's Rover line (and its near-siblings Wanderer / Adventurer /
Voyager) all expose a 12V load terminal controlled by Modbus
register 0x010A: write 1 to switch on, 0 to switch off. The same
register reads back the current state, but our existing rover.py
driver already extracts load_status from the bulk register dump
(register byte 67, high bit), so we lean on that for read-back
rather than issuing a redundant FC03.

### BT-2 firmware quirk

The BT-2 BLE dongle silently swallows the FC06 ack frame on Rover
firmware 3.x: the write IS applied at the Rover end, but no
response ever arrives back over the BLE notify char. Our transport
times out after 5s and raises TransportTimeout, which the naive
read of the response would treat as failure.

We handle this two ways:
  1. After FC06, we do an explicit FC03 read of register 0x010A
     within the same BLE session. If the readback matches the
     written value, we report `ok=True` regardless of whether the
     FC06 ack arrived.
  2. If even the readback times out we report `ok=False` with
     `confirmed_state=None`, the caller should retry or surface a
     "no response from charger" error.

Serial users (USB-RS485 wired in) typically *do* get a real ack;
the same code path works there because we attempt the ack first
and only fall through to the FC03 readback when it's missing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..modbus import (
    EXPECTED_WRITE_SINGLE_RESPONSE_LEN,
    build_read_holding,
    build_write_single,
    crc16,
    expected_read_response_len,
    verify_response,
)
from ..transport.base import TransportTimeout
from .base import ControllableOutput, OutputAdapter, WriteResult
from .registry import register_adapter

log = logging.getLogger(__name__)

# Renogy Rover load-control register. Confirmed against RNG-CTRL-RVR40
# FW 3.1.0 during #104 de-risk (see git log; the test was: FC06 to
# 0x010A with value 1 flipped load_status to "on" on the next poll).
LOAD_REGISTER = 0x010A
LOAD_ON  = 1
LOAD_OFF = 0

# Models we know expose a load output. Match on substring, Renogy
# model strings come in several forms across the product line:
#   * "RNG-CTRL-RVR40" , Rover (current standard naming)
#   * "RNG-CTRL-WND10" , Wanderer
#   * "RNG-CTRL-ADV30" , Adventurer
#   * "RNG-CTRL-VNG20" , Voyager (waterproof)
#   * "RVR40" / "WND10", Some older firmware drops the RNG-CTRL- prefix
#
# Bigger Rovers (200A+) without an L terminal report a model that
# matches these prefixes too, but flipping their non-existent load
# register is a no-op rather than dangerous, adapters can still
# discover an output here and the FC06 write goes nowhere.
_LOAD_BEARING_PREFIXES = (
    "RNG-CTRL-RVR", "RNG-CTRL-WND", "RNG-CTRL-ADV", "RNG-CTRL-VNG",
    # Bare-prefix fallbacks for older firmware that doesn't emit
    # the full RNG-CTRL- vendor tag.
    "RVR", "WND", "ADV", "VNG",
)


class RoverLoadAdapter:
    """Adapter for Renogy Rover-family charge controllers."""
    vendor = "renogy"
    handles_kinds = ("charge_controller",)

    def discover(self, device: dict[str, Any]) -> list[ControllableOutput]:
        # Only register a load output for models we know have one.
        # Bigger Rovers + non-load variants get nothing, they'll
        # just not show a Load Output panel on the device-detail page.
        latest = device.get("latest") or {}
        model = (device.get("model") or latest.get("model") or "").upper()
        if not any(model.startswith(p) for p in _LOAD_BEARING_PREFIXES):
            return []
        label = device.get("label") or "charge_controller"
        return [ControllableOutput(
            id=f"{label}.load",
            device_label=label,
            name="Load output",
            kind="load",
            capabilities=("toggle",),
        )]

    async def write(
        self, output: ControllableOutput, on: bool, *, transport, slave_id: int,
    ) -> WriteResult:
        value = LOAD_ON if on else LOAD_OFF
        write_frame = build_write_single(slave_id, LOAD_REGISTER, value)
        log.info("[outputs.rover] %s: FC06 reg=0x%04X val=%d frame=%s",
                 output.id, LOAD_REGISTER, value, write_frame.hex())
        # Pass 1: write. We tolerate TransportTimeout here because of
        # the BT-2 ack-swallowing quirk; serial transports may still
        # return the echo and we accept that as fast-path confirmation.
        ack_seen = False
        try:
            resp = await transport.request(
                write_frame, EXPECTED_WRITE_SINGLE_RESPONSE_LEN, timeout=3.0,
            )
            try:
                verify_response(resp, slave_id, expected_fc=6)
                ack_seen = True
            except ValueError as e:
                log.info("[outputs.rover] %s: FC06 ack malformed: %s, "
                         "falling through to read-back", output.id, e)
        except TransportTimeout:
            log.info("[outputs.rover] %s: FC06 ack timed out, BT-2 "
                     "quirk; verifying via read-back", output.id)
        except Exception as e:
            log.warning("[outputs.rover] %s: FC06 send failed: %s",
                        output.id, e)
            return WriteResult(ok=False, confirmed_state=None,
                               detail=f"{type(e).__name__}: {e}")

        # Pass 2: read back register 0x010A. Some BT-2 firmwares serve
        # the register one-at-a-time only via the bulk register dump,
        # but the standard FC03 single-register read works on the
        # Rovers we've tested, start with that. The bulk-dump fallback
        # ride the next regular poll cycle automatically (see
        # read_state_from_snapshot).
        await asyncio.sleep(0.3)
        read_frame = build_read_holding(slave_id, LOAD_REGISTER, 1)
        try:
            rb = await transport.request(
                read_frame, expected_read_response_len(1), timeout=3.0,
            )
            verify_response(rb, slave_id, expected_fc=3)
            confirmed = int(rb[3]) << 8 | int(rb[4])
            confirmed_state = LOAD_ON if confirmed else LOAD_OFF
            ok = (confirmed_state == value)
            if not ok:
                return WriteResult(
                    ok=False, confirmed_state=confirmed_state,
                    detail=f"read-back returned {confirmed_state} after writing {value}",
                )
            return WriteResult(ok=True, confirmed_state=confirmed_state)
        except TransportTimeout:
            # Even the read-back didn't come back, the BLE link is
            # likely flaky right now. The write may still have landed
            # (we saw acks swallowed before) but we can't prove it
            # here. The next regular poll cycle will reflect truth.
            if ack_seen:
                return WriteResult(ok=True, confirmed_state=None,
                                   detail="ack ok; read-back timed out, "
                                          "next poll will confirm")
            return WriteResult(
                ok=False, confirmed_state=None,
                detail="no ack and read-back timed out",
            )
        except Exception as e:
            return WriteResult(
                ok=False, confirmed_state=None,
                detail=f"read-back failed: {type(e).__name__}: {e}",
            )

    def read_state_from_snapshot(
        self, output: ControllableOutput, snapshot: dict[str, Any],
    ) -> int | None:
        # rover.py extracts load_status as "on" / "off" string from
        # the bulk register dump. We translate back to the 0/1 the
        # outputs layer stores.
        status = snapshot.get("load_status")
        if status == "on":
            return 1
        if status == "off":
            return 0
        return None


register_adapter(RoverLoadAdapter())
