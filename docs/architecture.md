# Architecture

This document captures the *why* behind solar-monitor's structure so a
future maintainer (or second engineer) can make changes without
re-deriving every decision.

## Product goals (shapes everything below)

1. **Local-first.** Works without internet. Optional Tailscale for
   remote. No cloud requirement, ever.
2. **Multi-vendor.** Renogy, Victron, JK-BMS, Daly, plus future BMSes
   and shunts. Adding a vendor is a folder drop, not a refactor.
3. **Pi-hole UX.** SD-card image, web admin on the LAN, no mobile app
   to ship.
4. **Solid foundation > more features.** This is shaped by an explicit
   research synthesis (see `MEMORY.md` if you have my agent memory, or
   the v0.0.1 changelog otherwise) — every layer was picked from a
   comparison of analogous successful products.

## Layered diagram

```
┌─────────────────────────────────────────────────┐
│  Browser   (vanilla HTML/CSS/JS + uPlot)        │
└──────────────────────┬──────────────────────────┘
                       │ HTTP + WebSocket (planned)
┌──────────────────────▼──────────────────────────┐
│  Litestar app  (REST + static file router)      │
└──────────────────────┬──────────────────────────┘
                       │
  ┌────────────────────┼─────────────────────┐
  │                    │                     │
┌─▼────────┐    ┌──────▼──────┐    ┌─────────▼─────┐
│ Storage  │    │ Scheduler   │    │ Exporters     │
│ (SQLite  │◄───┤  + Poller   ├───►│ (MQTT today,  │
│  WAL)    │    │             │    │  Influx/HTTP  │
│          │    │             │    │  later)       │
└──────────┘    └──────┬──────┘    └───────────────┘
                       │
                  ┌────▼─────┐
                  │ Vendor   │
                  │ drivers  │
                  └────┬─────┘
                       │ Modbus RTU frames
                  ┌────▼─────────┐
                  │ Transports   │
                  │ (BLE, RS-485)│
                  └────┬─────────┘
                       │ bytes
                  ┌────▼─────────┐
                  │ Physical bus │
                  │ (BT-2, USB-485)│
                  └──────────────┘
```

## Why each layer

### Transports (`solar_monitor/transport/`)
**Why it's its own layer:** Drivers shouldn't know whether their bytes
travel over BLE or RS-485. The Transport abstraction means a Renogy
Rover driver works the day someone plugs a USB-RS485 dongle into a Pi,
with zero driver changes.

**`request(frame, expected_len, timeout)` interface.** Modbus-flavoured
on purpose — most vendors of interest speak Modbus RTU. Non-Modbus
protocols (Victron's BLE Instant Readout broadcasts; JK-BMS's custom
GATT framing) will need a sibling abstraction (`BroadcastTransport`
maybe), not a refit of this one.

**One transport == one open BLE link / one open serial port.**
Multiple downstream Modbus slave IDs share the link. Avoids the
reconnect-per-poll churn that early prototypes suffered from.

### Vendors (`solar_monitor/vendors/`)
**Why a registry pattern:** adding a vendor is dropping a folder +
adding one import line in `vendors/__init__.py`. The orchestrator
doesn't know vendor names ahead of time; it consults `VENDORS[vendor]`
at config-parse time.

**Drivers declare `sections` (Modbus reads) + parser functions.** The
base class walks sections, validates frames, calls parsers, merges
results. Vendors with non-Modbus protocols override `poll()` directly.

**Normalised output keys.** Every driver outputs `battery_voltage_v`,
`pv_power_w`, etc. — SI units, never °F or kW. The UI is vendor-
agnostic because it can read `cell_voltage_2_v` from a Renogy battery
or a JK-BMS without caring which.

### Modbus framing (`modbus.py`)
A tiny shared module so transports and drivers don't reimplement CRC16
or response validation. Pulled out specifically because both the BLE
and serial transports need it.

### Storage (`solar_monitor/storage/sqlite.py`)
**Why SQLite, not InfluxDB / TimescaleDB / VictoriaMetrics:** Pi
appliance, low write rate (~100 rows/min sustained), atomic file
backup, zero ops. Benchmarks on Pi class showed SQLite-WAL beating
DuckDB by 2-60× for our write pattern and well outside the noise
floor of "good enough."

**Long-format `samples` table** (one row per device/metric/ts) so
adding a new metric doesn't `ALTER TABLE`. String metrics get
`samples_str` separately.

**Tiered rollup tables** (`samples_1min`, `_1hour`, `_1day`) with avg
/ min / max / count. `get_history` selects the right table by range:

| Range | Table | Native bucket |
|---|---|---|
| ≤ 6 h | `samples` | 1 s |
| ≤ 7 d | `samples_1min` | 60 s |
| ≤ 90 d | `samples_1hour` | 3600 s |
| > 90 d | `samples_1day` | 86400 s |

**Maintenance task in the scheduler** re-rolls the active window every
10 min (idempotent via `INSERT OR REPLACE`) and purges past retention
(raw 7d, 1-min 30d, 1-hour 1y, 1-day forever).

**`today_aggregate()`** integrates V × I across today's polls
(trapezoid rule) to compute `bank_charged_today_wh`,
`bank_discharged_today_wh`, and the derived `load_today_wh` —
because the Rover's `consumption_today_wh` only counts its load
output terminals, which almost nobody uses for real loads.

### Scheduler (`scheduler.py`)
**Two background tasks** managed as one unit:

1. **Poll loop** — calls `Poller.poll()` every `interval_seconds`,
   records the result, dispatches to exporters. Exponential backoff
   with jitter on consecutive failures (capped at 5 min).
2. **Maintenance loop** — every 10 min, calls
   `store.rollup_and_purge()`. Starts 30s after daemon start to avoid
   stepping on the first poll's writes.

**Long-lived `Poller`** holds transports open across the entire
daemon lifetime. Re-opens any transport that drops between polls
(`_ensure_open`). Critical for BLE — the BT-2 sometimes refuses to
re-advertise for 20+ s after disconnect, so reconnect-per-poll is
unworkable.

### Exporters (`solar_monitor/export/`)
Same registration pattern as transports + vendors. Each exporter has
its own async queue and worker; a slow MQTT broker can never stall
the scheduler.

**MQTT today** because the research said "make MQTT first-class from
day one, not retrofit." Power users pipe to Home Assistant / Grafana
via MQTT. Future exporters: webhook, HTTP push, InfluxDB direct.

### Forecasts (`solar_monitor/forecast/`)
Same provider-registry shape as alert transports. Each provider is
one client (`SolcastProvider`, future `TomorrowIoProvider`) returning
a normalised `PvForecast` (Watts, unix seconds). One
`ForecastService` background task per daemon owns the poll loop and
writes the JSON payload to the SQLite `kv` table at
`forecast:pv` — cache survives daemon restarts so the dashboard
isn't blank for hours after a reboot.

**Why we don't proxy keys.** Solcast's hobbyist terms require one
key per end-user; we'd lose the right to ship if we tried to share
a key. The upside is privacy — your forecast never goes through
anything we operate. Each appliance talks to Solcast directly over
HTTPS using the user's own credentials.

### Weather (`solar_monitor/weather/`)
Sibling of `forecast/`, same shape. `OpenMeteoProvider` returns a
normalised `CurrentWeather` (temp / cloud / wind / WMO code /
sunrise / sunset), `WeatherService` runs the 15-minute poll loop,
cache lands at `weather:current` in the same `kv` table.

**Why a separate module from forecast/.** The two answer different
questions and many users want one without the other (Open-Meteo
needs no signup, Solcast does). Keeping them split also leaves
room to add an `irradiance/` or `tariff/` module without inflating
either neighbour.

**No API key.** Open-Meteo's hobbyist endpoint is free and unkeyed —
lat/lon is the only "credential." Future paid-tier providers
(Tomorrow.io, Visual Crossing) can slot in with the same provider-
registry pattern when needed.

### API (`solar_monitor/api/app.py`)
**Litestar** because msgspec serialization is 10-20× faster than
Pydantic, the WebSocket story is first-class, and a single process
hosting REST + WS + static SPA fits a Pi Zero 2's RAM ceiling without
sweat.

**Endpoints kept minimal:**
- `/api/health` — liveness
- `/api/devices` + `/api/devices/{label}/latest` — current snapshots
- `/api/devices/{label}/history?metric=…` — time-series
- `/api/poll_run` — last-run telemetry for the status pill
- `/api/today` — energy-balance aggregates
- `/` — index.html, with `Content-Disposition: inline` so Safari
  renders it instead of downloading

**Static files served from same Litestar app.** When we move to a
SvelteKit build later, the `web/` directory becomes the static-adapter
output and everything else is identical.

### Frontend (`solar_monitor/web/`)
**Vanilla HTML + CSS + JS today, by choice.** A 200-line static page
gets us to product-market fit faster than a build toolchain on the
Pi. uPlot is vendored (no CDN — local-first promise) and the asset
URLs are cache-busted by version query string.

**Layout** is the convergent answer from three research threads —
not a guess. The 7 must-haves:

1. SoC % big, with a graphical fill (donut, ring, or bar)
2. Time-to-empty / time-to-full
3. Net battery W, signed
4. Live source → battery → load flow with direction
5. Today's totals (PV in, load consumed)
6. Conditional alert banner (red only when something's wrong)
7. Works offline

Per-cell view + drift detection are the differentiator vs Renogy +
Victron — they don't surface this.

**State classes drive everything.** `donut-wrap.discharging`,
`hero-v2.soc-low`, `panel-cells.drift-warn` — JS sets a class per
poll, CSS does all the visual work. No imperative tinting in JS.

**Cache busting.** `?v=N` query string on static asset URLs forces
Safari to fetch fresh versions; bump on every release.

## Config (`config.py`)
**YAML with msgspec.** One file, declarative. Transports section is
intentionally generic dicts because each transport type has different
fields; the typed structs are at the device level where the shape is
stable.

**Multiple transports allowed.** A user can have a BT-2 over BLE *and*
an RS-485 cable into the Pi, with different devices on each, all in
one config. The orchestrator opens unique transports once.

## Cross-cutting principles

- **Build for replaceability, not perfection.** Every layer above can
  be swapped independently. SvelteKit can replace vanilla; RAUC can
  replace tarball updates; Keygen.sh can replace hand-issued
  licences. Engineer the seams.
- **SI units everywhere internally.** UI converts at the edge only.
- **No vendor lock-in inside the codebase.** No "renogy-specific" code
  in the orchestrator, no Renogy strings in the API, no Renogy
  assumptions in the UI.
- **Errors collected, not raised.** A single failed section or device
  doesn't kill the poll cycle. The dashboard surfaces it via the alert
  banner.

## What this isn't

- **Not a control system.** Read-only by design. We never write
  registers to chargers, never set load output state, never flash
  firmware. Monitoring only — that's the safety story.
- **Not a Victron / Solar-Assistant clone.** Different segment. We
  serve the smaller, BLE-first, sub-$2k systems they explicitly skip.
- **Not a Home Assistant integration.** HA users get MQTT export and
  can build their own dashboards. We are the standalone appliance for
  people who don't want to run HA.
