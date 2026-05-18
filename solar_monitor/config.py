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
    # slave_id is the Modbus unit ID for ble_modbus / serial_modbus
    # devices. Victron Instant Readout devices (ble_victron_advertise
    # transport) don't have a Modbus address — they're identified by
    # MAC at the transport level. Optional so a Victron device config
    # block can omit it. Drivers that genuinely need a slave_id
    # validate it on poll().
    slave_id: int | None = None
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
    # the API + dashboard live on wattpost.cloud. Old pairings with
    # endpoint=https://wattpost.io keep working — Caddy reverse-proxies
    # /api/* on both hostnames.
    endpoint:          str  = "https://wattpost.cloud"
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
    # Per-appliance HMAC key for cloud→appliance SSO (#137). Cloud
    # pushes this via the pair response + every heartbeat response;
    # the daemon persists it here and verifies inbound /sso?token=…
    # requests against it. Empty until the appliance has heartbeat-ed
    # at least once post-v0.0.38; while empty, tunnel access falls
    # back to the local-password login page.
    sso_secret:        str  = ""
    # Public-share kiosk token. Generated lazily by the daemon on
    # first /kiosk request (or by load_config below). The cloud
    # dashboard builds the share URL `<slug>.wattpost.cloud/kiosk?key=<token>`
    # so the recipient can open the chrome-free wall-display view
    # without a session. Rotatable via POST /api/system/kiosk/rotate
    # to revoke an over-shared URL.
    kiosk_token:       str  = ""


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


class BankCfg(msgspec.Struct, kw_only=True):
    """Bank-level aggregation tuning (#121). Controls how the dashboard
    reconciles a system that has both a shunt and one or more smart
    BMSes reporting overlapping metrics (V, A, SoC, remaining Ah).

    Default behaviour (`source: auto`):
      * Cell-level data (per-cell V, drift, balance state) always
        comes from BMSes — shunts don't have it.
      * System-level data (V, A, SoC, time-to-go) prefers the shunt
        when present (direct measurement at the busbar, Coulomb-
        counted), falls back to summing BMSes when no shunt.

    Overrides for users whose hardware lies / drifts:
      * `source: shunt` — force system-level from shunt even when
        BMSes are present. Doesn't affect cell data.
      * `source: bms`   — force system-level from BMS pack sum
        even when a shunt is present.

    `disagreement_threshold_pct` controls when the dashboard surfaces
    a quiet "shunt vs BMS disagree" warning — 5% by default. Set to
    100 to suppress the warning entirely.
    """
    source: str = "auto"                      # auto | shunt | bms
    disagreement_threshold_pct: float = 5.0


class GpsCfg(msgspec.Struct, kw_only=True):
    """USB GPS receiver (#125). Off by default — opt in by adding a
    `gps:` block to config.yaml. The daemon reads NMEA at `baudrate`
    from `port` (typically `/dev/ttyACM0` for a USB-CDC receiver like
    the VK-162 G-Mouse) and, on significant movement (>5 km from
    the last applied fix, or >30 min idle), updates the weather +
    forecast services' lat/lon so a moving van/cabin gets correct
    forecasts."""
    port: str                                # e.g. /dev/ttyACM0
    baudrate: int = 9600
    # Tunables for the move-detection threshold. Defaults match #125;
    # power users in a tightly-mapped area can tighten these.
    min_move_km: float = 5.0
    refresh_after_s: int = 1800


class QuietHoursCfg(msgspec.Struct, kw_only=True):
    """Window during which `warn`-severity alerts are buffered instead of
    dispatched immediately. `alarm` always pages through. Hours are
    integers 0-23 in the daemon's local timezone. Overnight windows
    work — start > end means "from start_hour today to end_hour tomorrow".

    Disabled when start_hour == end_hour or this whole struct is absent.
    """
    start_hour: int
    end_hour: int


class BackupCfg(msgspec.Struct, kw_only=True):
    """Local rotating-snapshot policy. Disabled by default for
    backward-compat with installs that pre-date this feature; flip
    `enabled: true` in config.yaml to opt in. Cloud upload (Pro/
    Installer tier) is a separate field and only fires when
    `cloud_upload: true` AND the cloud pairing reports a paying
    tier.
    """
    enabled: bool = True
    # Interval in hours between auto-snapshots. Default = weekly.
    interval_hours: int = 168
    # How many auto-snapshots to keep on disk. Older ones pruned
    # after each successful capture. Manual snapshots live under
    # the same dir so they count too.
    keep_count: int = 4
    # Where the .tar.gz files land. Empty string = "<db_dir>/backups".
    dir: str = ""
    # Push each new local snapshot to wattpost.cloud after capture.
    # Only effective when the appliance is paired AND on a paying
    # tier — the cloud rejects uploads from Hobby with an explicit
    # "upgrade for cloud backups" 402.
    cloud_upload: bool = False
    # How many cloud-side backups to retain per appliance. The cloud
    # enforces this independently; this value is purely a hint sent
    # along with each upload so the cloud knows the operator's wish.
    cloud_keep_count: int = 4


class DiscoveryCfg(msgspec.Struct, kw_only=True):
    """Anonymous hardware-discovery telemetry (#129).

    OFF by default. When opted in, the appliance forwards anonymised
    fingerprints of devices its scans see but our drivers don't
    recognise — feeding the next-driver pipeline. No customer-
    identifying information leaves the appliance:

      * MAC truncated to vendor prefix (first 3 octets / OUI)
      * advertised local name (typically just a model + serial — we
        strip the serial suffix server-side)
      * manufacturer-data ID + first 4 bytes (model identifier)
      * service UUIDs
      * appliance bearer-token is the only auth — cloud derives no
        owner/email from it for the discovery write path
    """
    enabled: bool = False


class Config(msgspec.Struct, kw_only=True):
    # SQLite storage path. Read by cli._resolve_db_path. v0.0.60 added
    # the read logic but I FORGOT to add the field here, so msgspec
    # silently dropped the YAML value — every Docker user's history
    # was still landing in /app/solar-monitor.db (inside the
    # ephemeral writable layer). v0.0.63 actually wires it up.
    # Default matches the historical CLI default so absence of the
    # key in config.yaml keeps existing installs working unchanged.
    db_path: str = "solar-monitor.db"
    transports: list[dict[str, Any]]
    devices: list[DeviceCfg]
    exporters: list[dict[str, Any]] = []  # optional
    notification_transports: list[dict[str, Any]] = []  # optional
    alerts: list[AlertRuleCfg] = []  # optional
    quiet_hours: QuietHoursCfg | None = None  # optional
    forecast: ForecastCfg | None = None  # optional
    weather: WeatherCfg | None = None    # optional
    cloud: CloudCfg | None = None        # optional
    gps: GpsCfg | None = None            # optional (USB GPS — #125)
    bank: BankCfg | None = None          # optional (#121 — shunt-vs-BMS reconciliation)
    backup: BackupCfg | None = None      # optional — local rotating snapshots (#146 phase 2)
    discovery: DiscoveryCfg | None = None  # optional — anonymous discovery telemetry (#129)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    cfg = msgspec.convert(raw, Config)

    # Legacy endpoint auto-upgrade. Appliances paired before the
    # rebrand have `cloud.endpoint: https://app.wattpost.io` saved.
    # That hostname now 301s to wattpost.cloud at the edge — and
    # httpx (correctly) strips the Authorization header on cross-host
    # redirects, so every heartbeat 401s before being rewritten and
    # the appliance shows offline forever. Detect + upgrade in-place
    # so paired appliances heal themselves on next start.
    legacy_endpoints = (
        "https://app.wattpost.io",
        "https://app.wattpost.io/",
        "https://wattpost.io",
        "https://wattpost.io/",
    )
    # Auto-generate kiosk_token if missing. Pure convenience — the
    # token is the bearer for the public share URL, so anyone with
    # the URL has access regardless of whether the token was
    # auto-generated or user-set. Re-running this on every start is
    # idempotent (only fires when empty).
    if cfg.cloud is not None and not cfg.cloud.kiosk_token:
        import secrets as _secrets
        cfg.cloud.kiosk_token = _secrets.token_urlsafe(24)
        try:
            raw.setdefault("cloud", {})["kiosk_token"] = cfg.cloud.kiosk_token
            Path(path).write_text(yaml.safe_dump(raw, sort_keys=False))
        except Exception:
            pass  # in-memory generation is fine for this run

    if cfg.cloud is not None and cfg.cloud.endpoint in legacy_endpoints:
        cfg.cloud.endpoint = "https://wattpost.cloud"
        try:
            raw["cloud"]["endpoint"] = "https://wattpost.cloud"
            Path(path).write_text(yaml.safe_dump(raw, sort_keys=False))
        except Exception:
            # Persist-back is best-effort. The in-memory upgrade above
            # is enough to make this run work; the next save (pair,
            # settings edit) will then write the upgraded value
            # naturally. A read-only mount or permissions issue here
            # shouldn't break the daemon.
            pass

    return cfg
