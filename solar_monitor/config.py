"""Declarative config: YAML in, validated structs out.

Users edit one YAML file. The orchestrator reads it, opens each transport
once, and dispatches devices to the right driver.

Example:

    transports:
      - id: hub_bt
        type: ble_modbus
        address: CC:45:A5:83:B7:42

    devices:
      - vendor: renogy
        kind: charge_controller
        transport: hub_bt
        slave_id: 16
        label: rover_mppt

      - vendor: renogy
        kind: smart_battery
        transport: hub_bt
        slave_id: 48
        label: battery_0
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import yaml


class DeviceCfg(msgspec.Struct, kw_only=True):
    vendor: str
    kind: str
    transport: str
    slave_id: int
    label: str | None = None


class Config(msgspec.Struct, kw_only=True):
    transports: list[dict[str, Any]]
    devices: list[DeviceCfg]
    exporters: list[dict[str, Any]] = []  # optional


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return msgspec.convert(raw, Config)
