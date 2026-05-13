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


class AlertRuleCfg(msgspec.Struct, kw_only=True):
    """Mirror of alerts.AlertRule kept in the config schema so YAML
    loading validates the shape upfront."""
    id: str
    name: str
    metric: str
    op: str
    threshold: float
    severity: str = "warn"
    cooldown_seconds: int = 1800
    transports: list[str] = []


class ForecastCfg(msgspec.Struct, kw_only=True):
    """Third-party PV forecast integration. Each user supplies their
    own credentials — we don't proxy. Currently only `solcast` is
    implemented; structured this way so adding tomorrow.io / forecast.solar
    is a new provider class, not a config-schema change."""
    provider: str = "solcast"
    api_key: str
    resource_id: str
    # Cadence of the poll loop. Solcast hobbyist tier is 10 calls/day,
    # so 3 hours (8/day) leaves a comfortable buffer.
    poll_hours: int = 3


class QuietHoursCfg(msgspec.Struct, kw_only=True):
    """Window during which `warn`-severity alerts are buffered instead of
    dispatched immediately. `alarm` always pages through. Hours are
    integers 0-23 in the daemon's local timezone. Overnight windows
    work — start > end means "from start_hour today to end_hour tomorrow".

    Disabled when start_hour == end_hour or this whole struct is absent.
    """
    start_hour: int
    end_hour: int


class Config(msgspec.Struct, kw_only=True):
    transports: list[dict[str, Any]]
    devices: list[DeviceCfg]
    exporters: list[dict[str, Any]] = []  # optional
    notification_transports: list[dict[str, Any]] = []  # optional
    alerts: list[AlertRuleCfg] = []  # optional
    quiet_hours: QuietHoursCfg | None = None  # optional
    forecast: ForecastCfg | None = None  # optional


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return msgspec.convert(raw, Config)
