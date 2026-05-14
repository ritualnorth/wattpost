# solar-monitor

A Pi-hole-style appliance for off-grid solar / battery telemetry. Polls
Renogy (and, soon, Victron and JK-BMS) over BLE or RS-485, stores history
locally in SQLite, and serves a web dashboard with live state, history
charts, cell-level battery visibility, and energy-balance load detection.

**Local-first by design.** No mandatory cloud, no vendor lock-in, no
"requires internet to see your battery." Remote access via Tailscale
when you want it.

Working name; product name TBD.

## Status

This is a private pre-release. Working end-to-end against a live Renogy
rig (Rover 40A MPPT + 3× RBT100LFP12S-G1 batteries over BT-2). Not yet
shipped to anyone.

| Layer | State |
|---|---|
| Foundation | ✅ Transport / vendor / driver abstractions |
| BLE transport (Renogy BT-1/BT-2) | ✅ Validated on live rig |
| RS-485 transport | ✅ Interface locked, body unvalidated |
| Renogy: charge controllers + LFP smart batteries | ✅ |
| Victron SmartShunt | ⏳ Awaits hardware |
| JK-BMS | ⏳ Awaits hardware |
| Litestar API + SQLite (WAL + tiered rollups) | ✅ |
| Scheduler with auto-reconnect | ✅ |
| Web dashboard (vanilla HTML + uPlot, no toolchain) | ✅ |
| MQTT export | ✅ |
| Energy-balance load inference + /api/today | ✅ |
| Conditional alert engine | ✅ |
| pi-gen SD image build | ⏳ |
| Ed25519 license gate + Stripe glue | ⏳ |

## Quick start (development)

```bash
git clone git@github.com:ritualnorth/solar-monitor.git
cd solar-monitor
python3 -m venv .venv
.venv/bin/pip install -e .

cp config.example.yaml config.yaml
# edit config.yaml with your BT module's MAC + device IDs

# one-shot poll (prints JSON):
.venv/bin/solar-monitor poll --config config.yaml

# run the daemon (web UI on :8000):
.venv/bin/solar-monitor serve --config config.yaml
```

## Dashboard

What the user sees on first load:

- **Bank hero** — SoC donut (green/amber/red bands), animated direction
  pulse (CW=charging, CCW=discharging), net W, time-to-empty/full, voltage,
  capacity, pack info
- **Power flow strip** — sources → battery → loads, animated arrows
  showing live W and direction, with the load tile computed from the
  energy balance (sees loads on the busbar, not just the controller)
- **Today** — PV in, charged Ah, peak W, **load consumed (real, computed
  from balance — not what the MPPT thinks)**, lifetime Wh
- **Right now** — optional current-weather tile (Open-Meteo). Temp,
  cloud cover, wind, sunrise / sunset. No API key needed
- **Tomorrow** — optional PV forecast tile (Solcast). Expected kWh,
  peak time, day-after preview, sparkline. User brings their own free
  hobbyist key; nothing's proxied
- **7-day outlook** — per-day kWh + mini sparkline across the
  forecast window; common Y scale so quiet days read as quiet
- **Cell balance** — per-cell voltages across every pack, min/max
  highlighted, panel hue follows drift severity
- **Charge efficiency** — SoC-corrected coulombic η per pack, surfaced
  on smart_battery cards. Catches degradation before cycles get away
- **History chart** — uPlot, any metric of any device, 1h/6h/24h/7d/30d,
  with tiered rollups under the hood for fast year-scale queries.
  "Compare packs" overlay across multi-pack rigs; Solcast forecast
  overlay when viewing `pv_power_w`
- **Devices** — detail cards with firmware/serial/all metrics
- **Conditional alert banner** — hidden when healthy, shows on cell
  drift, low SoC, over-temp, comms loss

## Architecture

```
solar_monitor/
├── transport/              # how bytes get to a device
│   ├── base.py             # Transport ABC
│   ├── ble_modbus.py       # BLE GATT speaking Modbus (Renogy BT-1/BT-2)
│   ├── serial_modbus.py    # RS-485 over USB
│   └── registry.py
├── vendors/                # protocol knowledge per device family
│   ├── base.py             # DeviceDriver ABC, Section
│   ├── registry.py
│   └── renogy/             # one folder per vendor
│       ├── rover.py
│       └── smart_battery.py
├── export/                 # pluggable downstream sinks
│   ├── base.py             # Exporter ABC
│   ├── registry.py
│   └── mqtt.py             # JSON snapshots → MQTT broker
├── alerts/                 # local rule engine + notification transports
│   ├── engine.py           # rule eval, quiet-hours buffer
│   └── transports/         # ntfy / Discord / webhook / SMTP / MQTT / Pushover
├── forecast/               # third-party PV forecast integrations
│   ├── base.py             # ForecastProvider ABC + normalised shape
│   ├── solcast.py          # Solcast hobbyist API client
│   └── service.py          # poll loop, kv-cache writeback
├── weather/                # current-conditions integrations
│   ├── base.py             # WeatherProvider ABC + normalised shape
│   ├── openmeteo.py        # Open-Meteo client (no API key required)
│   └── service.py          # poll loop, kv-cache writeback
├── storage/
│   └── sqlite.py           # WAL, raw + 1min/1hr/1day rollups, energy
│                           # aggregates, retention purge, kv blob cache
├── api/
│   └── app.py              # Litestar HTTP + static SPA
├── web/                    # vanilla HTML/CSS/JS, no build step
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── uPlot.iife.min.js   # vendored, no CDN
│   └── uPlot.min.css
├── modbus.py               # Modbus RTU framing + CRC
├── config.py               # YAML schema (msgspec)
├── orchestrator.py         # Poller — holds open transports across polls
├── scheduler.py            # background poll loop + maintenance task
└── cli.py                  # solar-monitor poll | serve
```

See [docs/architecture.md](docs/architecture.md) for the why behind
each layer.

## Adding a vendor

Drop a folder under `solar_monitor/vendors/`, write one driver class
per device kind, register the vendor, add one import line to
`vendors/__init__.py`. Zero core changes.

Full recipe: [docs/adding-a-vendor.md](docs/adding-a-vendor.md).

## Releases + cloud

- [docs/cloud-architecture.md](docs/cloud-architecture.md) — UniFi-style pattern: cloud as router + overview, appliance as source of truth, tunnel for remote access
- [docs/release-pipeline.md](docs/release-pipeline.md) — how features reach a customer's Pi: SD image build, source tarballs, in-place Update-now flow, the self-hosted `releases.wattpost.io` infrastructure

## Licence

Proprietary. All rights reserved. See [LICENSE.txt](LICENSE.txt).
