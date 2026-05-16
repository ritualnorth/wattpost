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
    own credentials — we don't proxy. Each provider uses the subset
    of fields it cares about:
      * `solcast`   — api_key + resource_id
      * `openmeteo` — lat + lon + array_kw + tilt_deg + azimuth_deg
                      (free, no key; physical PV estimate from
                      irradiance + array geometry)
      * `synthetic` — none (demo container only)
    """
    provider: str = "solcast"
    # Solcast credentials. Empty when using openmeteo or synthetic.
    api_key: str = ""
    resource_id: str = ""
    # Open-Meteo PV estimator inputs. Lat/lon default to None so the
    # provider can fall back to the WeatherCfg location if both blocks
    # are configured at the same site.
    lat: float | None = None
    lon: float | None = None
    # Array nameplate capacity in kilowatts. Required for openmeteo;
    # ignored by solcast (Solcast learns capacity from the user's
    # site definition in their web app).
    array_kw: float = 1.0
    # Panel tilt above horizontal (0 = flat, 90 = vertical). Used by
    # the openmeteo estimator's POA transposition. Default 30° is a
    # reasonable mid-latitude compromise.
    tilt_deg: float = 30.0
    # Panel azimuth: 0 = south (northern hemisphere optimal), 90 = west,
    # 180 = north, 270 = east. We don't currently switch convention by
    # hemisphere — southern-hemisphere users should configure 180.
    azimuth_deg: float = 0.0
    # System efficiency multiplier — covers inverter losses, wiring,
    # soiling, temperature derating. 0.80 is the commonly-cited
    # all-in figure for a healthy system.
    system_efficiency: float = 0.80
    # Cadence of the poll loop. Solcast hobbyist tier is 10 calls/day,
    # so 3 hours (8/day) leaves a comfortable buffer. Open-Meteo has
    # no quota and can poll more often if desired.
    poll_hours: int = 3


class CloudCfg(msgspec.Struct, kw_only=True):
    """Opt-in cloud integration. When present, the daemon periodically
    POSTs a heartbeat (SoC, net power, alert summary) to wattpost.io.
    Nothing else changes — the appliance stays fully functional with
    no cloud, no internet, no account.

    Pairing flow lives in the API: user gets a code from the cloud
    dashboard, pastes it into Settings → Cloud on the appliance, the
    daemon exchanges the code for a `bearer_token` which gets written
    back into this struct.
    """
    # Canonical product subdomain. wattpost.io is the marketing site;
    # the API + dashboard live on app.wattpost.io. Old pairings with
    # endpoint=https://wattpost.io keep working — Caddy reverse-proxies
    # /api/* on both hostnames.
    endpoint:          str  = "https://app.wattpost.io"
    bearer_token:      str  = ""    # empty until paired
    appliance_id:      int | None = None
    label:             str  = ""
    heartbeat_minutes: int  = 5
    # Cloudflare Tunnel credentials, returned by the cloud's
    # /api/pair/exchange when the cloud has CF API access. Empty
    # when the cloud hasn't yet had CF credentials configured
    # (development, or pre-launch production) — appliance still
    # works locally + pushes heartbeats, just doesn't expose itself
    # over the tunnel.
    tunnel_token:      str  = ""
    tunnel_hostname:   str  = ""


class WeatherCfg(msgspec.Struct, kw_only=True):
    """Current weather conditions integration. Open-Meteo only for now —
    no API key required (free public service, generous rate limits).
    Lat/lon are user-supplied; could derive from Solcast in a future
    iteration but explicit is cleaner."""
    provider: str = "openmeteo"
    lat: float
    lon: float
    # Cadence in minutes — Open-Meteo doesn't rate-limit hobbyist
    # traffic but there's no point hammering when conditions change
    # on a 10-minute timescale anyway.
    poll_minutes: int = 15


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
    weather: WeatherCfg | None = None    # optional
    cloud: CloudCfg | None = None        # optional


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return msgspec.convert(raw, Config)
