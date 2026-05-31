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
    # transport) don't have a Modbus address, they're identified by
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
    own credentials, we don't proxy. Each provider uses the subset
    of fields it cares about:
      * `solcast`  , api_key + resource_id
      * `openmeteo`, lat + lon + array_kw + tilt_deg + azimuth_deg
                      (free, no key; physical PV estimate from
                      irradiance + array geometry)
      * `synthetic`, none (demo container only)
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
    # hemisphere, southern-hemisphere users should configure 180.
    azimuth_deg: float = 0.0
    # System efficiency multiplier, covers inverter losses, wiring,
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
    Nothing else changes, the appliance stays fully functional with
    no cloud, no internet, no account.

    Pairing flow lives in the API: user gets a code from the cloud
    dashboard, pastes it into Settings → Cloud on the appliance, the
    daemon exchanges the code for a `bearer_token` which gets written
    back into this struct.
    """
    # Canonical product subdomain. wattpost.io is the marketing site;
    # the API + dashboard live on wattpost.cloud. Old pairings with
    # endpoint=https://wattpost.io keep working, Caddy reverse-proxies
    # /api/* on both hostnames.
    endpoint:          str  = "https://wattpost.cloud"
    bearer_token:      str  = ""    # empty until paired
    appliance_id:      int | None = None
    label:             str  = ""
    heartbeat_minutes: int  = 5
    # Cloudflare Tunnel credentials, returned by the cloud's
    # /api/pair/exchange when the cloud has CF API access. Empty
    # when the cloud hasn't yet had CF credentials configured
    # (development, or pre-launch production), appliance still
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
    # Deprecated legacy kiosk token. The appliance no longer mints,
    # serves, or honours a `?key=` kiosk URL — cloud kiosk now flows
    # through the cloud's kiosk-shares registry (broker `scope=kiosk`),
    # and LAN kiosk uses the READONLY_PUBLIC path. Field kept so older
    # configs still load; it is unused.
    kiosk_token:       str  = ""


class WeatherCfg(msgspec.Struct, kw_only=True):
    """Current weather conditions integration. Open-Meteo only for now,
    no API key required (free public service, generous rate limits).
    Lat/lon are user-supplied; could derive from Solcast in a future
    iteration but explicit is cleaner."""
    provider: str = "openmeteo"
    lat: float
    lon: float
    # Cadence in minutes, Open-Meteo doesn't rate-limit hobbyist
    # traffic but there's no point hammering when conditions change
    # on a 10-minute timescale anyway.
    poll_minutes: int = 15


class BankCfg(msgspec.Struct, kw_only=True):
    """Bank-level aggregation tuning (#121). Controls how the dashboard
    reconciles a system that has both a shunt and one or more smart
    BMSes reporting overlapping metrics (V, A, SoC, remaining Ah).

    Default behaviour (`source: auto`):
      * Cell-level data (per-cell V, drift, balance state) always
        comes from BMSes, shunts don't have it.
      * System-level data (V, A, SoC, time-to-go) prefers the shunt
        when present (direct measurement at the busbar, Coulomb-
        counted), falls back to summing BMSes when no shunt.

    Overrides for users whose hardware lies / drifts:
      * `source: shunt`, force system-level from shunt even when
        BMSes are present. Doesn't affect cell data.
      * `source: bms`  , force system-level from BMS pack sum
        even when a shunt is present.

    `disagreement_threshold_pct` controls when the dashboard surfaces
    a quiet "shunt vs BMS disagree" warning, 5% by default. Set to
    100 to suppress the warning entirely.
    """
    source: str = "auto"                      # auto | shunt | bms
    disagreement_threshold_pct: float = 5.0


class LocationCfg(msgspec.Struct, kw_only=True):
    """Privacy gate for shipping location to the cloud (#263/#264).

    Location plumbing (GpsCfg or ForecastCfg.lat/lon) exists for local
    use regardless, forecasts, the appliance's own map tile, weather.
    Cloud transmission is a SEPARATE decision the customer makes here,
    OPT-IN, default OFF. Three modes:

      off    , cloud receives no location data at all (default)
      approx , appliance rounds to ~10km grid before transmission
      precise, real lat/lon

    Why three modes:  precise enables the cool features (fleet map,
    geofences, anchor watch).  approx supports "show roughly where my
    fleet is" without precise tracking, useful for vanlife customers
    who want fleet visibility but not constant pinpoint surveillance.
    off is the default so a fresh install never leaks location until
    the customer explicitly opts in.

    Customer-side is authoritative: even if the cloud's UI shows a
    fleet map, this toggle controls whether THIS appliance contributes.
    Bob Smith (the OEM builder pattern) cannot override it remotely,
    see project-oem-builder-gtm memory.
    """
    share_with_cloud: str = "off"   # "off" | "approx" | "precise"
    # Snap-to-grid size for approx mode, in km. ~10km is a good
    # privacy/utility tradeoff: shows "in the Lake District" not
    # "in their driveway." Customers in dense areas can crank it up.
    approx_grid_km: float = 10.0


class GpsCfg(msgspec.Struct, kw_only=True):
    """USB GPS receiver (#125). Off by default, opt in by adding a
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
    work, start > end means "from start_hour today to end_hour tomorrow".

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
    # Interval in hours between auto-snapshots. Default = daily.
    # Was weekly (168h) pre-launch; bumped 2026-05-27, "off-site
    # backups + one-click restore" as a Pro promise needs fresher
    # snapshots than once a week. A customer whose Pi dies on day
    # 6 of the cycle would otherwise lose 6 days of data.
    interval_hours: int = 24
    # How many auto-snapshots to keep on disk. Older ones pruned
    # after each successful capture. Manual snapshots live under
    # the same dir so they count too.
    # At 24h interval × 7 = 1 week rolling window. Cloud storage
    # cost is trivial (~26 MB × 7 × N customers).
    keep_count: int = 7
    # Where the .tar.gz files land. Empty string = "<db_dir>/backups".
    dir: str = ""
    # Push each new local snapshot to wattpost.cloud after capture.
    # Only effective when the appliance is paired AND on a paying
    # tier, the cloud rejects uploads from Hobby with an explicit
    # "upgrade for cloud backups" 402.
    cloud_upload: bool = False
    # How many cloud-side backups to retain per appliance. The cloud
    # enforces this independently; this value is purely a hint sent
    # along with each upload so the cloud knows the operator's wish.
    cloud_keep_count: int = 4


class LocalTelemetryCfg(msgspec.Struct, kw_only=True):
    """Anonymous install-count beacon (#217).

    Piggybacks an `install_id` + version + install method onto the
    daily update-check poll so the cloud can count distinct local
    installs and surface release adoption across the fleet (paired
    + unpaired). OFF by default, see `docs/privacy-and-telemetry.md`
    for what's sent and what's not. When enabled, an install_id query
    param rides the daily update poll; when off, the update poll still
    fires (needed for the dashboard's `Update available` badge) but
    carries no install_id. Toggle from Settings -> Privacy.
    """
    enabled: bool = False


class DiscoveryCfg(msgspec.Struct, kw_only=True):
    """Anonymous hardware-discovery telemetry (#129).

    OFF by default. When opted in, the appliance forwards anonymised
    fingerprints of devices its scans see but our drivers don't
    recognise, feeding the next-driver pipeline. No customer-
    identifying information leaves the appliance:

      * MAC truncated to vendor prefix (first 3 octets / OUI)
      * advertised local name (typically just a model + serial, we
        strip the serial suffix server-side)
      * manufacturer-data ID + first 4 bytes (model identifier)
      * service UUIDs
      * appliance bearer-token is the only auth, cloud derives no
        owner/email from it for the discovery write path
    """
    enabled: bool = False


class SmartPlugCfg(msgspec.Struct, kw_only=True):
    """One smart plug entry. WattPost talks to it over local HTTP,
    no broker, no cloud, no Home Assistant required.

    `kind` selects the protocol: `shelly_gen2` for any Shelly Plus /
    Pro / Plug S running stock firmware; `tasmota` for any
    Tasmota-flashed plug. `host` is the device's LAN address or
    hostname. `name` shows up in the dashboard's Settings dropdown
    when the user picks which plug the solar-pause rule controls.
    """
    name: str
    kind: str   # "shelly_gen2" | "tasmota"
    host: str
    user:     str | None = None
    password: str | None = None


class SolarPauseCfg(msgspec.Struct, kw_only=True):
    """Solar-aware AC charger pause rule (#163). Off by default.

    When enabled, the daemon evaluates the bank + PV + charger state
    on every poll cycle and may toggle the named output through the
    same write path the dashboard uses. See outputs/solar_pause.py
    for the full decision tree."""
    enabled: bool = False
    charger_output_id: str | None = None
    target_soc:       float = 80.0
    recover_soc:      float = 50.0
    hard_floor_soc:   float = 30.0
    pv_surplus_w:     float = 50.0
    cooldown_minutes: int   = 30


class MqttInTopicCfg(msgspec.Struct, kw_only=True):
    """One manual topic→metric mapping. The escape hatch for devices
    that don't publish HA-discovery configs (Shelly gen1, bespoke
    ESPHome, custom microcontrollers, etc.). Power users edit the
    YAML directly; the wizard offers HA-discovery first."""
    topic: str                        # MQTT topic, may include + / # wildcards
    label: str                        # virtual device label (becomes its row id)
    metric: str = "value"             # snapshot key under that device
    vendor: str = "mqtt"              # surfaced via _vendor
    kind: str = "sensor"              # surfaced via _kind
    # How to interpret the payload bytes:
    #   "scalar", payload is the raw number / string (default)
    #   "json"  , payload is JSON; extract `json_path` (dotted, e.g.
    #              `value.temperature` for `{"value": {"temperature": 21.3}}`)
    value_type: str = "scalar"
    json_path: str = ""               # required when value_type == "json"


class MqttInCfg(msgspec.Struct, kw_only=True):
    """Ingest external MQTT broker → virtual devices on the dashboard.

    When configured, the daemon connects to the user's broker (e.g.
    their Home Assistant Mosquitto, an industrial broker, or a Shelly
    gateway), subscribes to either HA-discovery topics (auto-find
    every entity HA already knows about) or a manual `topics:` list,
    and folds the latest values into the same `/api/devices` response
    that BLE/Modbus devices land in.

    Privacy note: only OUTBOUND connection to the user's own broker.
    Nothing leaves the LAN unless the user explicitly points us at a
    remote broker. Same model as the cloud-tier opt-in gate.
    """
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "wattpost-in"
    # HA MQTT-discovery autopopulate. Subscribes to
    # `<ha_discovery_prefix>/+/+/config` and friends; turns each
    # `state_topic` into a virtual device on the dashboard. This is
    # the single highest-leverage toggle, most existing HA users
    # already have hundreds of entities ready to surface.
    ha_discovery: bool = True
    ha_discovery_prefix: str = "homeassistant"
    # Manual fallback: explicit topic mappings for devices outside HA.
    topics: list[MqttInTopicCfg] = []
    # How long a virtual device stays in the result with no fresh
    # advertisement before we drop it from `/api/devices`. Mirrors
    # the BLE STALE_AFTER_SECONDS pattern but longer, MQTT devices
    # can be quiet for minutes between state changes.
    stale_after_seconds: int = 600


class HotspotCfg(msgspec.Struct, kw_only=True):
    """Appliance-as-WiFi-AP (Pillar 3). Turns the Pi's WiFi radio into
    an access point so a phone/laptop can reach the dashboard with no
    existing network, the field-setup and off-grid story ("plug it in,
    join WattPost-Setup, open the page").

    Driven through NetworkManager (`nmcli`), the default network stack
    on Pi OS Bookworm. We own a single named connection profile
    (`connection_name`) in AP mode with NM's shared IPv4 (built-in
    DHCP + NAT, 10.42.0.1/24 by default), so there's no hostapd /
    dnsmasq to install or hand-configure.

    Strictly opt-in and non-fatal, same contract as the tunnel:
      - `enabled` only controls auto-bring-up on boot. Manual
        on/off via /api/hotspot/{on,off} works regardless, as long
        as the host has nmcli + the radio.
      - If nmcli is missing or the interface doesn't exist we log
        once and stay out of the way; the local UI and polling are
        never affected.

    Phase 3b is wired in via the `auto_handoff` and `captive_portal`
    fields below.
    """
    enabled: bool = False          # auto-start the AP on boot (always-on)
    # Auto-handoff (Pillar 3b): bring the AP up automatically whenever the
    # appliance has no other network, and drop it again when a real LAN
    # returns. LOCAL source of truth — works with no cloud subscription;
    # this is the off-grid/vanlife path. The cloud operating mode
    # (van/cabin/marine) is layered on top as a convenience that implies
    # auto-handoff without the user touching this flag. Ignored when
    # `enabled` is true (the AP is always on then, nothing to hand off).
    auto_handoff: bool = False
    # Captive portal: while the AP is up, hijack DNS (via a NetworkManager
    # dnsmasq drop-in) so a joining device's OS connectivity check lands
    # on the dashboard and the "Sign in to network" sheet pops
    # automatically — no need to type http://10.42.0.1 by hand. Needs the
    # daemon to be able to write NM's dnsmasq-shared.d dir (the packaged
    # Pi image grants this); degrades to a no-op otherwise, AP unaffected.
    captive_portal: bool = False
    ssid: str = "WattPost-Setup"
    # WPA2-PSK passphrase. Empty string => open network (no auth).
    # NetworkManager/WPA require 8..63 chars when set; validated at
    # activation time so a bad value surfaces as last_error, not a crash.
    password: str = ""
    # 2.4 GHz ("bg") by default for range + universal client support;
    # "a" selects 5 GHz on radios/regions that allow AP there.
    band: str = "bg"
    channel: int = 6
    interface: str = "wlan0"
    # NM connection profile we create/own. Kept stable so repeated
    # activations modify one profile instead of piling up duplicates.
    connection_name: str = "wattpost-hotspot"


class HistoryCfg(msgspec.Struct, kw_only=True):
    """Polling cadence + how long each retention tier keeps data.

    Every field is optional; absent values fall back to the historical
    module-level constants (60s poll, 7/30/365-day retention pyramid).
    Editable via Settings → History (#172). Values apply live: the
    scheduler reads `interval_seconds` each cycle and storage reads
    the retention windows on every maintenance pass.

    The four defaults are sized for a Pi 4 with a 16 GB SD card and
    a single bank. Bigger installs (Pi 5, more devices) can shorten
    raw retention; off-grid users who only check the dashboard
    monthly can extend hour-aggregate retention.
    """
    poll_interval_seconds: int | None = None  # default 60
    retention_raw_days: int | None = None     # default 7
    retention_min_days: int | None = None     # default 30
    retention_hour_days: int | None = None    # default 365


class Config(msgspec.Struct, kw_only=True):
    # SQLite storage path. Read by cli._resolve_db_path. v0.0.60 added
    # the read logic but I FORGOT to add the field here, so msgspec
    # silently dropped the YAML value, every Docker user's history
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
    # Set to True after first-boot seeding so we don't re-seed defaults
    # if the user intentionally cleared all rules. Flip to False (or
    # delete the key) to trigger a re-seed on next start.
    alerts_seeded: bool = False
    quiet_hours: QuietHoursCfg | None = None  # optional
    forecast: ForecastCfg | None = None  # optional
    weather: WeatherCfg | None = None    # optional
    cloud: CloudCfg | None = None        # optional
    gps: GpsCfg | None = None            # optional (USB GPS, #125)
    location: LocationCfg | None = None  # optional, cloud-share gate (#263/#264). Default-off when absent.
    bank: BankCfg | None = None          # optional (#121, shunt-vs-BMS reconciliation)
    backup: BackupCfg | None = None      # optional, local rotating snapshots (#146 phase 2)
    discovery: DiscoveryCfg | None = None  # optional, anonymous discovery telemetry (#129)
    local_telemetry: LocalTelemetryCfg | None = None  # optional, anonymous install beacon (#217); OFF by default
    history: HistoryCfg | None = None    # optional, poll cadence + retention (#172)
    solar_pause: SolarPauseCfg | None = None  # optional, auto-pause AC charger when PV covers (#163)
    smart_plugs: list[SmartPlugCfg] = []      # optional, LAN-attached smart plugs for solar-pause to drive
    mqtt_in: MqttInCfg | None = None     # optional, ingest from user's MQTT broker (#256)
    hotspot: HotspotCfg | None = None    # optional, appliance-as-WiFi-AP (Pillar 3, off by default)


# #258, default alert rules seeded on first boot. System-voltage-
# agnostic (SoC + temperature only; voltage rules would need to know
# 12V/24V/48V which we don't at first boot). Empty `transports` list
# means they fire to the local ring buffer + cloud inbox via heartbeat
# extras, the user gets visibility immediately, and can attach SMTP /
# MQTT / Cloud push from the Alerts settings later.
_DEFAULT_ALERT_RULES: list[dict[str, Any]] = [
    {
        "id": "low-soc", "name": "Low battery (30%)",
        "metric": "bank.soc_pct", "op": "lt", "threshold": 30.0,
        "severity": "warn", "cooldown_seconds": 1800, "transports": [],
    },
    {
        "id": "critical-soc", "name": "Critical battery (15%)",
        "metric": "bank.soc_pct", "op": "lt", "threshold": 15.0,
        "severity": "alarm", "cooldown_seconds": 900, "transports": [],
    },
    {
        "id": "high-temp", "name": "Bank temperature high (45°C)",
        "metric": "bank.temperature_c", "op": "gt", "threshold": 45.0,
        "severity": "warn", "cooldown_seconds": 1800, "transports": [],
    },
    {
        "id": "critical-temp", "name": "Bank temperature critical (55°C)",
        "metric": "bank.temperature_c", "op": "gt", "threshold": 55.0,
        "severity": "alarm", "cooldown_seconds": 900, "transports": [],
    },
]


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    cfg = msgspec.convert(raw, Config)

    # #258, first-boot default rules. Only seeds when:
    #   1. `alerts:` is empty (or absent), AND
    #   2. `alerts_seeded:` is missing or False.
    # Persists alerts_seeded=true on disk so a user who intentionally
    # clears all rules later doesn't get them silently re-added on the
    # next start. Best-effort write, if the config is read-only the
    # in-memory seed still works for this session.
    if not cfg.alerts and not cfg.alerts_seeded:
        cfg.alerts = msgspec.convert(_DEFAULT_ALERT_RULES, list[AlertRuleCfg])
        cfg.alerts_seeded = True
        try:
            raw["alerts"] = _DEFAULT_ALERT_RULES
            raw["alerts_seeded"] = True
            Path(path).write_text(yaml.safe_dump(raw, sort_keys=False))
        except Exception:
            pass

    # Legacy endpoint auto-upgrade. Appliances paired before the
    # rebrand have `cloud.endpoint: https://app.wattpost.io` saved.
    # That hostname now 301s to wattpost.cloud at the edge, and
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
    # Auto-generate kiosk_token if missing. Pure convenience, the
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
