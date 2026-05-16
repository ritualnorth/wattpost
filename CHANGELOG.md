# Changelog

All notable changes to solar-monitor. Format: [Keep a Changelog].
Versions follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

## [0.0.25] — 2026-05-16

### Added — USB GPS support (#125)
- **New `gps:` config block** for mobile/van installs. Daemon
  reads NMEA-0183 from a configured serial port (typically
  `/dev/ttyACM0` for a USB-CDC receiver like the VK-162 G-Mouse).
  No external dependency on `gpsd` — pyserial + a minimal RMC
  decoder in `solar_monitor/gps/nmea.py`.
- **Significant-move detection** via the haversine distance from
  the last applied fix. Defaults: >5 km from previous applied
  fix OR >30 min stale → triggers a one-shot re-fetch of weather
  + Open-Meteo PV forecast at the new coordinates.
- **Solcast is intentionally not re-fetched on moves** — it's
  site-based (see `project_target_customer` in agent memory and
  the #130 release notes). When GPS is active, switch your
  forecast provider to `openmeteo` for moving-van support.
- **In-memory location updates only.** We mutate
  `config.weather.lat/lon` and (for Open-Meteo) `config.
  forecast.lat/lon` at runtime; we DON'T rewrite config.yaml on
  every move (would write hundreds of files a day in a moving
  van). The original config-file values are the cold-start
  fallback.
- **`GET /api/gps` status endpoint** — surfaces `configured`,
  latest fix, fix age, last-applied lat/lon. Settings UI panel
  will land in a follow-up commit; for now enable by adding a
  `gps:` block to config.yaml and restarting the daemon.

### Configuration example
    gps:
      port: /dev/ttyACM0
      baudrate: 9600           # default; usually fine for u-blox

### Notes
- VK-162 G-Mouse (£8 puck w/ magnetic base, 1 m USB cable) is the
  recommended receiver — better satellite reception than a USB
  stick because the puck can sit on the van roof.
- Wizard support (the "GPS support coming soon" button currently
  shown after USB-scan detects an NMEA-emitting device) will be
  wired in a follow-up once a customer has end-to-end-tested the
  serial → fix → re-fetch path with real hardware.

## [0.0.24] — 2026-05-16

### Added — Output schedules (Phase B of #104)
- **Cron-style local schedule engine** for any controllable output.
  Three trigger kinds: `time` (fires at fixed HH:MM in the
  appliance's local timezone), `sunrise`, `sunset` (both with a
  ± minute offset, sourced from the cached Open-Meteo sunrise/
  sunset timestamps — sun-relative triggers silently skip when
  weather isn't configured). Day-of-week mask (MTWTFSS bitmask)
  gates which days a rule fires.
- **Ticks once per poll cycle** alongside the existing outputs
  state refresh. Schedules dedupe within a day via `last_run_at`
  — a daemon restart won't re-fire today's already-run rules.
  Result of each fire is recorded ("ok" / "fail:reason") and
  shown in the UI under each schedule row.
- **API surface**: `GET/POST/PUT/DELETE
  /api/outputs/<id>/schedules` for full CRUD. Validates trigger
  shape + day-mask range. Backed by the `output_schedules` SQLite
  table that's been ready since v0.0.12.
- **Dashboard UI**: a collapsible "Schedules" section appears
  under each output panel on the device-detail page. Renders
  each rule with an enabled toggle + delete button + last-run
  status. "+ Add schedule" form has action radio (On/Off),
  trigger picker (Time/Sunrise/Sunset, with conditional
  time-vs-offset input), and day chips (MTWTFSS, default all).
  Lazy-loaded — the schedule list isn't fetched until the user
  taps the section, so users who only want the instant toggle
  pay no overhead.

### Closes the #104 saga
Phase A (instant toggle) shipped in v0.0.12. Phase B (schedules)
ships now. Phase C (cloud-fire — Pro tier) is the only remaining
piece, deferred until cloud-side roadmap pulls it in.

## [0.0.23] — 2026-05-16

### Fixed
- **Forecast form: Open-Meteo fields no longer leak in when
  Solcast is selected.** The `hidden` attribute was being emitted
  on the inactive provider's field group, but
  `.alerts-form-grid { display: grid }` was overriding the
  browser's default `[hidden]{display:none}` UA rule via
  specificity. Now Solcast users see only `api_key` +
  `resource_id`; Open-Meteo users see only `lat/lon/array_kw/
  tilt/azimuth/efficiency`. Same fix for the per-provider help
  paragraphs below the form.

## [0.0.22] — 2026-05-16

### Added — Renogy coverage finished
- **Renogy 1000W/2000W/3000W pure-sine inverter driver (#135).**
  Covers RIV/RNG-INVT inverter-charger family. Exposes AC input +
  AC output (V/A/Hz), battery side, integrated MPPT side (some
  models include solar), and AC load percentage. Modbus FC03
  over the existing BT-2 / USB-RS485 transports. Register map
  from cyril/renogy-bt's `InverterClient.py`. Registered as
  `(vendor=renogy, kind=inverter)`.

### Changed — Model classifier sweep (#134)
- **Model-string classifier now recognises the full Renogy line.**
  Probe + setup-wizard now routes:
  - `RVR/WND/ADV/VNG` (any model code) → `charge_controller`
    — covers Rover (40A/60A/100A), Rover Elite, Rover Boost,
    Wanderer (10A/30A/Li/PG), Adventurer (30A), Voyager (20A
    waterproof) and any newer SKU using the same prefix.
  - `DCC*` with a digit anywhere → `dcdc` — covers DCC50S,
    DCC30S, DCC25S, DCC15S (plus `RNG-DCC*` variants).
  - `RBT*` or `*LFP*` → `smart_battery`.
  - `RIV*` or `*INV*` → `inverter`.
- **Load-output discovery** in `outputs/renogy_rover.py` now
  matches bare prefixes (`RVR`, `WND`, etc.) too, so older
  firmware that drops the `RNG-CTRL-` vendor tag still gets a
  load toggle on the dashboard.

### Renogy coverage status
Effectively complete. The only gap is the Smart Shunt 300
(#113) — blocked on the lack of a community-documented register
map, will be unblocked via the discovery telemetry pipeline
(#129) or a customer-contributed Modbus capture.

## [0.0.21] — 2026-05-16

### Added — JK BMS (JiKong) support (#114)
- **New `ble_jkbms` BLE transport** for JK's proprietary GATT
  protocol (service 0xFFE0, char 0xFFE1). Maintains a persistent
  GATT connection + notification subscription; on connect, sends
  the "request cell info" command and the BMS auto-streams its
  state every ~1 s thereafter. Frame accumulator handles multi-
  notification frames (a single ~300-byte cell-info frame
  arrives as 15+ MTU-sized BLE chunks).
- **New `jkbms` vendor** with `bms` device kind. Auto-detects
  protocol version (JK02_24S vs JK02_32S) from frame length and
  parses with the correct field offsets. Per-cell voltages flow
  into the existing cell-balance dashboard tile; total V, current,
  SoC, time-to-go, temps, MOS state, cycle count, alarm flags all
  surface as standard normalised fields.
- **Why this matters**: JK BMS is the dominant choice in the DIY
  LFP crowd — 16x EVE 280Ah builds, 48V house banks, vanlife.
  Adding support brings that entire segment (orders of magnitude
  larger than the commercial-pack market) into WattPost's reach.
  See `project_target_customer` + `project_coverage_commitment`
  in agent memory for the strategic context.

### Validation status
- Cell-voltage parser validated against a real upstream frame
  fixture from syssi/esphome-jk-bms: 16 active cells at 3.327-
  3.329V (perfectly balanced 48V LFP bank, drift 2 mV) decoded
  correctly. Parser code mirrors syssi's C++ field offsets
  byte-for-byte for the trailer (V/A/SoC/temps/cycles/alarms),
  but trailer values are unvalidated against real hardware in
  this release — the fixture I had was hand-transcribed and
  inconsistent. First-customer validation will flush out any
  alignment issues; the code path is in place, the JK protocol
  is well-documented, and any field-position fixes are
  surgical.

## [0.0.20] — 2026-05-16

### Added — Victron coverage sweep
- **Victron SmartSolar MPPT driver (#131).** Every model from
  75/15 through 250/100 — they all share one `SolarCharger` BLE
  Instant Readout decoder, so one driver covers the whole family.
  Registered as `(vendor=victron, kind=charge_controller)` so the
  existing dashboard tiles render unchanged. Validated end-to-end
  against the upstream BlueSolar 75/15 fixture.
- **Victron Orion XS DC-DC driver.** Newer DC-DC line replacing
  the Orion-Tr Smart family. Adds proper output-current
  measurement. Registered as `kind=dcdc_xs` to coexist with the
  existing Orion-Tr driver (`kind=dcdc`).
- **Victron Smart BatteryProtect driver.** Load-disconnect device
  (cuts the load circuit at low SoC). `kind=load_disconnect`.
  Validated end-to-end against the 12/24V-65A fixture.
- **Victron Blue Smart AC Charger driver.** Mains-input battery
  charger; 3-output models surface each channel separately.
  `kind=ac_charger`. Validated end-to-end against the IP22 12/30
  fixture.
- **Victron Smart Lithium driver.** Victron's own LFP battery
  range. Surfaces per-cell voltages so the existing cell-balance
  panel works unchanged. `kind=smart_battery`.
- **Victron Lynx Smart BMS driver.** Distribution + BMS combo.
  Reports V/A/SoC/consumed Ah/time-to-go/temps/contactor state.
  `kind=bms`.

WattPost now covers every consumer Victron BLE Instant Readout
device. The only remaining Victron gap is VE.Bus (MultiPlus /
Phoenix / Quattro inverters) — needs a separate transport,
deferred until first customer asks.

## [0.0.19] — 2026-05-16

### Added
- **Renogy DCC50S / DCC30S driver (#123).** The DC-DC + MPPT combo
  charger that dominates mid-tier van builds — single device with
  both an alternator input and a solar input. Speaks Modbus RTU
  over the existing BT-2 / USB-RS485 transports; just a new
  register map. Configure with `vendor: renogy, kind: dcdc`.
- Exposes alternator side (V / A / W), solar/PV side (V / A / W),
  battery side (V / A / SoC / temp), daily extremes (min/max V,
  max A, max charging W, today's Ah + Wh), lifetime totals, and
  per-bit alarm decoding. Charging state includes the
  `alternator_direct` value the DCC50S exposes (engine running,
  pure alternator feed) that the Rover doesn't have.
- Register map sourced from cyril/renogy-bt's `DCChargerClient.py`
  — well-validated against real DCC50S hardware in production at
  multiple van builders.

### Notes for the user
- Two vendors now register `device_kind: dcdc`: Victron (Orion-Tr)
  and Renogy (DCC50S/DCC30S). The orchestrator resolves by
  `(vendor, kind)` tuple, so both can coexist on the same Pi.

## [0.0.18] — 2026-05-16

### Added
- **Victron Orion-Tr Smart DC-DC support (read-only).** Trivial
  follow-up to #112 — reuses the existing `ble_victron_advertise`
  transport, just registers a new driver under `device_kind: dcdc`.
  Exposes input voltage, output voltage, charging state (off/bulk/
  abs/float/etc), charger error, off reason (e.g. ENGINE_SHUTDOWN
  so the dashboard can show "waiting for ignition"), and model
  name. Validated end-to-end against the victron-ble library's
  upstream Orion fixture.
- Models covered: Orion-Tr Smart 12/12-18, 12/12-30, 12/24-15,
  24/12-30, 24/24-17. All share the same BLE Instant Readout
  protocol so a single driver covers the family.

### Deferred
- **#113 Renogy Smart Shunt 300** — no widely-documented OSS
  register map exists; shipping a guessed driver risks silently
  returning wrong values. Deferred until either a customer
  contributes a Modbus capture, or #129 (anonymous device-discovery
  telemetry) gives us enough samples to reverse-engineer.

## [0.0.17] — 2026-05-16

### Added
- **Open-Meteo PV forecast provider** — free, unlimited, lat/lon-
  based PV forecast that doesn't require a Solcast account. Solar
  irradiance from Open-Meteo is combined with the user's array
  geometry (capacity_kW, tilt, azimuth, system_efficiency) via a
  simple solar-position + tilt-cosine model to estimate PV output
  hourly for 7 days. Validated end-to-end against a real UK
  location — physically sensible peak watts + day totals.
- **Settings → Integrations → PV forecast** form now has a
  provider dropdown: pick "Solcast (site-trained ML)" for fixed-
  roof installs with a registered account, or "Open-Meteo
  (irradiance estimate)" for moving vans / no-account installs.
  Each provider shows only its own field set; the picker
  swaps them. Lat/lon left blank inherits from the weather
  integration's location.
- **Why this matters**: Solcast is fundamentally site-based
  (free tier = 10 calls/day, max 2 sites, no API to register
  sites) — a non-starter for moving vans + a real barrier-to-
  entry for casual users. Open-Meteo doesn't have any of those
  limits. We're already calling Open-Meteo for current weather;
  the irradiance endpoint is one more parameter.

### Notes
- Forecast accuracy hierarchy: Solcast best for fixed roofs (site-
  trained ML), Open-Meteo good enough for "should I drive south
  tomorrow" + as a no-config default. Future ticket #125 (USB
  GPS) auto-switches to Open-Meteo when location drift is
  detected.
- Today / Tomorrow tile sub-line now credits whichever provider
  is configured rather than hard-coding "Solcast".

## [0.0.16] — 2026-05-16

### Added
- **USB-scan now classifies each device by protocol.** The wizard's
  wired-adapter list opens each `/dev/ttyUSB*` / `/dev/ttyACM*`,
  reads briefly, and tags it as:
  - `Modbus` — silent serial (the typical case) — "Use as Modbus" button
  - `NMEA GPS` — emitted `$GP…` / `$GN…` sentences (preparation for
    #125 USB GPS support; button disabled with "coming soon" hint)
  - `unknown output` — bytes seen but no recognised pattern
  - `port busy` — already held by another process
- Stops users accidentally adding a GPS receiver as a Modbus
  transport — a £8 VK-162 G-Mouse GPS would otherwise show up
  alongside legitimate RS-485 adapters and silently fail every poll
  after pairing.

### Notes
- Detection is read-only (no Modbus probe write at scan time). The
  existing `/api/setup/probe` endpoint does an active slave-ID
  sweep once a Modbus transport is selected — that's where real
  device confirmation happens.

## [0.0.15] — 2026-05-16

### Added
- **"Add another transport" in the setup wizard.** Once you've got
  a transport configured, a collapsible tile under the list lets
  you wire up a second one without deleting the first. Same two
  buttons (Bluetooth / Wired USB-RS485) as the empty-state
  picker. Pairs cleanly with the underlying architecture —
  BLE and USB serial subsystems are completely independent on
  the Pi, so a single host can run a Renogy BT-2 for the MPPT
  *and* a USB-RS485 dongle for a JK BMS at the same time without
  contention.

## [0.0.14] — 2026-05-16

### Added
- **Setup wizard now also finds USB-RS485 adapters.** Phase 1 of
  the unified-wizard work (#120). The "no transports configured"
  empty state now has two paths: "Bluetooth (e.g. Renogy BT-2)"
  (existing) and "Wired (USB-RS485 adapter)" (new). The wired
  path enumerates every `/dev/ttyUSB*` / `/dev/ttyACM*` the host
  sees, labels each with the chip (FTDI FT232 / WCH CH340 /
  Prolific PL2303 / Silicon Labs CP210x), and the user picks one
  with a single tap. Add-transport writes a `serial_modbus` block
  with sensible defaults (9600 baud, 8N1 — Renogy/Epever standard).
- **Why this matters**: replacing the BT-2 dongle with a wired
  USB-RS485 dongle (~£10) gives sub-millisecond round-trips, no
  BLE timeouts, and proper FC06 ack frames (fixing the silent-ack
  quirk we hit during #104 de-risk). It also opens the door for
  customers who don't have line-of-sight BLE to their kit — cabin
  installs, gear in a metal-roof barn, etc.

### Notes
- Phase 1B of #120 (Victron / JK BMS pattern-specific forms in
  the wizard) lands in a follow-up. The current Victron driver
  (v0.0.13) still needs manual YAML config; #118 tracks that gap.
- See the wizard's new tooltip: the RJ45 port on chargers is
  **RS-485, not Ethernet** — Cat5 from there terminates at a
  USB-RS485 dongle on the Pi, NOT the Pi's network jack.

## [0.0.13] — 2026-05-16

### Added
- **Victron SmartShunt support (read-only).** The BMV-style
  battery monitor — voltage, current, SoC, time-to-go, consumed
  Ah, aux input (starter/midpoint/temperature), model + alarm
  state — now lights up the same dashboard tiles as our other
  vendors. New BLE transport `ble_victron_advertise` runs a
  passive BleakScanner that decrypts Victron's Instant Readout
  advertisements via the per-device key (find it in
  VictronConnect → Product info → Show device key). New vendor
  `victron` with driver `shunt`. Validated end-to-end against
  the `victron-ble` library's upstream test fixtures — every
  field decodes correctly.
- Adds `victron-ble>=0.10` as a dependency.

### Notes for early adopters
- v0.0.13 ships the engine — a wizard flow for adding a Victron
  device is coming in #118. Until then, drop a transport block
  and matching device block into `config.yaml` manually:

      transports:
        - id: shunt1
          type: ble_victron_advertise
          address: CC:CC:CC:CC:CC:CC
          encryption_key: aff4d0...  # 32-char hex from VictronConnect

      devices:
        - transport: shunt1
          vendor: victron
          kind: shunt
          slave_id: 0
          label: shunt

- **Write capability is permanently out of scope for Victron.**
  Heavy-Victron customers live on VRM/Cerbo — chasing them is a
  rabbit hole we won't go down. See `project_victron_scope` in
  the AI's memory for the strategic call.

## [0.0.12] — 2026-05-16

### Added
- **Renogy MPPT load-output toggle.** Rover-family chargers (Rover
  / Wanderer / Adventurer / Voyager) now expose their 12 V load
  terminal as a controllable output on the device-detail page.
  Toggle button writes register 0x010A via FC06 and confirms the
  new state via an explicit FC03 read-back inside the same BLE
  session — works around the BT-2 dongle quirk where Rover
  firmware 3.x silently swallows FC06 ack frames. Confirmed
  end-to-end against a real RNG-CTRL-RVR40 FW 3.1.0.
- **One-shot safety gate** before the first toggle on any output:
  the panel explains what's about to happen ("write command to
  your charger, the load terminal will switch") and the user has
  to acknowledge before any control surface appears. Persisted
  per-output — won't nag on every visit.
- **Audit line** under each control: "Last command: on · 6 sec
  ago · by user · ok". So you can see whether a command actually
  landed, especially handy when BLE was wobbly.
- **Generic `ControllableOutput` schema + adapter protocol** under
  `solar_monitor/outputs/`. JK BMS charge/discharge MOS toggles
  drop into the same UI + (forthcoming) schedule engine without
  per-vendor UI work. Today's adapter is `renogy_rover`; more
  arrive with #114 (JK BMS).

### Foundations (no user-visible change yet)
- **FC06 `build_write_single` helper** in `modbus.py`, plus
  `verify_response(expected_fc=...)` so future write functions
  share the same exception-code plumbing as FC03.
- **SQLite tables `controllable_outputs` + `output_schedules`.**
  The schedules table lands ahead of the scheduler tick that
  uses it (Phase B of #104) — the schema's lighter to evolve
  if it ships in one shot.

## [0.0.11] — 2026-05-16

### Added
- **Right-now tile now shows the next 8 hours.** Apple-Weather-
  style hourly strip at the bottom of the panel: HH:00 label,
  a tiny WMO icon (sun / partly cloudy / cloud / rain / snow /
  thunder, switched to a moon for night-time clear skies), and
  the predicted °C for each cell. Pulled from the same
  Open-Meteo fetch as the current conditions — one extra HTTP
  param, no new provider — so refresh cadence + auth-free
  setup is unchanged. The strip is hidden if the provider
  doesn't return hourly data, and scrolls horizontally on
  narrow viewports rather than wrapping.

## [0.0.10] — 2026-05-16

### Changed
- **Dashboard tile redesign — Today is now the headline.**
  The standalone "Tomorrow" tile is gone; its content folds
  into the Today panel as a sub-line. The Today panel now
  shows kWh-so-far as a big hero number, with a forecast
  sparkline running across the day (solid for the past,
  dashed for "still to come") and a faint "now" marker. The
  sub-line tells you what was expected and what's still to
  come ("Of 3.8 kWh expected · 1.4 kWh still to come"). The
  Tomorrow preview drops to a one-line footer at the bottom
  of the tile.
- **Sunset flip.** After dusk — when no PV is forecast for
  the rest of today and tomorrow's window has data — the
  Today tile auto-flips: Tomorrow's expected kWh becomes the
  headline, today's tally demotes to "Today (final): PV …
  Load …" in the footer. The dashboard's "operational
  moment" stays unambiguous all day.
- **5-day outlook highlights Today**, not Tomorrow. Matches
  the headline tile so the "you are here" anchor reads
  consistently top-to-bottom.

### Fixed
- **"Right now" weather tile gets its background tint back.**
  Was the only dashboard tile without a panel tint, which
  made it visually inert next to the others.

## [0.0.9] — 2026-05-16

### Fixed
- **Bumped the `/web/app.js?v=` cache-buster** in index.html.
  Several recent appliance fixes (Settings → About row visibility,
  history chart forecast bound, Check-now button focus state)
  were sitting unread in the container because the script-tag's
  version query hadn't moved since v0.0.5 — Cloudflare's edge
  was serving the same URL out of its 4h cache regardless of
  what the container actually held. From now on the index.html
  `?v=` must move in lockstep with `sw.js` CACHE_VERSION so
  every JS update gets a fresh URL that bypasses any CDN cache.

## [0.0.8] — 2026-05-16

### Fixed
- **History chart: the forecast overlay no longer stretches the
  x-axis past the selected range.** Picking "6h" used to render
  a week-wide axis because Solcast's full 7-day forecast was
  appended. The forecast horizon now mirrors the chosen history
  window (1h history → 1h forecast, 24h → 24h, etc).
- **"Check now" button on Settings → About stops looking
  pressed** after the action completes — it was the iOS focus
  ring sticking; we now blur the button when the work returns.
- **iOS Safari picks up appliance updates faster.** Service
  worker registration now uses `updateViaCache: 'none'`, so
  Safari fetches the SW file fresh on every page load instead
  of holding the previous version's cached copy for hours.

## [0.0.7] — 2026-05-15

### Fixed
- **Settings → About uptime now reports the daemon's uptime**,
  not the host's. The previous `/proc/uptime` read leaked the
  host machine's uptime through Docker — a freshly-restarted
  container could show "3d 23h" if that's how long the laptop
  had been booted.
- **"Updates: docker compose pull..." row only shows when
  there's actually an update pending.** Used to render
  permanently on every Docker install, even with nothing to
  apply — read as a nag.
- **Fresh installs land in the setup wizard automatically.**
  First-time users opening the dashboard with zero transports
  configured used to see an empty dashboard with a "Setup
  needed" pill top-right and no signpost. They now get
  redirected straight to `#/setup` on first paint.

## [0.0.6] — 2026-05-15

### Added
- **Cloud dashboard shows weather + PV forecast per site.**
  Each heartbeat now ships the appliance's cached weather
  snapshot (temperature, conditions, sunset) and Solcast
  forecast totals (today + tomorrow kWh). The cloud card
  surfaces a quiet strip — e.g. *"☀ 16°C · Mostly clear ·
  Sunset 19:42 · Today 4.2 kWh PV · Tomorrow 5.1 kWh"* — so a
  glance at app.wattpost.io tells you whether your off-grid
  setup is going to make it through the day.
- **Appliance reports its install method** in the heartbeat
  (`pi` vs `docker`). The cloud uses this to hide the
  "Update now" button on Docker installs (where the action
  has to happen on the host via `docker compose pull`).

### Changed
- Update notes pulled from `releases.wattpost.io/CHANGELOG.md`
  remain the same source as before — this entry will appear
  in the dashboard's "Release notes" link.

## [0.0.5] — 2026-05-15

### Changed
- **Setup wizard BLE scan now flags "recently visible but
  missing" dongles** — when a BT-2 was seen in the last 15 min
  but doesn't respond to the active scan, the wizard surfaces
  it with a likely-cause hint (most commonly: the Renogy mobile
  app is holding the connection). Replaces the previous silent
  empty-list outcome.
- **Docker installs no longer show Tailscale UI rows.**
  Tailscale doesn't run inside the container, so the rows were
  dead toggles. Pi installs are unchanged.

### Fixed
- **iOS Safari text inflation** on `/docs` made body text ~2×
  nav text. Pinned with `text-size-adjust: 100%`.
- **Docs grid overflowed viewport** on narrow mobile screens —
  wide tables now horizontally scroll, grid cells respect
  viewport width.

## [0.0.4] — 2026-05-15

### Added
- **Cloud-managed updates for Pi appliances.** Multi-site
  dashboard renders an amber "v0.0.x → v0.0.y" pill on cards
  running behind `releases.LATEST` and a one-click
  "Update to v0.0.y" button that queues the action; the
  appliance picks it up on its next heartbeat, runs
  `wattpost-update`, and the cloud auto-reconciles the command
  to "success" when the new version reports in.
- **Stripe billing (v1).** $5 per appliance per month, 14-day
  free trial, Stripe-managed grace period. Subscribe / Manage
  billing buttons in the cloud dashboard and account page.
  Webhook ingestion mirrors subscription state into the
  `appliances` table.
- **Per-device delete button on the appliance Devices tab** —
  one click + confirm to drop a slave from polling, no trip
  through the Setup wizard.
- **Bank aggregate pinned to the top of the Devices tab.**
  Previously filtered out; now the headline reading sits where
  users look first.
- **Live-streaming Setup → Scan.** The wizard now shows
  "Probed N of 17 · X responded" with results streaming in as
  each slave answers, instead of staring at a spinner for ~60s
  while the full sweep finishes.
- **SQLite migration framework on the appliance.** Future
  schema changes evolve existing databases via PRAGMA
  user_version instead of breaking customers.
- **`cloudflared` bundled in the Docker image.** The paired-
  cloud "Open site" tunnel now works identically on Pi and
  Docker installs.

### Changed
- **BLE auto-detects the notify characteristic** per BT-2
  firmware generation (`fff1` first, `ffd2` fallback). Setup
  wizard's scan finds Renogy devices reliably across both
  generations without manual config.
- **BLE self-heals stale BlueZ state** on connect failures —
  the daemon now sends `bluetoothctl disconnect` + `remove`
  on the retry path, recovering from "device disconnected
  during service discovery" without operator intervention.
- **Bank aggregate is now stable across single-pack poll drops.**
  Previously recomputed from "what answered this cycle";
  now augmented with cached snapshots from any pack last
  seen within 5 min. `pack_count` no longer flips between
  3 and 2 on a noisy BLE link.
- **Cross-subdomain session.** Cookie scoped to
  `.wattpost.io` so a logged-in user clicking "Download" or
  visiting `wattpost.io/docs` stays signed in instead of
  apparently logging out.
- **Hot-reload wizard writes.** add_device / add_transport /
  add_forecast / add_weather + their deletes all now return
  in <10ms with a background hot-reload, instead of awaiting
  the ~5s scheduler swap. No more "saving…" hangs during
  scans.
- **Update notes now fetched live** from
  `releases.wattpost.io/CHANGELOG.md`, so the in-app
  "Release notes →" link previews entries for a version the
  user hasn't installed yet.
- **Marketing + docs theme defaults to system** preference,
  not hardcoded dark.

### Fixed
- **"Setup needed" pill stuck on amber** despite a healthy
  appliance — SSE snapshot's `poll_run` was missing the
  `transports` field, so every tick reset the dashboard's
  view to "no transports configured".
- **Pairing flow re-introduced "Restart daemon"** UX after the
  hot-start path was added; UI now respects the
  `restart_required: false` response.
- **About → Update section** showed "Latest available —" and
  a stuck "Update progress: waiting…" on Docker after earlier
  manual Update-Now clicks. Both rows now hide when there's
  nothing to apply.
- **Setup wizard locked users out** when the BLE link was
  idle-dropped — the transport row went disabled with no
  recovery. Now: row stays clickable, scan auto-reopens the
  link.

## [0.0.3] — 2026-05-15

### Added
- BLE transport auto-detects the notify characteristic per Renogy
  BT-1 / BT-2 firmware revision (`ffd2` on newer modules, `fff1`
  on older ones). Setup wizard's Scan step now works against both
  generations without manual config.
- About → Update surfaces the new version number and a "Release
  notes →" link to the in-app `#/docs/release-notes` page
  whenever an update is available, so users can see what changed
  before deciding to apply.
- Release notes are now fetched live from `releases.wattpost.io`
  on every manifest poll and cached on the appliance. Means the
  dashboard can preview a not-yet-installed version's changelog
  entry — bundled docs only cover versions ≤ the running release.
  Falls back to bundled `docs/release-notes.md` when offline.

### Changed
- Settings → About: Docker installs no longer show an in-app
  "Update now" button — they get a persistent hint to run
  `docker compose pull && docker compose up -d` on the host
  (matches Immich / Pi-hole / Vaultwarden conventions). Pi
  installs are unchanged.

## [0.0.2] — 2026-05-15

### Added
- WattPost cloud (wattpost.io) — opt-in. Pair the appliance to a
  cloud account from Settings → Integrations → WattPost cloud,
  paste an 8-character code, daemon exchanges it for a long-lived
  bearer token and starts pushing 5-minute heartbeats. Cloud's
  multi-site dashboard shows online/offline per appliance and
  flags overdue heartbeats. Local appliance keeps working with
  no internet, no cloud, no account — strictly additive.

- Solcast PV forecast: configurable in Settings → Integrations
  (user supplies their own free API key + resource UUID), polled
  every 3h by default, overlaid as a dashed line on the History
  chart when viewing pv_power_w. New SQLite `kv` table caches
  the most recent fetch so a daemon restart doesn't blank the
  overlay.
- Dashboard "Tomorrow" tile: when a forecast is configured, a new
  panel between Today and Cell balance shows expected PV (kWh),
  peak power + time, day-after preview, and a translucent SVG
  sparkline of tomorrow's curve. Auto-hidden when no forecast
  data is cached.
- Dashboard "7-day outlook" strip below the Tomorrow tile —
  per-day kWh + mini sparkline across all forecast days, common
  Y scale so quiet days read as quiet next to sunny ones, the
  Tomorrow card highlighted as the focal point.
- History chart's forecast overlay now renders Solcast's
  P10–P90 confidence band as a translucent amber fill between
  the bounds — wide band = the model isn't sure, narrow = high
  confidence. Median line stays dashed on top.
- Current weather (Open-Meteo): new `weather/` module + Settings
  → Integrations row + dashboard "Right now" tile showing temp,
  conditions icon (WMO code → SVG), cloud cover, wind, humidity,
  sunrise / sunset. No API key required; user supplies lat/lon.
  Polls every 15 min by default, cached in the same `kv` table
  the PV forecast uses.
- Forecast accuracy: every Solcast fetch is now archived to a new
  `forecast_history` table (30-day retention). The Tomorrow tile
  grows a "Yesterday: predicted X · actual Y · Z% of forecast"
  line tinted green / amber / red by deviation. Surfaces drift in
  Solcast's site model (capacity, tilt, azimuth, shading) before
  it gets too far from reality.
- Charge efficiency: `/api/devices/{label}/efficiency` returns
  SoC-corrected coulombic efficiency over 7d / 30d / 90d / lifetime
  windows. Smart-battery device cards show an `η` tile picking the
  shortest reliable window; the device detail page shows the full
  4-window breakdown with greyed-out cells for windows that haven't
  seen enough cycling.
- History chart: "Compare packs" toggle overlays every smart_battery
  pack's metric on one chart. Auto-disabled when fewer than two
  packs are configured or when a non-battery device is selected.
- Quiet hours: `config.yaml` accepts a `quiet_hours: {start_hour, end_hour}`
  block. Inside the window, warn-severity alerts buffer and flush
  when the window ends; alarm-severity always pages through.
  Settings → Alerts now has a UI editor for the same setting
  (`PUT /api/alerts/quiet_hours`) so it isn't YAML-only.
- CI: `.github/workflows/build-image.yml` builds the SD image via
  pi-gen on tag push (`v*`) and attaches the `.img.xz` + SHA256 to a
  GitHub Release. `workflow_dispatch` available for smoke tests.
- Local alert engine — rule schema (metric / op / threshold / severity /
  cooldown), Settings → Alerts UI editor (rules + transports), per-rule
  Test button
- Notification transports: ntfy, Discord webhook, generic webhook,
  SMTP / email, MQTT-publish (LAN-local), Pushover
- CSV export of any metric over any range
  (`/api/devices/{label}/history.csv`)
- PWA install — manifest + service worker, dashboard installs to home
  screen on iOS / Android
- Tailscale auto-config — sudoers entry, `tailscale serve` for HTTPS,
  Settings → System surfaces the auth URL
- In-app docs (`/docs/...`) rendered from bundled Markdown — no
  external site needed
- Diagnostics — Settings → System shows recent log lines + a Restart
  daemon button (no SSH required)
- Kiosk mode — `#/kiosk` chrome-free SoC + flow tiles, Settings toggle
  to default-on for one device, Wake Lock keeps the screen on
- WebSocket / SSE live updates — dashboard streams snapshots after
  every poll instead of polling every 5s
- BLE discovery wizard — Setup page scans an open transport for new
  slave IDs and appends them to config.yaml
- Home Assistant MQTT discovery topics
- packaging/install.sh + systemd unit + pi-gen stage for SD-image
  builds

### Changed
- BLE transport now auto-recovers from "device not advertising"
  timeouts on daemon restart by clearing BlueZ's stale connection
  state (`bluetoothctl disconnect`) and retrying once
- Tailscale endpoints surface real sudo errors to the UI instead of
  returning `ok:true` and only logging — Enable HTTPS / Connect /
  Disconnect now show a username-aware fix-it hint
  (`packaging/dev-sudoers.sh` for dev shells, re-run `install.sh`
  for production `wattpost` user)

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
