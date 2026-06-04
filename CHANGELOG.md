# Changelog

All notable changes to WattPost. Format: [Keep a Changelog].
Versions follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

## [0.1.139-beta.1] - 2026-06-04

Beta — appliance support for the cloud consent-gated remote-access flow (read-only staff sessions).

### Added
- Appliance accepts a new **`staff_read`** broker scope: an owner-approved, read-only support session (any GET, never a write). Part of the cloud back-office consent flow (#10) — a WattPost engineer can only reach your box after you approve a request, and a read grant can look but not touch.

## [0.1.138-beta.1] - 2026-06-04

Beta — Ember kiosk skin: one seamless background, no more rectangular seam.

### Fixed
- Kiosk **Ember** skin no longer shows a visible rectangular seam ("two backgrounds"). The skin painted its own warm rect inside its 1.6 box while the page painted a separate warm gradient in the letterbox gutters; the two didn't align. Ember now paints one continuous background that overspills into the gutters, so it's seamless on any display shape.

## [0.1.137-beta.1] - 2026-06-04

Beta — kiosk skins centre on any display shape (no more sitting high).

### Fixed
- Kiosk skins now **centre on any display shape**. The skin SVG was collapsing to its intrinsic 1.6 aspect height and pinning to the top, so on a screen taller than that (e.g. a portrait-ish browser window) the artwork sat high with a dead band beneath it. The SVG now fills its container, so the letterbox is split evenly and every skin (Halo / Ember / Command) is vertically centred. Fixes the "sits too high" look across the board.

## [0.1.136-beta.1] - 2026-06-04

Beta — the kiosk harvested-today sparkline draws a real PV curve.

### Added
- Kiosk **harvested-today sparkline** now draws a real curve. The Command (and any skin using `daySeries`) tile plots today's PV production profile — pulled from `/api/energy/today`, normalised to the day's peak — instead of being blank. Also reachable through the brokered cloud kiosk.

## [0.1.135-beta.1] - 2026-06-04

Beta — Ember kiosk skin fills the screen edge to edge.

### Fixed
- Kiosk **Ember** skin: the warm background now fills the whole screen. The skin's artwork is letterboxed on most displays, and the page behind it was always the default cool-dark gradient — so Ember showed a jarring dark band in the gutters (usually below the flow row). The kiosk page background is now skin-aware and matches Ember's warm tone, so it blends seamlessly edge to edge.

## [0.1.134-beta.1] - 2026-06-04

Beta — Command kiosk skin: the Bank tile shows real data, and the layout fits wide screens better.

### Fixed
- Kiosk **Command** skin: the **Bank** tile is no longer blank — it now shows a capacity bar per battery pack (or a single bar for a one-bank system) reading live fill, instead of just the "300Ah bank" label. The skin also fills the screen a touch better (trimmed dead space below the tile row), so it doesn't sit high on wide displays.

## [0.1.133-beta.1] - 2026-06-04

Beta — GPS acquisition is finally legible, and the cloud knows which kiosk skin you're running.

### Added
- **GPS acquisition is now visible.** When a USB GPS is connected but hasn't locked yet, the Location panel says **"GPS connected — acquiring a fix: N satellites in view (best signal X dB-Hz)"** and warns when the signal's too weak to lock (needs a clearer view of the sky), instead of the old "no location source" that read as "GPS not detected". It auto-refreshes every few seconds so you can watch satellites climb and the fix land. Reads new `satellites_in_view` / `best_snr_dbhz` / `acquiring` fields from `/api/gps` (GSV sentence parsing).
- Heartbeat now reports the appliance's active **kiosk skin**, so the cloud's kiosk-share dialog can preview what a shared link will display ("Shared kiosks display the Command skin").

## [0.1.132-beta.1] - 2026-06-03

Beta — makes a shared cloud kiosk show the skin you actually picked.

### Fixed
- **Shared cloud kiosk now shows the chosen skin.** When you share a read-only kiosk link (`wattpost.cloud/k/…`), the visitor's brokered view is the appliance's own kiosk page — but it was always falling back to the default **Halo** skin because the read-only kiosk allow-list blocked it from reading the appliance's kiosk settings. It can now read (but never change) the skin, so a shared kiosk matches what you set on the wall display. (#28 Milestone D)

## [0.1.131-beta.1] - 2026-06-03

Beta — completes the wall-display kiosk (selectable skins) and fixes a couple of real-world gremlins (USB GPS vs ModemManager, the cloud→display kiosk toggle).

### Added
- **Kiosk skins** — pick how your wall display looks in **Settings → Appearance → Kiosk skin**: **Halo** (minimal, SoC-ring hero), **Ember** (warm van/night, a day-arc with the sun's position + big runtime, and it dims after sunset so it isn't a lightbox in a dark van), or **Command** (command-centre with a branching power flow + a tile band of battery / bank / forecast / weather). All three render from live data.
- Kiosk **defaults are now stored on the appliance**, not the browser — so set the skin or "open in kiosk" from a cloud session and the **local wall display obeys**. (Previously the toggle was per-browser localStorage and never reached the screen.)

### Fixed
- USB GPS: the appliance no longer fights **ModemManager** for the serial port — the SD image / `install.sh` ship a udev rule that keeps ModemManager off GPS + USB-RS485 adapters (the classic "GPS opens but returns no data, vanishes after a reboot"), and the daemon user is added to `dialout`.
- Location panel: the **share-with-cloud radios** (Off / Approximate / Precise) are settable before a fix exists — they're a preference that applies once a source comes online — instead of being greyed out.

## [0.1.130-beta.1] - 2026-06-03

Beta — bundles the new kiosk, the history analytics, the simpler update model, and the headless-onboarding work. Supersedes the 0.1.129-edge.1 test build.

### Added
- New kiosk skin engine + the **Halo** skin (#28 Phase 1). The wall-mount kiosk is now driven by a versioned `KioskViewModel` and a swappable skin: Halo leads with the state-of-charge ring, an animated power-flow (colour = source, thickness = watts), and time-to-full / harvested-today at a glance. Foundation for selectable skins (Ember/Command), a skin selector, night mode, and an eventual community skin gallery. Skin is chosen per-device; defaults to Halo.
- Kiosk exit button is now easy to find on a wall display: it flashes a few times when the kiosk loads, then reveals + highlights whenever you move the mouse or tap, fading out when the screen goes still.
- History — **PV actual vs forecast**: the PV forecast is now overlaid across the historical window (interpolated onto your actual samples), so you can see at a glance how the day tracked the forecast, not just the future curve. Drag to zoom, **double-click to reset**, and MIN/AVG/MAX recompute for whatever range you've zoomed into.
- First-boot setup hotspot (headless onboarding): a freshly-flashed appliance with **no network** — no WiFi set in Raspberry Pi Imager, no Ethernet — now raises its own `WattPost-Setup` WiFi access point automatically, so you can reach the dashboard from your phone with **no monitor and no router**. That's the van / off-grid setup case. Joining the AP pops the dashboard via the captive portal. It only triggers on a box that has *never* been on a network; once it's seen a LAN it latches off for good, so a home appliance that briefly loses WiFi never gets a surprise AP. Opt out with `hotspot.onboarding: false` (#27).
- Selective restore: when restoring a backup you can now choose which parts to bring back — **History & readings**, **Configuration**, **Dashboard password** (all on by default = a full restore). Untick *Configuration* to restore your history onto a fresh, clean config — the fix for "my config got broken, I just want my data back" (#26).

### Changed
- **Updates are simpler.** Two channels now — **Stable** and **Beta** — the confusing Edge channel is gone. And the in-dashboard **Update button now works on Docker too** (one click, via the bundled `wattpost-updater` sidecar) — no more `docker compose pull`.
- Docker appliance now defaults to **port 80** so the bare host IP works (`http://<host-ip>`), matching the SD-card image — no more `:8000`. Override with the `WATTPOST_PORT` env var if port 80 is already taken on the host. After pulling the new image, the dashboard moves from `http://<host-ip>:8000` to `http://<host-ip>`.

### Fixed
- WiFi-hotspot settings: the SSID and password fields no longer truncate ("WattPost-Sett") — they're full-width and left-aligned.
- Restoring a backup no longer silently drops your **WiFi-hotspot/onboarding settings** and **release-channel choice** — the restore's config allow-list was stale and stripped the `hotspot` and `update` keys (#26).

### Security
- The SD-card image now ships with **no default login or password** and **SSH disabled by default**. You set your own username, password, SSH and WiFi in Raspberry Pi Imager's settings when you flash (or via the first-boot wizard on an attached monitor). Previously the image shipped a default `wattpost` / `wattpost` SSH account — removed to meet the UK PSTI Act / EU Cyber Resilience Act ban on default credentials on consumer devices. The daemon runs as a separate locked system account and is unaffected.
- Backups no longer carry your plaintext secrets. The backup builder now **redacts third-party credentials** (SMTP, MQTT, Solcast/weather API keys, the WiFi-hotspot password) and **omits the plaintext dashboard-password file** before writing the tarball — so a downloaded or cloud-uploaded backup can't leak them. You re-enter these via Settings after a restore. The appliance's own cloud pairing tokens are kept so a fresh-Pi restore recovers its identity + history (cloud-issued, revocable). Previously redaction ran only on *restore*, not on backup.

## [0.1.128] - 2026-06-02

### Added
- Update-channel selector (Settings → About): choose **Stable**, **Beta**, or **Edge** release streams. Stable is tagged releases that have soaked; Beta is release candidates the moment they're cut; Edge is every commit to main (Docker-only). The daily update check follows the chosen channel, with a pre-release warning shown for Beta/Edge. Backed by per-channel release manifests on the cloud and a `:beta` Docker tag / `manifest-beta.json` Pi image in the release pipeline (#11)

## [0.1.127] - 2026-06-02

### Changed
- Cloud-managed backups: backup control moves off the appliance into the cloud dashboard. The appliance now honours the cloud's per-appliance "upload backups" toggle and backup schedule (interval + retention), delivered in the heartbeat and applied to the live backup config without a restart. The appliance's local "Cloud backups" tile is now a pointer to the cloud dashboard, where enable/browse/download/restore live. Local file backup/restore is unchanged (#17, #22)

### Fixed
- Prometheus `/metrics` no longer emits internal metadata fields (`_vendor`, `_kind`, `_slave_id`) as gauges — only real telemetry

## [0.1.126] - 2026-06-01

### Added
- Prometheus / Grafana export: a new export target that serves your live readings at a read-only `/metrics` endpoint in Prometheus text format. Point Prometheus (or Grafana Agent) at it and add Prometheus as a Grafana data source. No credentials, local-LAN, and it runs alongside the MQTT exporter, so you can feed Home Assistant over MQTT and Grafana over Prometheus at the same time. Enable it from Settings → Integrations (or `{type: prometheus}` in config.yaml). Each numeric per-device reading becomes a gauge, e.g. `wattpost_soc_pct{device="battery_0"}`. See [docs/integrations.md](docs/integrations.md) (#14)

## [0.1.125] - 2026-06-01

### Added
- WiFi hotspot (appliance-as-AP, Pillar 3): the appliance can turn its WiFi radio into a NetworkManager access point so a phone/laptop reaches the dashboard with no other network — field setup and off-grid. Off by default; configure and toggle from Settings → WiFi hotspot, or via `POST /api/hotspot/{on,off}`. Reachable at `http://10.42.0.1` while up. See [docs/hotspot.md](docs/hotspot.md)
- Hotspot auto-handoff (Pillar 3b): with "Auto-enable when offline" (`hotspot.auto_handoff`), the appliance raises the hotspot by itself whenever it has no other network and drops it when a real LAN returns. Local-only — works with no cloud subscription. Ethernet/dual-radio handoff is seamless; a single-radio Pi blips the AP briefly every few minutes while off-grid to re-test for known networks.
- Hotspot captive portal (`hotspot.captive_portal`): joining the hotspot pops the dashboard automatically on the device — the "Sign in to network" sheet — so no one has to type the address. While a captive AP is up the appliance hijacks DNS via a NetworkManager dnsmasq drop-in and answers the OS connectivity checks (Apple/Android/Windows) with a redirect to the dashboard. Needs NM DNS-dir write access (granted to the `wattpost` user by the packaged image); degrades to a no-op elsewhere with the AP unaffected.

### Fixed
- Settings → Integrations no longer fails the whole panel with "Could not load integrations: 429" over the cloud tunnel. The four per-integration config requests are folded into a single `/api/system/integrations` round-trip (no more 4-request burst tripping the edge limiter), with a 429 retry/backoff and a graceful partial render that keeps the last-loaded panel instead of blanking it (#18)

## [0.1.124] - 2026-05-29

### Added
- Battery-monitor view for shunt-only systems: when there is no charging source (just a battery + a shunt), the Today tile now leads with energy used today and shows charged + net battery throughput, instead of a meaningless "Harvested today: 0 Wh" with empty PV cells

### Changed
- Anonymous install-count beacon is now off by default, with a Settings -> Privacy toggle ("Anonymous install ping"). Was previously on by default with config-file-only opt-out (#217)

### Fixed
- Kiosk share view rendered a blank SoC donut over the cloud: the kiosk read allow-list was missing `/api/snapshot` (the donut's data source) and still listed the removed `/api/bank/current` (#225)
- Empty "Tomorrow" forecast footer could appear on setups with no solar forecast configured, because a `display` rule was overriding the element's hidden attribute

## [0.1.123] - 2026-05-28

### Changed
- Kiosk sharing for remote viewing is now managed entirely from the cloud (per-share links with expiry, one-click revoke, optional PIN). The appliance's legacy `?key=` kiosk URL and its Settings tile are removed; LAN wall-display kiosk is unchanged (#225)

### Security
- Cloud kiosk share revocation now takes effect immediately. The broker re-validates each share against the server on every request instead of trusting the session cookie, which previously kept a revoked share working for up to 24h (#225)

## [0.1.122] - 2026-05-28

### Fixed
- "Kiosk view on this device" toggle hidden + auto-redirect skipped under the cloud broker; the toggle is LAN-only, cloud kiosk goes through the share-token path (#225)

## [0.1.121] - 2026-05-28

### Fixed
- Bank aggregate now matches all inverter sub-kinds (#366 followup)

## [0.1.120] - 2026-05-28

### Added
- Deye / Sunsynk / Sol-Ark driver pair (#366, experimental)

## [0.1.119] - 2026-05-28

### Fixed
- Battery never disappears from Power Flow, label stops lying

## [0.1.118] - 2026-05-28

### Added
- EG4 XP / kPV / FlexBOSS driver (#364, experimental)

## [0.1.117] - 2026-05-28

### Fixed
- Dashboard tiles render inverter-only installs

## [0.1.116] - 2026-05-28

### Added
- Bank aggregate accepts inverter as SoC source (#361)
- Setup wizard supports usbhid_voltronic (#362)

### Fixed
- Setup transport status for request/response transports (#363)

## [0.1.115] - 2026-05-28

### Added
- Voltronic-family inverter driver (#360, experimental)

## [0.1.114] - 2026-05-27

### Added
- Cloud observability, GlitchTip + Umami (#338)

### Changed
- Pricing reset: single Cloud tier at £6/mo, no-card 14-day trial (#333, #334)
- Backup defaults: daily snapshots, 7-day rolling window (#336)
- Removed unfounded "Ltd" claims across all customer-facing surfaces

### Removed
- /landing-v2 preview reverted

### Fixed
- /app/account/billing 500 from a Jinja-eating JS comment
- Appliance command state machine: instant + idempotent transitions
- Identity v2 signed_audit: PyNaCl → cryptography
- UI polish round

## [0.1.113] - 2026-05-25

### Changed
- Settings split into a menu + 7 sub-pages (#328)

## [0.1.112] - 2026-05-25

### Fixed
- Power-flow icon tap returned 404 over the broker tunnel

## [0.1.111] - 2026-05-25

### Added
- Lifetime cycles + energy from shunt when BMS won't report (#295)

## [0.1.110] - 2026-05-24

### Added
- Energy chart, range buttons + weather overlay (#251)

## [0.1.109] - 2026-05-24

### Added
- Battery detail page, drill in from the SoC donut (#292)

### Changed
- Cleaner Leaflet maps, kill the in-tile attribution strip

## [0.1.108] - 2026-05-24

### Changed
- Map tiles: CARTO Dark Matter → CARTO Voyager

### Security
- Cloud-side at-rest backup encryption (#300 Tier 1)

## [0.1.107] - 2026-05-24

### Security
- Heartbeat signing (Phase 6B alternative, #308)

## [0.1.106] - 2026-05-24

### Security
- Wire signed-audit at security touchpoints (Phase 8B fanout, #310)

## [0.1.105] - 2026-05-24

### Security
- Cloud signs commands; appliance verifies (#299)

## [0.1.104] - 2026-05-24

### Fixed
- Sign out on cloud-broker session: explain + redirect to cloud sign-out

## [0.1.103] - 2026-05-24

### Added
- OS security patches surface (#280)

## [0.1.102] - 2026-05-24

### Security
- Appliance-side signed audit log + cloud sync (Phase 8B, #310)

## [0.1.101] - 2026-05-24

### Security
- Appliance mTLS client cert (Identity v2 Phase 6B-A, #308)

## [0.1.100] - 2026-05-24

### Security
- XSS escape DB-derived strings in device detail UI (#297-4)

## [0.1.99] - 2026-05-24

### Security
- Sign cloud backups with appliance keypair (#297-3)

## [0.1.98] - 2026-05-24

### Security
- Harden cloud restore against compromised cloud account (#297-1, #297-2)

## [0.1.97] - 2026-05-24

### Fixed
- Cloud sign-in survives appliance restart (#305 follow-up)

## [0.1.96] - 2026-05-24

### Added
- Dashboard battery health badge + honest empty states (#293, #294)

## [0.1.95] - 2026-05-24

### Added
- Sensors panel, Mopeka tanks + Govee/Ruuvi ambient (#257)

## [0.1.94] - 2026-05-24

### Fixed
- Identity v2 keypair survives Docker container recreate

## [0.1.93] - 2026-05-24

### Added
- Identity v2 Phase 3 + 4, LAN OIDC login (#305, #306)

## [0.1.92] - 2026-05-24

### Added
- Identity v2 Phase 1, appliance keypair foundation (#303)

## [0.1.91] - 2026-05-24

### Changed
- Battery node tone-down
- Cloud per-site card "History" → "Manage"

## [0.1.90] - 2026-05-24

### Added
- Battery node colour-codes direction more dramatically

### Fixed
- Battery node was grey when discharging (since v2)

## [0.1.89] - 2026-05-24

### Fixed
- SD-image pi-gen build (silently broken since v0.1.45)

## [0.1.88] - 2026-05-24

### Changed
- Power flow tile v3, visual overhaul

## [0.1.87] - 2026-05-23

### Added
- MQTT-IN: ingest external broker into the dashboard (#256)

## [0.1.86] - 2026-05-23

### Added
- Govee + Ruuvi ambient sensor drivers (#255)

## [0.1.85] - 2026-05-23

### Added
- Mopeka Pro / Pro Check tank-level driver (#254)

## [0.1.84] - 2026-05-23

### Added
- Cloud inbox auto-notify email (#246)

## [0.1.83] - 2026-05-23

### Added
- Cloud-orchestrated disk cleanup (#279)

## [0.1.82] - 2026-05-23

### Changed
- Update history paginates instead of dumping everything

## [0.1.81] - 2026-05-23

### Added
- Map / Satellite toggle on all map surfaces

## [0.1.80] - 2026-05-23

### Fixed
- Build-only: swap python base image to AWS Public ECR mirror

## [0.1.79] - 2026-05-23

### Changed
- Prettier map tiles, CartoDB Dark Matter

## [0.1.78] - 2026-05-23

### Added
- "Where you are" map tile on the appliance dashboard (#264)

## [0.1.77] - 2026-05-23

### Fixed
- Phantom rollback when duplicate update cmds queued (#283)

## [0.1.76] - 2026-05-23

### Added
- Fleet map + per-site location tile (#263) with opt-in privacy gate

## [0.1.75] - 2026-05-23

### Added
- Cloud device-health view (#267)

## [0.1.74] - 2026-05-23

### Fixed
- Backup tables overflowing the card on mobile

## [0.1.73] - 2026-05-23

### Added
- Rules trilogy, defaults, cloud transport, empty-state nudge

## [0.1.72] - 2026-05-23

### Fixed
- Don't show "Update to vX" after the user already clicked it (#275)

## [0.1.71] - 2026-05-23

### Fixed
- Don't snapshot twice when an update retries (#274)

## [0.1.70] - 2026-05-23

### Fixed
- wattpost-updater container-name collision (#273)

## [0.1.69] - 2026-05-23

### Changed
- Fleet bulk update now runs the full safety chain (#271)

## [0.1.68] - 2026-05-23

### Added
- Per-site update history + 1-click rollback (#272)

## [0.1.67] - 2026-05-23

### Added
- Auto-rollback for failed updates (#270)

## [0.1.66] - 2026-05-23

### Added
- Pre-update safety chain (#269)

## [0.1.65] - 2026-05-23

### Added
- Cloud "Update now" for Docker installs (#265)

## [0.1.64] - 2026-05-23

### Fixed
- Appliance alert rules now actually sync up to the cloud

## [0.1.63] - 2026-05-23

### Changed
- Stat strip is leaner

### Fixed
- Chart taps now show the value at the cursor

## [0.1.62] - 2026-05-22

### Added
- Energy data shipped to cloud (#252 slice 1)

## [0.1.61] - 2026-05-22

### Changed
- DCC combos now prong into two source nodes

### Fixed
- Renogy DCC50S/DCC30S, alternator side was showing 0 W

## [0.1.60] - 2026-05-22

### Changed
- Power-flow source colours distinguish DC-DC from AC, battery discharge is pink

## [0.1.59] - 2026-05-22

### Added
- Bidirectional rule sync, edit local rules from the cloud (#261 slice 2)

## [0.1.58] - 2026-05-22

### Added
- Heartbeat ships local alert rules to cloud (#261 slice 1A)

## [0.1.57] - 2026-05-22

### Fixed
- History chart legends came back

## [0.1.56] - 2026-05-22

### Fixed
- Placeholder legend on all History charts

## [0.1.55] - 2026-05-22

### Fixed
- Dark labels + uPlot legend placeholders

## [0.1.54] - 2026-05-22

### Fixed
- Broker Exit-kiosk button kept showing

## [0.1.53] - 2026-05-22

### Fixed
- Energy chart cleanup

## [0.1.52] - 2026-05-22

### Added
- Energy-today overview (top of /history)

## [0.1.51] - 2026-05-22

### Changed
- Power flow: Powerwall-style SVG diagram

## [0.1.50] - 2026-05-22

### Changed
- Power flow: battery centerpiece is now a SoC donut

## [0.1.49] - 2026-05-22

### Added
- "Battery full · solar throttled" caption

## [0.1.48] - 2026-05-22

### Changed
- Power flow gets a plain-English caption

## [0.1.47] - 2026-05-22

### Fixed
- Power flow connector amperage was misleading

## [0.1.46] - 2026-05-22

### Fixed
- Power flow summary line ignored the battery

## [0.1.45] - 2026-05-22

### Fixed
- SD-image build (pi-gen), broken since v0.1.32

## [0.1.44] - 2026-05-22

### Changed
- plain-English alert copy across every local transport (#249)

## [0.1.43] - 2026-05-22

### Fixed
- appliance PWA hints suppressed under cloud broker

## [0.1.42] - 2026-05-22

### Added
- BLE adapter "wedged" detection + auto-recovery (#244)

## [0.1.41] - 2026-05-22

### Fixed
- Victron BLE adverts dropped by orchestrator reopen loop

## [0.1.40] - 2026-05-22

### Fixed
- charger_state pill now reflects the bank, not whichever charger sorted first

## [0.1.39] - 2026-05-22

### Fixed
- stale charger state + phantom "Other source" tile

## [0.1.38] - 2026-05-21

### Changed
- appliance ships device snapshot in heartbeat extras

## [0.1.37] - 2026-05-21

### Changed
- appliance dashboard strips its chrome inside the WattPost mobile app

## [0.1.36] - 2026-05-21

### Fixed
- appliance dashboard respects device safe-area insets

## [0.1.35] - 2026-05-21

### Changed
- donut head telegraphs flow direction

## [0.1.34] - 2026-05-21

### Removed
- Tailscale integration

## [0.1.33] - 2026-05-21

### Added
- cloud error tracking via self-hosted GlitchTip

### Fixed
- phantom PV credit at sunrise
- cloud broker "Open" intermittent white screen
- kiosk-share modal stuck on "Loading…"
- cloud backup gate bypass on LIST endpoint
- stale UI shell after service-worker eviction

## [0.1.32] - 2026-05-20

### Added
- `header_prefix` in broker-auth diagnostics

### Fixed
- #225 dual-format broker-auth verifier

## [0.1.31] - 2026-05-20

### Added
- #36 Atomic-swap auto-apply updater

## [0.1.30] - 2026-05-20

### Fixed
- **`wattpost-update` was silently doing nothing on Pi installs**
- **`/api/system/update/apply` returned `Internal Server Error`** on Docker installs (or any host without `/usr/local/bin/wattpost-update`) because Litestar hides `HTTPException.detail` on 5xx. Changed to 400; users now see the actionable text ("Docker installs should run `docker compose pull && docker compose up -d`…") instead of a generic 500. The UI hides the button on Docker so this is an edge case, but curl users + broken-helper Pi installs now get a useful response.

## [0.1.29] - 2026-05-20

### Added
- #217 Anonymous local-install beacon

### Fixed
- **`/api/snapshot` 500 in demo mode**, `build_snapshot` accessed `self._poller._transports` directly, but the synthetic poller used in demo / dev installs has no such attribute. Defaults configured/open transport counts to 0 when the poller doesn't expose them. Found during the appliance smoke sweep.
- **Appliance 500s now log the traceback**, added an `after_exception` hook on the Litestar app so unhandled exceptions print the full stack to stdout instead of vanishing into a generic "500 Internal Server Error". Mirrors what cloud got in #194; the snapshot bug above is what made the gap obvious.
- **install.sh on non-Pi Debian/Ubuntu hosts**, the systemd unit declares `SupplementaryGroups=bluetooth`, which fails with `216/GROUP` and crash-loops the daemon on hosts where bluez hasn't created the group yet (notably Ubuntu Server cloud-init images). install.sh now creates the `bluetooth` group if it's missing, so the unit can always resolve it. Pi OS, which ships bluez, is unaffected. Found during a fresh-VM Phase F smoke.

## [0.1.28] - 2026-05-19

### Added
- #208 Admin oversight (release / billing / support actions)

## [0.1.27] - 2026-05-19

### Added
- #207 Cloud energy analytics + savings page

### Fixed
- Cloud alerts API was using `Appliance.owner_user_id` (the field doesn't exist, the correct column is `owner_id`). Would have 500'd every request to the inbox; corrected before any traffic hit it.

## [0.1.26] - 2026-05-19

### Added
- #206 Cloud alerts inbox (cross-site feed)

## [0.1.25] - 2026-05-19

### Added
- #201–#205 Tier 1 + Tier 2 driver batch
- `Section.function_code` (FC03 / FC04)
- `modbus.build_read_input`

## [0.1.24] - 2026-05-19

### Added
- #199 Setup wizard support for VE.Direct

## [0.1.23] - 2026-05-19

### Added
- #197 VE.Direct wired transport for Victron read

## [0.1.22] - 2026-05-19

### Added
- #163 followup, smart-plug output adapter

### Fixed
- v0.1.21 release notes called it "Renogy AC charger only"

## [0.1.21] - 2026-05-19

### Added
- #138 Reset-to-defaults for Docker parity
- #163 Solar-aware AC charger pause (Pro)
- `/api/snapshot`

### Fixed
- #162 Hero / Flow snapshot disagreement

## [0.1.20] - 2026-05-19

### Added
- #184 wizard hint for "BT-2 held by another LAN host"
- #158 BLE diagnostic endpoint
- #172 editable retention tiers + poll interval

## [0.1.19] - 2026-05-19

### Added
- #170 writable-settings fan-out (phase 3, Renogy DCC50S/30S)

## [0.1.18] - 2026-05-18

## [0.1.17] - 2026-05-18

## [0.1.16] - 2026-05-18

## [0.1.15] - 2026-05-18

## [0.1.14] - 2026-05-18

## [0.1.13] - 2026-05-18

## [0.1.11] - 2026-05-18

## [0.1.10] - 2026-05-18

## [0.1.9] - 2026-05-18

## [0.1.8] - 2026-05-18

## [0.1.7] - 2026-05-18

### Added
- "Take backup now" button on cloud appliance detail (#165)

## [0.1.6] - 2026-05-18

## [0.1.5] - 2026-05-18

## [0.1.4] - 2026-05-18

## [0.1.3] - 2026-05-18

## [0.1.2] - 2026-05-17

## [0.1.1] - 2026-05-17

## [0.1.0] - 2026-05-17

## [0.0.99] - 2026-05-17

## [0.0.98] - 2026-05-17

## [0.0.97] - 2026-05-17

## [0.0.96] - 2026-05-17

## [0.0.95] - 2026-05-17

## [0.0.94] - 2026-05-17

## [0.0.93] - 2026-05-17

## [0.0.92] - 2026-05-17

## [0.0.91] - 2026-05-17

## [0.0.90] - 2026-05-17

## [0.0.89] - 2026-05-17

## [0.0.88] - 2026-05-17

## [0.0.87] - 2026-05-17

## [0.0.86] - 2026-05-17

## [0.0.85] - 2026-05-17

## [0.0.81] - 2026-05-17

## [0.0.80] - 2026-05-17

## [0.0.79] - 2026-05-17

## [0.0.78] - 2026-05-17

## [0.0.77] - 2026-05-17

## [0.0.76] - 2026-05-17

## [0.0.75] - 2026-05-17

## [0.0.74] - 2026-05-17

## [0.0.73] - 2026-05-17

### Fixed
- "Idle" shown when slow-charging from PV

## [0.0.72] - 2026-05-17

## [0.0.71] - 2026-05-17

### Fixed
- "Remaining" tile showed instant rate, not realistic forecast

## [0.0.70] - 2026-05-17

## [0.0.69] - 2026-05-17

## [0.0.64] - 2026-05-17

## [0.0.63] - 2026-05-17

## [0.0.62] - 2026-05-17

## [0.0.61] - 2026-05-17

## [0.0.60] - 2026-05-17

## [0.0.59] - 2026-05-17

## [0.0.58] - 2026-05-17

## [0.0.57] - 2026-05-17

## [0.0.56] - 2026-05-17

## [0.0.55] - 2026-05-17

## [0.0.54] - 2026-05-16

## [0.0.53] - 2026-05-16

### Fixed
- 2FA enforcement could 403 /api/login itself

## [0.0.52] - 2026-05-16

### Fixed
- 2FA enforcement allowlist locked users out of enrolment

## [0.0.51] - 2026-05-16

## [0.0.50] - 2026-05-16

## [0.0.49] - 2026-05-16

## [0.0.48] - 2026-05-16

## [0.0.47] - 2026-05-16

### Security
- 2FA enrolment enforcement for staff accounts

## [0.0.46] - 2026-05-16

## [0.0.45] - 2026-05-16

## [0.0.44] - 2026-05-16

## [0.0.43] - 2026-05-16

## [0.0.42] - 2026-05-16

## [0.0.41] - 2026-05-16

## [0.0.40] - 2026-05-16

## [0.0.39] - 2026-05-16

## [0.0.38] - 2026-05-16

## [0.0.37] - 2026-05-16

## [0.0.36] - 2026-05-16

## [0.0.35] - 2026-05-16

## [0.0.34] - 2026-05-16

## [0.0.33] - 2026-05-16

## [0.0.32] - 2026-05-16

## [0.0.31] - 2026-05-16

## [0.0.30] - 2026-05-16

### Added
- "No-BMS" dashboard mode (#115)

## [0.0.29] - 2026-05-16

### Fixed
- Previously, the bank aggregator's shunt branch returned early and dropped `worst_pack_drift_v`, `cell_min_v`, `cell_max_v` from the snapshot when both a shunt and BMSes were present · meaning customers with a hybrid install lost the cell-balance panel data. The aggregator now keeps both layers independent.

## [0.0.28] - 2026-05-16

### Fixed
- **`pyproject.toml` pinned `victron-ble>=0.10`, which PyPI doesn't have** (the latest published version is `0.9.3`). Cached CI builds had been resolving through this since v0.0.13, but a clean dependency resolve failed the appliance + demo Docker builds. Relaxed to `victron-ble>=0.9`.

## [0.0.27] - 2026-05-16

### Changed
- CI build pipeline tuned so a long SD-image build doesn't block the faster Docker and source-tarball builds that fire on the same tag push.

## [0.0.26] - 2026-05-16

### Changed
- Restored the pi-gen trigger to all `v*` tags (we'd briefly restricted to `v<major>.<minor>.0` only as a minute-saver · no longer needed).

## [0.0.25] - 2026-05-16

### Notes
- VK-162 G-Mouse (£8 puck w/ magnetic base, 1 m USB cable) is the recommended receiver. Better satellite reception than a USB stick because the puck can sit on the van roof.
- Wizard support (the "GPS support coming soon" button currently shown after USB-scan detects an NMEA-emitting device) will be wired in a follow-up once a customer has end-to-end-tested the serial → fix → re-fetch path with real hardware.

## [0.0.24] - 2026-05-16

## [0.0.23] - 2026-05-16

### Fixed
- **Forecast form: Open-Meteo fields no longer leak in when Solcast is selected.** The `hidden` attribute was being emitted on the inactive provider's field group, but `.alerts-form-grid { display: grid }` was overriding the browser's default `[hidden]{display:none}` UA rule via specificity. Now Solcast users see only `api_key` + `resource_id`; Open-Meteo users see only `lat/lon/array_kw/ tilt/azimuth/efficiency`. Same fix for the per-provider help paragraphs below the form.

## [0.0.22] - 2026-05-16

## [0.0.21] - 2026-05-16

## [0.0.20] - 2026-05-16

## [0.0.19] - 2026-05-16

### Added
- **Renogy DCC50S / DCC30S driver (#123).** The DC-DC + MPPT combo charger that dominates mid-tier van builds. Single device with both an alternator input and a solar input. Speaks Modbus RTU over the existing BT-2 / USB-RS485 transports; just a new register map. Configure with `vendor: renogy, kind: dcdc`.
- Exposes alternator side (V / A / W), solar/PV side (V / A / W), battery side (V / A / SoC / temp), daily extremes (min/max V, max A, max charging W, today's Ah + Wh), lifetime totals, and per-bit alarm decoding. Charging state includes the `alternator_direct` value the DCC50S exposes (engine running, pure alternator feed) that the Rover doesn't have.
- Register map sourced from cyril/renogy-bt's `DCChargerClient.py`

## [0.0.18] - 2026-05-16

### Added
- **Victron Orion-Tr Smart DC-DC support (read-only).** Trivial follow-up to #112. Reuses the existing `ble_victron_advertise` transport, just registers a new driver under `device_kind: dcdc`. Exposes input voltage, output voltage, charging state (off/bulk/ abs/float/etc), charger error, off reason (e.g. ENGINE_SHUTDOWN so the dashboard can show "waiting for ignition"), and model name. Validated end-to-end against the victron-ble library's upstream Orion fixture.
- Models covered: Orion-Tr Smart 12/12-18, 12/12-30, 12/24-15, 24/12-30, 24/24-17. All share the same BLE Instant Readout protocol so a single driver covers the family.

## [0.0.17] - 2026-05-16

### Added
- **Open-Meteo PV forecast provider**. Free, unlimited, lat/lon- based PV forecast that doesn't require a Solcast account. Solar irradiance from Open-Meteo is combined with the user's array geometry (capacity_kW, tilt, azimuth, system_efficiency) via a simple solar-position + tilt-cosine model to estimate PV output hourly for 7 days. Validated end-to-end against a real UK location. Physically sensible peak watts + day totals.
- **Settings → Integrations → PV forecast** form now has a provider dropdown: pick "Solcast (site-trained ML)" for fixed- roof installs with a registered account, or "Open-Meteo (irradiance estimate)" for moving vans / no-account installs. Each provider shows only its own field set; the picker swaps them. Lat/lon left blank inherits from the weather integration's location.
- **Why this matters**: Solcast is fundamentally site-based (free tier = 10 calls/day, max 2 sites, no API to register sites). A non-starter for moving vans + a real barrier-to- entry for casual users. Open-Meteo doesn't have any of those limits. We're already calling Open-Meteo for current weather; the irradiance endpoint is one more parameter.

### Notes
- Forecast accuracy hierarchy: Solcast best for fixed roofs (site- trained ML), Open-Meteo good enough for "should I drive south tomorrow" + as a no-config default. Future ticket #125 (USB GPS) auto-switches to Open-Meteo when location drift is detected.
- Today / Tomorrow tile sub-line now credits whichever provider is configured rather than hard-coding "Solcast".

## [0.0.16] - 2026-05-16

### Added
- **USB-scan now classifies each device by protocol.** The wizard's wired-adapter list opens each `/dev/ttyUSB*` / `/dev/ttyACM*`, reads briefly, and tags it as:
- `Modbus`. Silent serial (the typical case) · "Use as Modbus" button
- `NMEA GPS`. Emitted `$GP…` / `$GN…` sentences (preparation for #125 USB GPS support; button disabled with "coming soon" hint)
- `unknown output`. Bytes seen but no recognised pattern
- `port busy`. Already held by another process
- Stops users accidentally adding a GPS receiver as a Modbus transport. A £8 VK-162 G-Mouse GPS would otherwise show up alongside legitimate RS-485 adapters and silently fail every poll after pairing.

### Notes
- Detection is read-only (no Modbus probe write at scan time). The existing `/api/setup/probe` endpoint does an active slave-ID sweep once a Modbus transport is selected. That's where real device confirmation happens.

## [0.0.15] - 2026-05-16

### Added
- **"Add another transport" in the setup wizard.** Once you've got a transport configured, a collapsible tile under the list lets you wire up a second one without deleting the first. Same two buttons (Bluetooth / Wired USB-RS485) as the empty-state picker. Pairs cleanly with the underlying architecture · BLE and USB serial subsystems are completely independent on the Pi, so a single host can run a Renogy BT-2 for the MPPT

## [0.0.14] - 2026-05-16

### Added
- **Setup wizard now also finds USB-RS485 adapters.** Phase 1 of the unified-wizard work (#120). The "no transports configured" empty state now has two paths: "Bluetooth (e.g. Renogy BT-2)" (existing) and "Wired (USB-RS485 adapter)" (new). The wired path enumerates every `/dev/ttyUSB*` / `/dev/ttyACM*` the host sees, labels each with the chip (FTDI FT232 / WCH CH340 / Prolific PL2303 / Silicon Labs CP210x), and the user picks one with a single tap. Add-transport writes a `serial_modbus` block with sensible defaults (9600 baud, 8N1. Renogy/Epever standard).
- **Why this matters**: replacing the BT-2 dongle with a wired USB-RS485 dongle (~£10) gives sub-millisecond round-trips, no BLE timeouts, and proper FC06 ack frames (fixing the silent-ack quirk we hit during #104 de-risk). It also opens the door for customers who don't have line-of-sight BLE to their kit. Cabin installs, gear in a metal-roof barn, etc.

### Notes
- Phase 1B of #120 (Victron / JK BMS pattern-specific forms in the wizard) lands in a follow-up. The current Victron driver (v0.0.13) still needs manual YAML config; #118 tracks that gap.
- See the wizard's new tooltip: the RJ45 port on chargers is

## [0.0.13] - 2026-05-16

### Added
- **Victron SmartShunt support (read-only).** The BMV-style battery monitor. Voltage, current, SoC, time-to-go, consumed Ah, aux input (starter/midpoint/temperature), model + alarm state. Now lights up the same dashboard tiles as our other vendors. New BLE transport `ble_victron_advertise` runs a passive BleakScanner that decrypts Victron's Instant Readout advertisements via the per-device key (find it in VictronConnect → Product info → Show device key). New vendor `victron` with driver `shunt`. Validated end-to-end against the `victron-ble` library's upstream test fixtures. Every field decodes correctly.
- Adds `victron-ble>=0.10` as a dependency.

## [0.0.12] - 2026-05-16

### Added
- **Renogy MPPT load-output toggle.** Rover-family chargers (Rover / Wanderer / Adventurer / Voyager) now expose their 12 V load terminal as a controllable output on the device-detail page. Toggle button writes register 0x010A via FC06 and confirms the new state via an explicit FC03 read-back inside the same BLE session. Works around the BT-2 dongle quirk where Rover firmware 3.x silently swallows FC06 ack frames. Confirmed end-to-end against a real RNG-CTRL-RVR40 FW 3.1.0.
- **One-shot safety gate** before the first toggle on any output: the panel explains what's about to happen ("write command to your charger, the load terminal will switch") and the user has to acknowledge before any control surface appears. Persisted per-output. Won't nag on every visit.
- **Audit line** under each control: "Last command: on · 6 sec ago · by user · ok". So you can see whether a command actually landed, especially handy when BLE was wobbly.
- **Generic `ControllableOutput` schema + adapter protocol** under `solar_monitor/outputs/`. JK BMS charge/discharge MOS toggles drop into the same UI + (forthcoming) schedule engine without per-vendor UI work. Today's adapter is `renogy_rover`; more arrive with #114 (JK BMS).

## [0.0.11] - 2026-05-16

### Added
- **Right-now tile now shows the next 8 hours.** Apple-Weather- style hourly strip at the bottom of the panel: HH:00 label, a tiny WMO icon (sun / partly cloudy / cloud / rain / snow / thunder, switched to a moon for night-time clear skies), and the predicted °C for each cell. Pulled from the same Open-Meteo fetch as the current conditions. One extra HTTP param, no new provider. So refresh cadence + auth-free setup is unchanged. The strip is hidden if the provider doesn't return hourly data, and scrolls horizontally on narrow viewports rather than wrapping.

## [0.0.10] - 2026-05-16

### Changed
- **Dashboard tile redesign. Today is now the headline.** The standalone "Tomorrow" tile is gone; its content folds into the Today panel as a sub-line. The Today panel now shows kWh-so-far as a big hero number, with a forecast sparkline running across the day (solid for the past, dashed for "still to come") and a faint "now" marker. The sub-line tells you what was expected and what's still to come ("Of 3.8 kWh expected · 1.4 kWh still to come"). The Tomorrow preview drops to a one-line footer at the bottom of the tile.
- **Sunset flip.** After dusk. When no PV is forecast for the rest of today and tomorrow's window has data. The Today tile auto-flips: Tomorrow's expected kWh becomes the headline, today's tally demotes to "Today (final): PV … Load …" in the footer. The dashboard's "operational moment" stays unambiguous all day.
- **5-day outlook highlights Today**, not Tomorrow. Matches the headline tile so the "you are here" anchor reads consistently top-to-bottom.

### Fixed
- **"Right now" weather tile gets its background tint back.** Was the only dashboard tile without a panel tint, which made it visually inert next to the others.

## [0.0.9] - 2026-05-16

### Fixed
- **Bumped the `/web/app.js?v=` cache-buster** in index.html. Several recent appliance fixes (Settings → About row visibility, history chart forecast bound, Check-now button focus state) were sitting unread in the container because the script-tag's version query hadn't moved since v0.0.5. Cloudflare's edge was serving the same URL out of its 4h cache regardless of what the container actually held. From now on the index.html `?v=` must move in lockstep with `sw.js` CACHE_VERSION so every JS update gets a fresh URL that bypasses any CDN cache.

## [0.0.8] - 2026-05-16

### Fixed
- **History chart: the forecast overlay no longer stretches the x-axis past the selected range.** Picking "6h" used to render a week-wide axis because Solcast's full 7-day forecast was appended. The forecast horizon now mirrors the chosen history window (1h history → 1h forecast, 24h → 24h, etc).
- **"Check now" button on Settings → About stops looking pressed** after the action completes. It was the iOS focus ring sticking; we now blur the button when the work returns.
- **iOS Safari picks up appliance updates faster.** Service worker registration now uses `updateViaCache: 'none'`, so Safari fetches the SW file fresh on every page load instead of holding the previous version's cached copy for hours.

## [0.0.7] - 2026-05-15

### Fixed
- **Settings → About uptime now reports the daemon's uptime**, not the host's. The previous `/proc/uptime` read leaked the host machine's uptime through Docker. A freshly-restarted container could show "3d 23h" if that's how long the laptop had been booted.
- **"Updates: docker compose pull..." row only shows when there's actually an update pending.** Used to render permanently on every Docker install, even with nothing to apply. Read as a nag.
- **Fresh installs land in the setup wizard automatically.** First-time users opening the dashboard with zero transports configured used to see an empty dashboard with a "Setup needed" pill top-right and no signpost. They now get redirected straight to `#/setup` on first paint.

## [0.0.6] - 2026-05-15

### Added
- **Cloud dashboard shows weather + PV forecast per site.** Each heartbeat now ships the appliance's cached weather snapshot (temperature, conditions, sunset) and Solcast forecast totals (today + tomorrow kWh). The cloud card surfaces a quiet strip. E.g. *"☀ 16°C · Mostly clear · Sunset 19:42 · Today 4.2 kWh PV · Tomorrow 5.1 kWh"*. So a glance at app.wattpost.io tells you whether your off-grid setup is going to make it through the day.
- **Appliance reports its install method** in the heartbeat (`pi` vs `docker`). The cloud uses this to hide the "Update now" button on Docker installs (where the action has to happen on the host via `docker compose pull`).

### Changed
- Update notes pulled from `releases.wattpost.io/CHANGELOG.md` remain the same source as before. This entry will appear in the dashboard's "Release notes" link.

## [0.0.5] - 2026-05-15

### Changed
- **Setup wizard BLE scan now flags "recently visible but missing" dongles**. When a BT-2 was seen in the last 15 min but doesn't respond to the active scan, the wizard surfaces it with a likely-cause hint (most commonly: the Renogy mobile app is holding the connection). Replaces the previous silent empty-list outcome.
- **Docker installs no longer show Tailscale UI rows.** Tailscale doesn't run inside the container, so the rows were dead toggles. Pi installs are unchanged.

### Fixed
- **iOS Safari text inflation** on `/docs` made body text ~2× nav text. Pinned with `text-size-adjust: 100%`.
- **Docs grid overflowed viewport** on narrow mobile screens · wide tables now horizontally scroll, grid cells respect viewport width.

## [0.0.4] - 2026-05-15

### Added
- **Cloud-managed updates for Pi appliances.** Multi-site dashboard renders an amber "v0.0.x → v0.0.y" pill on cards running behind `releases.LATEST` and a one-click "Update to v0.0.y" button that queues the action; the appliance picks it up on its next heartbeat, runs `wattpost-update`, and the cloud auto-reconciles the command to "success" when the new version reports in.
- **Stripe billing (v1).** $5 per appliance per month, 14-day free trial, Stripe-managed grace period. Subscribe / Manage billing buttons in the cloud dashboard and account page. Webhook ingestion mirrors subscription state into the `appliances` table.
- **Per-device delete button on the appliance Devices tab** · one click + confirm to drop a slave from polling, no trip through the Setup wizard.
- **Bank aggregate pinned to the top of the Devices tab.** Previously filtered out; now the headline reading sits where users look first.
- **Live-streaming Setup → Scan.** The wizard now shows "Probed N of 17 · X responded" with results streaming in as each slave answers, instead of staring at a spinner for ~60s while the full sweep finishes.
- **SQLite migration framework on the appliance.** Future schema changes evolve existing databases via PRAGMA user_version instead of breaking customers.
- **`cloudflared` bundled in the Docker image.** The paired- cloud "Open site" tunnel now works identically on Pi and Docker installs.

### Changed
- **BLE auto-detects the notify characteristic** per BT-2 firmware generation (`fff1` first, `ffd2` fallback). Setup wizard's scan finds Renogy devices reliably across both generations without manual config.
- **BLE self-heals stale BlueZ state** on connect failures · the daemon now sends `bluetoothctl disconnect` + `remove` on the retry path, recovering from "device disconnected during service discovery" without operator intervention.
- **Bank aggregate is now stable across single-pack poll drops.** Previously recomputed from "what answered this cycle"; now augmented with cached snapshots from any pack last seen within 5 min. `pack_count` no longer flips between 3 and 2 on a noisy BLE link.
- **Cross-subdomain session.** Cookie scoped to `.wattpost.io` so a logged-in user clicking "Download" or visiting `wattpost.io/docs` stays signed in instead of apparently logging out.
- **Hot-reload wizard writes.** add_device / add_transport / add_forecast / add_weather + their deletes all now return in <10ms with a background hot-reload, instead of awaiting the ~5s scheduler swap. No more "saving…" hangs during scans.
- **Update notes now fetched live** from `releases.wattpost.io/CHANGELOG.md`, so the in-app "Release notes →" link previews entries for a version the user hasn't installed yet.
- **Marketing + docs theme defaults to system** preference, not hardcoded dark.

### Fixed
- **"Setup needed" pill stuck on amber** despite a healthy appliance. SSE snapshot's `poll_run` was missing the `transports` field, so every tick reset the dashboard's view to "no transports configured".
- **Pairing flow re-introduced "Restart daemon"** UX after the hot-start path was added; UI now respects the `restart_required: false` response.
- **About → Update section** showed "Latest available ·" and a stuck "Update progress: waiting…" on Docker after earlier manual Update-Now clicks. Both rows now hide when there's nothing to apply.
- **Setup wizard locked users out** when the BLE link was idle-dropped. The transport row went disabled with no recovery. Now: row stays clickable, scan auto-reopens the link.

## [0.0.3] - 2026-05-15

### Added
- BLE transport auto-detects the notify characteristic per Renogy BT-1 / BT-2 firmware revision (`ffd2` on newer modules, `fff1` on older ones). Setup wizard's Scan step now works against both generations without manual config.
- About → Update surfaces the new version number and a "Release notes →" link to the in-app `#/docs/release-notes` page whenever an update is available, so users can see what changed before deciding to apply.
- Release notes are now fetched live from `releases.wattpost.io` on every manifest poll and cached on the appliance. Means the dashboard can preview a not-yet-installed version's changelog entry. Bundled docs only cover versions ≤ the running release. Falls back to bundled `docs/release-notes.md` when offline.

### Changed
- Settings → About: Docker installs no longer show an in-app "Update now" button. They get a persistent hint to run `docker compose pull && docker compose up -d` on the host (matches Immich / Pi-hole / Vaultwarden conventions). Pi installs are unchanged.

## [0.0.2] - 2026-05-15

### Added
- WattPost cloud (wattpost.io). Opt-in. Pair the appliance to a cloud account from Settings → Integrations → WattPost cloud, paste an 8-character code, daemon exchanges it for a long-lived bearer token and starts pushing 5-minute heartbeats. Cloud's multi-site dashboard shows online/offline per appliance and flags overdue heartbeats. Local appliance keeps working with no internet, no cloud, no account. Strictly additive.
- Solcast PV forecast: configurable in Settings → Integrations (user supplies their own free API key + resource UUID), polled every 3h by default, overlaid as a dashed line on the History chart when viewing pv_power_w. New SQLite `kv` table caches the most recent fetch so a daemon restart doesn't blank the overlay.
- Dashboard "Tomorrow" tile: when a forecast is configured, a new panel between Today and Cell balance shows expected PV (kWh), peak power + time, day-after preview, and a translucent SVG sparkline of tomorrow's curve. Auto-hidden when no forecast data is cached.
- Dashboard "7-day outlook" strip below the Tomorrow tile · per-day kWh + mini sparkline across all forecast days, common Y scale so quiet days read as quiet next to sunny ones, the Tomorrow card highlighted as the focal point.
- History chart's forecast overlay now renders Solcast's P10–P90 confidence band as a translucent amber fill between the bounds. Wide band = the model isn't sure, narrow = high confidence. Median line stays dashed on top.
- Current weather (Open-Meteo): new `weather/` module + Settings → Integrations row + dashboard "Right now" tile showing temp, conditions icon (WMO code → SVG), cloud cover, wind, humidity, sunrise / sunset. No API key required; user supplies lat/lon. Polls every 15 min by default, cached in the same `kv` table the PV forecast uses.
- Forecast accuracy: every Solcast fetch is now archived to a new `forecast_history` table (30-day retention). The Tomorrow tile grows a "Yesterday: predicted X · actual Y · Z% of forecast" line tinted green / amber / red by deviation. Surfaces drift in Solcast's site model (capacity, tilt, azimuth, shading) before it gets too far from reality.
- Charge efficiency: `/api/devices/{label}/efficiency` returns SoC-corrected coulombic efficiency over 7d / 30d / 90d / lifetime windows. Smart-battery device cards show an `η` tile picking the shortest reliable window; the device detail page shows the full 4-window breakdown with greyed-out cells for windows that haven't seen enough cycling.
- History chart: "Compare packs" toggle overlays every smart_battery pack's metric on one chart. Auto-disabled when fewer than two packs are configured or when a non-battery device is selected.
- Quiet hours: `config.yaml` accepts a `quiet_hours: {start_hour, end_hour}` block. Inside the window, warn-severity alerts buffer and flush when the window ends; alarm-severity always pages through. Settings → Alerts now has a UI editor for the same setting (`PUT /api/alerts/quiet_hours`) so it isn't YAML-only.
- CI: `.github/workflows/build-image.yml` builds the SD image via pi-gen on tag push (`v*`) and attaches the `.img.xz` + SHA256 to a GitHub Release. `workflow_dispatch` available for smoke tests.
- Local alert engine. Rule schema (metric / op / threshold / severity / cooldown), Settings → Alerts UI editor (rules + transports), per-rule Test button
- Notification transports: ntfy, Discord webhook, generic webhook, SMTP / email, MQTT-publish (LAN-local), Pushover
- CSV export of any metric over any range (`/api/devices/{label}/history.csv`)
- PWA install. Manifest + service worker, dashboard installs to home screen on iOS / Android
- Tailscale auto-config. Sudoers entry, `tailscale serve` for HTTPS, Settings → System surfaces the auth URL
- In-app docs (`/docs/...`) rendered from bundled Markdown. No external site needed
- Diagnostics. Settings → System shows recent log lines + a Restart daemon button (no SSH required)
- Kiosk mode · `#/kiosk` chrome-free SoC + flow tiles, Settings toggle to default-on for one device, Wake Lock keeps the screen on
- WebSocket / SSE live updates. Dashboard streams snapshots after every poll instead of polling every 5s
- BLE discovery wizard. Setup page scans an open transport for new slave IDs and appends them to config.yaml
- Home Assistant MQTT discovery topics
- packaging/install.sh + systemd unit + pi-gen stage for SD-image builds

### Changed
- BLE transport now auto-recovers from "device not advertising" timeouts on daemon restart by clearing BlueZ's stale connection state (`bluetoothctl disconnect`) and retrying once
- Tailscale endpoints surface real sudo errors to the UI instead of returning `ok:true` and only logging. Enable HTTPS / Connect / Disconnect now show a username-aware fix-it hint (`packaging/dev-sudoers.sh` for dev shells, re-run `install.sh` for production `wattpost` user)

## [0.0.1] - 2026-05-12

### Added
- Pluggable Transport abstraction (BLE Modbus, RS-485 Modbus stub)
- Pluggable Vendor / DeviceDriver abstraction with central registry
- Renogy vendor: Rover charge controller + LFP smart battery drivers, parsers ported in (no upstream dependency on `cyrils/renogy-bt`)
- Modbus RTU framing helpers (CRC16, frame builders, response verify)
- YAML config + `solar-monitor poll` CLI
- Long-lived `Poller` that holds transports open across the daemon's lifetime (5× faster than reopen-per-poll)
- SQLite storage (WAL mode) with raw `samples` + 1-min / 1-hour / 1-day rollup tables and a background retention/rollup task
- `PollScheduler` background asyncio task with exponential backoff on failure, exporter dispatch, and clean shutdown
- Litestar HTTP API:
- `/api/health`
- `/api/devices` + `/api/devices/{label}/latest`
- `/api/devices/{label}/history` (range-aware table selection)
- `/api/poll_run`
- `/api/today` (energy-balance aggregates: PV today, bank charged/discharged today, **real load today**)
- Static SPA served from same Litestar app (no toolchain):
- SoC donut with state-banded color, animated charge/discharge pulse (CW = charging, CCW = discharging), drop-shadow glow following direction, signed-W indicator pill
- Hero with net power, time-to-empty/full, voltage, capacity, bank info
- **Power flow strip**. Data-driven from device kinds. Sources → Battery → Loads, animated arrows, energy-balance "Load" tile that captures bus-wired consumption invisible to the charge controller
- Today strip (PV / charged Ah / peak / **real load** / lifetime)
- Cell-balance panel with per-cell chips, min/max highlight, panel hue follows drift severity
- History chart (uPlot, vendored offline) routing to the right rollup table by range
- Device detail cards with kind icons + firmware + serial
- Section header icons, status pill icon (✓ / ⚠ / ✗)
- Conditional alert banner. Hidden when healthy; surfaces low SoC, cell drift, over-temperature, comms loss, transport errors
- MQTT exporter (aiomqtt-based). Full device snapshots + per-metric topics, retained, with LWT `_status` topic for online/offline
- Tailscale-friendly: serves on 0.0.0.0, no TLS required for LAN, no cloud touched
- Firmware + serial decoded from registers and surfaced in device cards + MQTT + API
- Per-panel color hues (hero follows SoC band, cell-balance follows drift, power-flow has source→storage tint gradient)

### Notes
- Bank current rounds at 0.01 A per pack. Small trickle currents (< ~0.5 A on a single 100 Ah pack) show as zero. Not a bug, a BMS resolution limit.
- Renogy load output (`load_power_w`) is intentionally not used as the primary load number; bus-wired loads (the common case) need the energy-balance approach.
- The 32-ish watts of "Other loads" you see when nothing's running is real phantom draw (inverter standby, BMS overhead × 3 packs, Hub + MPPT self-consumption). Most apps hide this. We don't.
