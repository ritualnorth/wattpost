# Changelog

All notable changes to solar-monitor. Format: [Keep a Changelog].
Versions follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

## [0.0.1] — 2026-05-12
Initial private commit. End-to-end working appliance against a live
Renogy rig.

### Added
- Pluggable Transport abstraction (BLE Modbus, RS-485 Modbus stub)
- Pluggable Vendor / DeviceDriver abstraction with central registry
- Renogy vendor: Rover charge controller + LFP smart battery drivers,
  parsers ported in (no upstream dependency on `cyrils/renogy-bt`)
- Modbus RTU framing helpers (CRC16, frame builders, response verify)
- YAML config + `solar-monitor poll` CLI
- Long-lived `Poller` that holds transports open across the daemon's
  lifetime (5× faster than reopen-per-poll)
- SQLite storage (WAL mode) with raw `samples` + 1-min / 1-hour /
  1-day rollup tables and a background retention/rollup task
- `PollScheduler` background asyncio task with exponential backoff
  on failure, exporter dispatch, and clean shutdown
- Litestar HTTP API:
  - `/api/health`
  - `/api/devices` + `/api/devices/{label}/latest`
  - `/api/devices/{label}/history` (range-aware table selection)
  - `/api/poll_run`
  - `/api/today` (energy-balance aggregates: PV today, bank
    charged/discharged today, **real load today**)
- Static SPA served from same Litestar app (no toolchain):
  - SoC donut with state-banded color, animated charge/discharge pulse
    (CW = charging, CCW = discharging), drop-shadow glow following
    direction, signed-W indicator pill
  - Hero with net power, time-to-empty/full, voltage, capacity, bank
    info
  - **Power flow strip** — data-driven from device kinds. Sources →
    Battery → Loads, animated arrows, energy-balance "Load" tile that
    captures bus-wired consumption invisible to the charge controller
  - Today strip (PV / charged Ah / peak / **real load** / lifetime)
  - Cell-balance panel with per-cell chips, min/max highlight, panel
    hue follows drift severity
  - History chart (uPlot, vendored offline) routing to the right
    rollup table by range
  - Device detail cards with kind icons + firmware + serial
  - Section header icons, status pill icon (✓ / ⚠ / ✗)
  - Conditional alert banner — hidden when healthy; surfaces low SoC,
    cell drift, over-temperature, comms loss, transport errors
- MQTT exporter (aiomqtt-based) — full device snapshots + per-metric
  topics, retained, with LWT `_status` topic for online/offline
- Tailscale-friendly: serves on 0.0.0.0, no TLS required for LAN, no
  cloud touched
- Firmware + serial decoded from registers and surfaced in device
  cards + MQTT + API
- Per-panel color hues (hero follows SoC band, cell-balance follows
  drift, power-flow has source→storage tint gradient)

### Notes
- Bank current rounds at 0.01 A per pack — small trickle currents
  (< ~0.5 A on a single 100 Ah pack) show as zero. Not a bug, a BMS
  resolution limit.
- Renogy load output (`load_power_w`) is intentionally not used as
  the primary load number; bus-wired loads (the common case) need the
  energy-balance approach.
- The 32-ish watts of "Other loads" you see when nothing's running is
  real phantom draw (inverter standby, BMS overhead × 3 packs, Hub +
  MPPT self-consumption). Most apps hide this. We don't.
