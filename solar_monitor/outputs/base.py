"""Output abstraction — the contract every per-vendor adapter implements.

A vendor adapter (e.g. RoverLoadAdapter for Renogy Rover MPPTs) does
three things:
  1. Decides whether a given device exposes any controllable outputs
     (model-string match, BMS feature flag, etc.).
  2. Issues the actual write — FC06 for Modbus vendors, the JK BMS
     proprietary command for JK, an MQTT publish for smart-plug
     bridges, and so on.
  3. Reports the latest read-back state from the device's poll data so
     the UI's toggle reflects measured truth, not optimistic state.

The base layer (this module) knows nothing about vendors. Read-back
goes through the `read_state_from_snapshot` hook so we don't have to
spin extra BLE traffic on top of the existing poll cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ControllableOutput:
    """Static definition of an output. Mutable state (current value,
    last command, safety_confirmed) lives in the SQLite table — this
    record is what the adapter exposes at discovery time."""
    id: str                       # stable: "<device_label>.<kind>"
    device_label: str             # the parent device
    name: str                     # user-visible label
    kind: str                     # "load" | "charge_mos" | "discharge_mos" | ...
    capabilities: tuple[str, ...] = ("toggle",)


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a write attempt. Whether the BLE/serial layer round-
    tripped an ack, plus the post-write read-back if the adapter could
    perform one synchronously (e.g. an explicit FC03 read right after
    the FC06 write)."""
    ok: bool
    confirmed_state: int | None   # 0/1 after read-back, None if not checked yet
    detail: str = ""              # human-readable reason on failure


class OutputAdapter(Protocol):
    """Per-vendor adapter. Implementations register themselves with
    `register_adapter` keyed on the device kind they handle. The
    service walks every known device and asks each registered adapter
    to enumerate the outputs it cares about — Renogy adapter answers
    for `charge_controller`, JK BMS adapter for `bms`, etc."""

    vendor: str
    handles_kinds: tuple[str, ...]

    def discover(self, device: dict[str, Any]) -> list[ControllableOutput]:
        """Inspect a device snapshot from the latest poll. Return zero
        or more outputs to register. Pure / no I/O — runs every boot."""

    async def write(
        self, output: ControllableOutput, on: bool, *, transport, slave_id: int,
    ) -> WriteResult:
        """Apply the new state to the physical device. May time out
        waiting for an ack (see Rover BT-2 quirk in #104 de-risk
        notes) — adapters that know their device swallows acks should
        return ok=True with confirmed_state from a follow-up read."""

    def read_state_from_snapshot(
        self, output: ControllableOutput, snapshot: dict[str, Any],
    ) -> int | None:
        """Extract the output's current state (0/1) from a device's
        post-poll snapshot. None when the snapshot doesn't carry the
        relevant field yet (cold start, missed poll). The service
        calls this on every poll cycle to keep state_at fresh."""
