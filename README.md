# WattPost

A Pi-hole-style appliance for off-grid solar / battery telemetry.
Polls **Renogy**, **Victron**, and **JK BMS** gear over Bluetooth
or wired serial, stores history locally in SQLite, and serves a
web dashboard with live state, history charts, cell-level battery
visibility, energy-balance load detection, and configurable
alerts.

**Local-first by design.** No mandatory cloud, no vendor lock-in,
no "requires internet to see your battery." Optional cloud
companion at [app.wattpost.io](https://app.wattpost.io) adds
remote access, push notifications, and multi-site fleet view for
installers.

Product site: [wattpost.io](https://wattpost.io) · Live demo:
[demo.wattpost.io](https://demo.wattpost.io) · Cloud + docs:
[wattpost.cloud](https://wattpost.cloud)

## What it covers today

| Vendor | Transports | Status |
| --- | --- | --- |
| **Renogy** | BT-1 / BT-2 BLE, USB-RS485, GPIO serial | ✅ Charge controllers, DC-DC + MPPT combos, smart lithium batteries, smart shunts, 1-3 kW inverter-chargers |
| **Victron** | BLE Instant Readout, VE.Direct (wired) | ✅ Read-only. SmartShunt, BMV-7xx, SmartSolar MPPT, Orion-Tr Smart, Orion XS, Smart BatteryProtect, Blue Smart AC Charger, Smart Lithium, Lynx Smart BMS, Phoenix Inverter VE.Direct |
| **JK BMS** | Native BLE GATT | ✅ B-series (BD6A20S, B1A24S, B2A24S, etc.) |

Coverage roadmap (JBD, Daly, EPEVER, AiLi, Junctek, MPP Solar,
Sterling, REDARC) lives in
[docs/coverage-roadmap.md](docs/coverage-roadmap.md).

## Components

| Layer | State |
| --- | --- |
| Transport / vendor / driver abstractions | ✅ |
| Renogy BLE (BT-1 / BT-2) + USB-RS485 + GPIO serial | ✅ |
| Victron BLE Instant Readout | ✅ |
| Victron VE.Direct (wired) | ✅ v0.1.23 |
| JK BMS BLE | ✅ |
| Litestar API + SQLite (WAL + tiered rollups) | ✅ |
| Scheduler with auto-reconnect | ✅ |
| Web dashboard (vanilla HTML + uPlot, no toolchain) | ✅ |
| MQTT export with Home Assistant auto-discovery | ✅ |
| Energy-balance load inference | ✅ |
| Conditional alert engine + push / SMTP / MQTT / webhook | ✅ |
| Smart-plug output adapter (Shelly Gen2, Tasmota) | ✅ v0.1.22 |
| Solar-aware AC charger pause rule | ✅ v0.1.21 |
| pi-gen SD image build + Update-now flow | ✅ |
| Cloud broker, multi-site fleet, billing, push | ✅ |

## Quick start (development)

```bash
git clone git@github.com:ritualnorth/offgrid-monitor.git
cd offgrid-monitor
python3 -m venv .venv
.venv/bin/pip install -e .

cp config.example.yaml config.yaml
# edit config.yaml with your transports + devices

# one-shot poll (prints JSON):
.venv/bin/solar-monitor poll --config config.yaml

# run the daemon (web UI on :8000):
.venv/bin/solar-monitor serve --config config.yaml
```

## Install on a Pi

The recommended path is the SD-card image:
[wattpost.io/download](https://wattpost.io/download). Flash with
Raspberry Pi Imager, boot, follow the setup wizard.

Docker is the other supported path:
[docs/docker-install.md](docs/docker-install.md). One compose
file, BLE passthrough optional via `--device=/dev/ttyUSB0` or a
host-network setting for BLE.

## Dashboard

- **Bank hero**, SoC donut (green / amber / red), animated
  direction pulse, net W, time-to-empty / full, voltage,
  capacity, pack info
- **Power flow strip**, sources → battery → loads, animated
  arrows, with the load tile computed from energy balance (sees
  loads on the busbar, not just the controller)
- **Today**, PV in, charged Ah, peak W, load consumed
  (computed from balance, not what the MPPT thinks), lifetime Wh
- **Right now**, optional current-weather tile (Open-Meteo)
- **Tomorrow**, optional PV forecast tile (Solcast or
  Open-Meteo). Expected kWh, peak time, day-after preview,
  sparkline
- **7-day outlook**, per-day kWh + sparkline across the
  forecast window
- **Cell balance**, per-cell voltages across every pack,
  min / max highlighted, panel hue follows drift severity
- **Charge efficiency**, SoC-corrected coulombic η per pack,
  surfaced on smart_battery cards
- **History chart**, uPlot, any metric of any device,
  1h / 6h / 24h / 7d / 30d, with tiered rollups under the hood.
  Compare-packs overlay across multi-pack rigs; PV-forecast
  overlay when viewing `pv_power_w`
- **Settings**, UI-driven device + transport management,
  alerts, integrations (MQTT, Solcast, Open-Meteo), retention
  tiers, kiosk mode, backups, solar-pause rule, smart-scene
  rules
- **Conditional alert banner**, hidden when healthy, shows on
  cell drift, low SoC, over-temp, comms loss

## Architecture

```
solar_monitor/
├── transport/              # how bytes get to a device
│   ├── base.py             # Transport ABC
│   ├── ble_modbus.py       # BLE GATT Modbus (Renogy BT-1/BT-2)
│   ├── ble_victron_advertise.py # passive BLE Instant Readout
│   ├── ble_jkbms.py        # JK BMS native BLE
│   ├── serial_modbus.py    # RS-485 over USB (Renogy wired)
│   ├── ve_direct.py        # Victron VE.Direct text frames
│   └── registry.py
├── vendors/                # protocol knowledge per device family
│   ├── base.py             # DeviceDriver ABC, Section
│   ├── registry.py
│   ├── renogy/             # Rover, DCC, smart batteries, shunt, inverter
│   ├── victron/            # SmartShunt, MPPT, Orion, AC charger, …
│   │   └── ve_direct.py    # wired Victron drivers (#197)
│   └── jkbms/              # JK B-series
├── outputs/                # controllable outputs (writes)
│   ├── base.py             # OutputAdapter ABC
│   ├── renogy_rover.py     # Rover load relay (FC06)
│   ├── smart_plug.py       # Shelly Gen2 + Tasmota over local HTTP
│   ├── solar_pause.py      # solar-aware charger pause rule
│   ├── schedules.py        # cron-style output schedules
│   └── service.py
├── export/                 # pluggable downstream sinks
│   ├── base.py
│   └── mqtt.py             # JSON snapshots → MQTT broker (HA discovery)
├── alerts/                 # local rule engine + notifications
│   ├── engine.py
│   └── transports/         # ntfy / Discord / webhook / SMTP / MQTT / push
├── forecast/               # PV forecast integrations
│   ├── solcast.py
│   ├── openmeteo.py
│   └── service.py
├── weather/                # current-conditions
│   ├── openmeteo.py
│   └── service.py
├── storage/
│   └── sqlite.py           # WAL, raw + 1min/1hr rollups, kv blob cache
├── api/                    # Litestar HTTP + static SPA
├── web/                    # vanilla HTML/CSS/JS, no build step
├── backup/                 # local + cloud snapshots
├── cloud/                  # appliance ↔ wattpost.cloud heartbeat
├── modbus.py               # Modbus RTU framing + CRC
├── config.py               # YAML schema (msgspec)
├── scheduler.py            # background poll loop + maintenance
└── cli.py                  # solar-monitor poll | serve
```

See [docs/architecture.md](docs/architecture.md) for the why
behind each layer.

## Adding a vendor

Drop a folder under `solar_monitor/vendors/`, write one driver
class per device kind, register the vendor, add one import line
to `vendors/__init__.py`. Zero core changes. The priority queue
of what to add next lives in
[docs/coverage-roadmap.md](docs/coverage-roadmap.md).

Full recipe: [docs/adding-a-vendor.md](docs/adding-a-vendor.md).

## Releases + cloud

- [docs/release-pipeline.md](docs/release-pipeline.md), how
  features reach a customer's Pi: SD image build, source
  tarballs, in-place Update-now flow, the self-hosted
  `releases.wattpost.io` infrastructure
- [docs/cloud-architecture.md](docs/cloud-architecture.md),
  cloud broker + multi-site fleet pattern
- [CHANGELOG.md](CHANGELOG.md), versioned release notes

## Licence

Proprietary. All rights reserved. See [LICENSE.txt](LICENSE.txt).
The full source ships under `/opt/wattpost-src` on every
installed appliance, source-available, not open source.
