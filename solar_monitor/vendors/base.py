"""DeviceDriver and Vendor base classes.

A DeviceDriver describes *one type of device* (e.g. Renogy Rover charge
controller, Victron SmartShunt). The driver:

  - declares a list of Modbus read sections (or, for non-Modbus protocols,
    overrides `poll` directly)
  - knows how to parse each section's bytes into a normalized dict
  - exposes a stable `vendor_id` / `device_kind` pair so the orchestrator,
    storage, and UI can route data sanely.

Normalized output is key: a `battery_voltage` reading from Renogy and Victron
should land under the same key in the result dict so the UI doesn't need to
care which vendor a number came from.
"""
from __future__ import annotations

import abc
from typing import Callable, Sequence

import msgspec

from ..modbus import (
    build_read_holding,
    build_read_input,
    expected_read_response_len,
    verify_response,
)
from ..transport import Transport, TransportError


class Section(msgspec.Struct, frozen=True):
    """One Modbus read + parser.

    `function_code` selects FC03 (read holding registers, default,
    Renogy + most vendors) or FC04 (read input registers, EPEVER and
    the Tracer family use FC04 for live state). Other values are
    rejected at poll time."""

    register: int
    word_count: int
    parser: Callable[[bytes], dict]
    name: str = ""  # for log/debug; optional
    function_code: int = 3


class VendorInfo(msgspec.Struct, frozen=True):
    id: str           # short stable id, e.g. "renogy"
    display_name: str
    description: str = ""


class WritableSetting(msgspec.Struct, frozen=True):
    """A device-side setting the user can read (and, phase-2, change).

    Drivers declare these from `writable_settings()`; the daemon
    surfaces them via `/api/devices/{label}/settings` so the UI can
    render a Settings panel on the device detail page without
    hard-coding any vendor knowledge.

    Fields:
      key            stable id, used in API paths + UI form names.
      label          human-readable name ("Battery type", "Float voltage").
      kind           "enum" | "float" | "int". Drives UI input shape.
      register       Modbus holding register the write hits (FC06).
      read_from      snapshot field name to pull the current value from
                     the latest poll. None when the value isn't already
                     in the bulk read, phase 2 will add an explicit
                     read for those.
      units          display-only ("V", "A", "°C", ""), no scaling.
      choices        for kind="enum": (value, label) pairs.
      min / max      for kind="float" / "int": validation clamps.
      step           UI hint for numeric inputs.
      scale          register-int ↔ user-facing value. 0.1 means
                     user enters 14.4 → register holds 144.
      help_text      one-liner under the input. Keep it short.
    """
    key: str
    label: str
    kind: str
    register: int
    read_from: str | None = None
    units: str = ""
    choices: tuple[tuple[int, str], ...] = ()
    min: float | None = None
    max: float | None = None
    step: float = 1.0
    scale: float = 1.0
    help_text: str = ""


class DeviceDriver(abc.ABC):
    """Abstract base for one device type within a vendor."""

    #: e.g. "renogy"
    vendor_id: str

    #: e.g. "charge_controller", "smart_battery", "shunt"
    device_kind: str

    def __init__(self, slave_id: int, label: str | None = None) -> None:
        self.slave_id = slave_id
        self.label = label or f"{self.vendor_id}.{self.device_kind}.{slave_id}"

    @property
    @abc.abstractmethod
    def sections(self) -> Sequence[Section]:
        """The Modbus reads this driver issues to fully sample a device."""

    def writable_settings(self) -> Sequence["WritableSetting"]:
        """Device-side settings the user can view (and phase-2 change).

        Default empty, drivers that don't have a write story or
        haven't been audited for safe ranges shouldn't expose any.
        Renogy + JK BMS implement this; Victron stays read-only
        forever (separate product-scope decision)."""
        return ()

    async def poll(self, transport: Transport) -> dict:
        """Default poll: run each Section sequentially, merge parsed dicts.

        Drivers that need non-Modbus or non-sequential behavior (e.g. Victron's
        broadcast Instant Readout) override this entirely.
        """
        result: dict = {
            "_vendor": self.vendor_id,
            "_kind": self.device_kind,
            "_label": self.label,
            "_slave_id": self.slave_id,
        }
        for section in self.sections:
            fc = section.function_code
            if fc == 4:
                frame = build_read_input(self.slave_id, section.register, section.word_count)
            else:
                # Default FC03; any unrecognised value falls back to
                # holding-register reads (matches every existing
                # vendor before the function_code field landed).
                frame = build_read_holding(self.slave_id, section.register, section.word_count)
            expected = expected_read_response_len(section.word_count)
            try:
                resp = await transport.request(frame, expected, timeout=5.0)
                verify_response(resp, self.slave_id, expected_fc=fc)
                result.update(section.parser(resp))
            except (TransportError, ValueError) as e:
                result.setdefault("_errors", []).append(
                    f"section {section.name or section.register}: {e}"
                )
        return result
