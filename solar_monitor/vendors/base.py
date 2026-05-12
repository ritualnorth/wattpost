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
    expected_read_response_len,
    verify_response,
)
from ..transport import Transport, TransportError


class Section(msgspec.Struct, frozen=True):
    """One Modbus read + parser."""

    register: int
    word_count: int
    parser: Callable[[bytes], dict]
    name: str = ""  # for log/debug; optional


class VendorInfo(msgspec.Struct, frozen=True):
    id: str           # short stable id, e.g. "renogy"
    display_name: str
    description: str = ""


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
            frame = build_read_holding(self.slave_id, section.register, section.word_count)
            expected = expected_read_response_len(section.word_count)
            try:
                resp = await transport.request(frame, expected, timeout=5.0)
                verify_response(resp, self.slave_id)
                result.update(section.parser(resp))
            except (TransportError, ValueError) as e:
                result.setdefault("_errors", []).append(
                    f"section {section.name or section.register}: {e}"
                )
        return result
