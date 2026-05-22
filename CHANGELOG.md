# Changelog

All notable changes to solar-monitor. Format: [Keep a Changelog].
Versions follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

## [0.1.55] · 2026-05-22

### Fixed · Dark labels + uPlot legend placeholders

Two small things from the broker / mobile view:

- All references to `var(--text-1)` across `styles.css` were
  resolving to `unset` (the real variable is `--text`). The
  Power-flow node W labels and a handful of other accents
  rendered in a dark colour against the dark surface, hard to
  read. Replaced every `--text-1` with `--text` (also fixes
  half a dozen pre-existing dark-text spots dating from May 16
  that nobody had flagged).
- The Energy chart's built-in uPlot legend was showing
  permanent `·` placeholders on touch devices (no hover, so
  the live legend never updates). Hidden — the static
  colour-chip legend below the chart already labels every
  series.

## [0.1.54] · 2026-05-22

### Fixed · Broker Exit-kiosk button kept showing

The `.kiosk-exit` author CSS had equal specificity to the UA
`[hidden]` declaration and won by source order, so the JS's
`hidden = true` for broker / kiosk-share visitors was silently
ignored — Exit button kept rendering top-right on
`<slug>.wattpost.cloud`. Added a `.kiosk-exit[hidden]` rule with
`!important` to honour the attribute.

## [0.1.53] · 2026-05-22

### Fixed · Energy chart cleanup

Two readability bugs in v0.1.52's Energy-today chart:

- SoC line drew a misleading drop-to-zero when a poll bucket missed.
  Now treat any SoC ≤ 0 as null so the line shows a gap instead
  (0% is physically impossible — BMS would have cut off long
  before).
- Load (purple) area was vanishing behind Solar / AC-charger areas
  on heavy-source days. Switched Load from filled area to a
  thicker line drawn on top — clearly legible against everything.

## [0.1.52] · 2026-05-22

### Added · Energy-today overview (top of /history)

Powerwall-Dashboard-inspired overview replacing the History page's
front. Stacked-area chart showing solar / load / into-battery /
out-of-battery as signed kW over the local calendar day, with SoC
overlaid on a right-side % axis. Below: four kWh totals (solar,
load, into battery, out of battery) and a self-powered breakdown
bar (what % of today's load came from solar vs battery vs charger).

The existing per-metric line chart (with device + metric selector,
range buttons, CSV export) is preserved as "Detailed metrics" below.
Load-profile heatmap stays below that.

Backed by a new `/api/energy/today` endpoint that returns all five
series aligned to one shared `ts` grid + pre-computed kWh totals
in a single request. Buckets at 5 min for the default day window.

Slice 2 follow-ups: weather overlay (temp + cloud cover lines on
the right axis), range buttons (1h/6h/24h/7d/30d) tied to this
chart, and animated draw-in on poll updates.

## [0.1.51] · 2026-05-22

### Changed · Power flow: Powerwall-style SVG diagram

Replaces v0.1.50's central donut with a Tesla / Powerwall-Dashboard
inspired layout:

- Icon-only nodes at the perimeter — sun for solar, plug for AC
  charger, house for load, battery for the bank when active
- Watts as labels *outside* each node (not crammed inside)
- Curved bezier connectors with animated particles flowing along
  them in real time, particle speed scales with W
- Battery sits in a slim card below the diagram: big SoC %,
  state label (Full / Charging / Discharging / Resting / Low),
  signed W with direction arrow, slim horizontal fill bar
- Silent / zero-W sources go grey, no particles
- Battery joins the diagram as a node only when its flow is ≥10 W
  (small float trickles stay in the card to keep the diagram clean)
- Respects prefers-reduced-motion (replaces SMIL particles with a
  dashed line so direction is still implied)

## [0.1.50] · 2026-05-22

### Changed · Power flow: battery centerpiece is now a SoC donut

The Power-flow tile's central battery rectangle is replaced with a
SoC donut, matching the Hero donut's visual language. The donut
shows:

- Percentage SoC big in the centre
- State label underneath (Full / Charging / Discharging / Resting / Low)
- Direction arrow + magnitude inside the donut (↓ X W in when charging,
  ↑ X W out when discharging) — battery-relative, never bus-relative
- Bus voltage + shunt amperage as a small sub-line
- Arc colour-coded by state (charging green, holding blue, discharging
  amber, critical red)

The eye now lands on the SoC + direction first instead of a
`−5 W` figure that needed a translation. Sources and loads stay
as flanking tiles, plain-English caption stays below.

## [0.1.49] · 2026-05-22

### Added · "Battery full · solar throttled" caption

When the bank is at ≥98% SoC and the MPPT has dropped into float
mode (only pulling enough sun to cover load + bus maintenance),
the panel output looks artificially low — "three panels on a sunny
day and only 94 W?". The power-flow caption now calls this out
directly:

> Battery full · solar throttled to load demand (94 W, panels not maxed)

so people don't go hunting for a fault that isn't there.

## [0.1.48] · 2026-05-22

### Changed · Power flow gets a plain-English caption

The diagram was technically correct but kept producing "wait, why
do the numbers not match?" moments — e.g. solar pushing 94 W, load
pulling 99 W, battery at 100% but quietly trickling 5 W into the
gap. The numbers DO reconcile, but you had to know where to look.

Under the diagram there's now a single line in plain English:

- "Sources covering load · charging battery at 40 W"
- "Load is 5 W more than sources · battery making up the difference"
- "Running off battery · 99 W to load"
- "Sources matched to load · battery resting"

The v0.1.46 "battery N W in/out" pill in the sub-header is dropped
— it was bus-perspective wording that read backwards from how you
think about it.

## [0.1.47] · 2026-05-22

### Fixed · Power flow connector amperage was misleading

The connectors between sources / battery / loads were labelled with
their bus-equivalent amperage (e.g. 94 W solar → "6.6 A"). Because
the top connector visually terminates at the battery tile, that
amperage read as "6.6 A flowing into the battery" — even when the
bank's own shunt was reporting only ±0.3 A. When the bank is
present we now drop the connector amperage and leave the watts on
their own; the bank tile remains the source of truth for shunt
current. Without a bank the connectors still show A (no other place
to put it).

## [0.1.46] · 2026-05-22

### Fixed · Power flow summary line ignored the battery

The "N sources · X W in · M loads · Y W out" header could appear
off-balance when solar didn't quite cover the load and the battery
was making up the difference — e.g. "94 W in · 99 W out" with no
hint that the missing 5 W came from the bank. The header now adds
a `battery N W in/out` pill whenever the bank contribution is
≥ 1 W, so the totals reconcile.

## [0.1.45] · 2026-05-22

### Fixed · SD-image build (pi-gen) — broken since v0.1.32

Every tagged SD-image build since v0.1.32 (the slot-directory
refactor, #219) failed in the pi-gen chroot at the very last
step with

```
ERROR: Invalid requirement: '/opt/wattpost-src'
Hint: It looks like a path. File '/opt/wattpost-src' does not exist.
```

Root cause was in `packaging/install.sh`: the migration block
moves `/opt/wattpost-src` → `/opt/wattpost-slots/a/src/`, but
when invoked from pi-gen (which sets `WATTPOST_SOURCE=
/opt/wattpost-src`) the `SOURCE` variable still pointed at the
now-moved legacy path. The final `pip install ${SOURCE}` then
errored looking for the directory we'd just relocated.

Fix is to refresh `SOURCE` inside the migration block when it
matches `LEGACY_SRC`. Docker and curl-bash installs are
unaffected (their `SOURCE` defaults to `REPO_ROOT`, never to
the legacy path).

Customer impact: `/download` on wattpost.cloud had been serving
the v0.1.31 SD image since 21 April. Anyone who fresh-installed
in that window had to run `wattpost-update` on first boot to
catch up. v0.1.45's SD image will be the first new one in
a month.

## [0.1.44] · 2026-05-22

### Changed · plain-English alert copy across every local transport (#249)

Same fix shape we applied cloud-side, now on the appliance.
Previously every local-alert transport (ntfy, Discord, Pushover,
SMTP-from-the-Pi) emitted strings like

  `bank.soc_pct = 18.50 (lt threshold 20.00)`

which reads as a debug printout to the user. Now they're
rendered through a single set of helpers in
`solar_monitor/alerts/base.py`:

  `State of charge is 18.5% (threshold < 20%)`

— with units inferred per metric (% / V / W / A / °C / min),
operator words humanised (< / > / ≤ / ≥), and per-metric
rounding (SoC 1dp, voltage 2dp, watts integer).

The SMTP local-alert email also gets a better subject:
`WattPost warn: state of charge 18.5% (Low battery)` — leads
with the metric + current value so a phone preview answers
"which?" + "how bad?" without opening.

Machine-format transports (MQTT, webhook) keep raw JSON
unchanged — downstream integrations render their own way.

## [0.1.43] · 2026-05-22

### Fixed · appliance PWA hints suppressed under cloud broker

When the appliance's HTML was served via the cloud broker
(`<slug>.wattpost.cloud/`), the page advertised `manifest.web-
manifest` + apple-touch-icon + apple-mobile-web-app meta tags
and registered its own service worker. A user who "Add to Home
Screen"d while viewing the broker view ended up with a PWA
scoped to that single broker subdomain — push notifications
register against the page's origin, so cloud-delivered alerts
(sent from `wattpost.cloud`) never arrived, and there was no
multi-site picker or alerts inbox inside the PWA.

The canonical install target is `wattpost.cloud/app` — that
PWA's start_url is the fleet dashboard, push registers at the
SaaS origin so alerts from any paired appliance fire, and the
alerts inbox + account live inside the same install.

Now: when `location.hostname.endsWith('.wattpost.cloud')` (i.e.
served via the broker), an early head-script strips the manifest
link, apple-touch-icon, apple-mobile-web-app meta tags, and
sets a flag that blocks SW registration further down the page.
LAN access (192.168.x.x, .local, etc.) still installs the
appliance PWA — that's still valid for the offline-first /
no-cloud user.

## [0.1.42] · 2026-05-22

### Added · BLE adapter "wedged" detection + auto-recovery (#244)

Follow-up to v0.1.41's orchestrator-reopen-loop fix. Two adjacent
gaps closed:

**Orchestrator retries failed transport opens.** If a transport's
`open()` raises (e.g. `org.bluez.Error.InProgress` after a USB
state hiccup), the orchestrator now schedules a retry with
exponential backoff (5s → 10s → 20s → 40s → 80s → 160s → cap
at 5 min). Previously a one-time open() failure stranded the
transport until container restart. The Garage Stack VM hit this
exact case post-USB-reset and would otherwise have stayed dark
for hours.

**BLE-adapter-wedged surfacing.** The shared Victron scanner now
tracks "did we receive ANY advertisement since scan-start" (not
just Victron payloads — any advert proves the dongle is delivering
data). After 30s of zero callbacks the adapter is flagged
`wedged`. Heartbeat extras carries the state field
(`ble_adapter_state ∈ {ok, warming, wedged, idle}`); the cloud's
per-site dashboard renders a red banner explaining the situation
and how to recover ("Unplug the USB Bluetooth dongle, wait 10s,
plug it back in.") instead of showing every Victron device
independently going silent.

### Recovery note for Realtek dongle users

If you've been running v0.1.40 or earlier with a Realtek-based
BLE dongle (the popular TP-Link UB500, ASUS USB-BT500, and most
sub-£15 dongles use the RTL8761B), the pre-v0.1.41 reopen loop
was firing thousands of `HCI_LE_Set_Scan_Enable` cycles per day
against firmware that handles that poorly. Symptom: Victron
devices show "Silent" or stale-data even after pulling
v0.1.41/0.1.42.

**Fix once:** unplug the USB Bluetooth dongle from the appliance,
wait 10 seconds, plug it back in. Soft resets (`systemctl restart
bluetooth`, container restart, even VM reboots) often don't fully
clear Realtek firmware state — a physical power-cycle does.

## [0.1.41] · 2026-05-22

### Fixed · Victron BLE adverts dropped by orchestrator reopen loop

The orchestrator's transport-liveness check assumed every transport
exposed a GATT-style `_client` attribute with `is_connected`. The
passive BLE-advertise listeners that drive Victron Instant Readout
(IP22, SmartShunt, SmartSolar, etc.) deliberately don't — they
subscribe to a shared BlueZ scanner. So:

```python
client = getattr(t, "_client", None)       # None on advertise listener
if client is None or not getattr(client, "is_connected", True):
    # reopen
```

That `client is None` branch fired on every poll cycle, so
every ~60 s we tore down the scanner subscription, BlueZ
deregistered the discovery filter, the listener rearmed, and
adverts arriving during the settle window were silently lost.

On most installs we'd still decode something in each 60 s
window — Victron broadcasts every ~5 s, so the dropouts were
brief enough to be invisible. On the Garage Stack appliance
(Proxmox VM, USB-passed-through dongle) the timing's worse,
and the loop killed every IP22 advert for 2+ hours before this
was noticed.

Now we only reopen when a transport actually exposes a client
that reports disconnected. Passive listeners manage their own
lifecycle; the orchestrator no longer interferes.

## [0.1.40] · 2026-05-22

### Fixed · charger_state pill now reflects the bank, not whichever charger sorted first

On a multi-charger install (MPPT + AC charger + DC-DC) different
chargers can be in different stages at the same instant — MPPT
in absorption while the AC charger is still in bulk, or vice
versa. Today we picked whichever charger sorted first by label
in `get_latest()`, so the pill was effectively alphabetical
luck of the draw.

Now the pill picks the **most-active stage** across every
online charger:

  `bulk > mppt > absorption > equalize > float > storage > low_power > off > fault`

If ANY charger is in bulk, the pill says bulk — matches the
user's mental model ("is my bank charging hard right now or
just maintaining?"). Silent devices (≥10 min since last
broadcast) are excluded so a dead BLE radio's stale "bulk"
doesn't poison the aggregate.

## [0.1.39] · 2026-05-22

### Fixed · stale charger state + phantom "Other source" tile

Two related UX fixes for the "AC charger went silent on BLE"
case (very common on Victron IP22 chargers, which intermittently
stop broadcasting during float / storage stages):

- **Cloud dashboard's "bulk" pill stayed forever**: the appliance
  read `charger_state` straight from the latest-values table
  without any recency check, so an IP22 that broadcast `bulk`
  before going quiet kept showing as "bulk" on
  wattpost.cloud/app indefinitely. Now skips devices whose
  `_last_seen` is more than 10 min old — same threshold the
  Devices snapshot uses for the online flag.
- **Power Flow tile rendered a phantom "Other source · estimated"**:
  when reconciliation found unattributed watts flowing into the
  bank (because the silent charger was still physically pushing
  but its BLE was dark), the dashboard added a separate "Other
  source" tile alongside the silent AC Charger tile. Now, when
  exactly one source is silent and there's an unattributed gap,
  the gap is attributed back to that silent device with a "best
  estimate from bank" sub-label. One tile, clearly labelled,
  maths still balances.

## [0.1.38] · 2026-05-21

### Changed · appliance ships device snapshot in heartbeat extras

Each heartbeat now includes a `devices` field listing up to 8
devices the appliance is polling — name, vendor, kind, online
flag, and one headline value (battery SoC, charger PV power,
shunt current, etc.). Capped at ~400 bytes total to stay
inside the 2 KiB extras budget.

This powers the **Devices** section on the mobile per-site
dashboard at `wattpost.cloud/app/site/{id}` so a Pro / Installer
user opening the app sees their Renogy MPPT, JK BMS, and Victron
shunt on one screen — without needing to open the appliance's
own dashboard. Older appliances (≤0.1.37) keep working; the
cloud simply hides the section when the field is absent.

## [0.1.37] · 2026-05-21

### Changed · appliance dashboard strips its chrome inside the WattPost mobile app

When the appliance dashboard is loaded inside the Capacitor
WebView (detected by `WattPostApp/` in the User-Agent), the
appliance now hides:

- Its own top header (the cloud already gave the user a status
  bar + mobile shell; the appliance's `.app-header` rendered as
  duplicate chrome)
- The floating "?" help FAB (docs live in the mobile app's
  Account tab)

Side effect: standalone PWA users on the appliance's local URL
see the original layout — only the `WattPostApp/` UA flips this.

Cache-busters: `app.js?v=185`, `styles.css?v=122`,
`sw.js CACHE_VERSION` → `wattpost-v98-app185-css122`.

## [0.1.36] · 2026-05-21

### Fixed · appliance dashboard respects device safe-area insets

The `.app-header` topbar padding now uses
`max(design-floor, env(safe-area-inset-*))` on all four sides so
the system status bar / display cutout no longer draws on top of
the brand + Healthy pill + help button when the dashboard
renders inside the Capacitor WebView (or any other mobile shell
that opts into edge-to-edge layout). Desktop browsers see no
change — `env()` resolves to 0.

Caught during the first WattPost mobile-app emulator test.

## [0.1.35] · 2026-05-21

### Changed · donut head telegraphs flow direction

The leading-edge dot + pulsing halo on the SoC donut now reflect
*flow direction* instead of inheriting the arc's SoC-severity
colour. The arc still reads SoC: green / blue / amber / red
across charging / holding / discharging / critical bands. But the
head dot independently shows whether the bank is charging (green
pulse) or discharging (amber pulse).

The visible case this unlocks: a bank at 11 % SoC that's actively
charging shows a red ring (still low — don't sugarcoat it) with a
green pulsing head (we're recovering). Before this change the
head was red too, masking the recovery signal.

Applied only when |netW| > 5 W. The "holding" band keeps its
neutral blue head so a near-zero net flow doesn't flicker between
green and amber.

## [0.1.34] · 2026-05-21

### Removed · Tailscale integration

Remote access via WattPost now goes through `wattpost.cloud` (pair
your appliance, then use the cloud broker URL). The in-app
Tailscale wiring — Settings → Network panel, install.sh sudoers
fragment, /api/system/tailscale/* endpoints, MOTD URL — is all
gone in this release.

If you were using Tailscale as your remote-access path, you have
two choices:
1. **Pair with wattpost.cloud** (recommended — handles HTTPS,
   auth, no port-forwarding). Free Hobby tier covers one site.
   See docs/remote-access.md.
2. **Run Tailscale yourself** — `curl -fsSL https://tailscale.com/install.sh | sh`
   on the appliance host, then `sudo tailscale up`. The WattPost
   daemon no longer manages it but doesn't conflict with it
   either.

The `/etc/sudoers.d/wattpost-tailscale` fragment is removed on
the next `install.sh` run (which the auto-updater does anyway).

## [0.1.33] · 2026-05-21

### Fixed · phantom PV credit at sunrise

`pv_today_wh` was reading MAX of the device's running counter,
which doesn't reset on UTC midnight (it follows the MPPT's own
clock). On a fresh morning poll the appliance was reporting
~940 Wh of "harvested today" at 06:00 — yesterday's accumulated
total bleeding through. Replaced with a positive-delta walk over
ordered samples that resets cleanly on counter rollback. Live
verified: 940 → 12 Wh on a real install.

### Fixed · cloud broker "Open" intermittent white screen

`broker_can_access` was checking the ASGI scope peer IP and
rejecting requests it didn't recognise — but behind CF + Caddy
that field intermittently reports a Cloudflare edge IP
(141.101.x, 162.158.x) instead of the proxy. About 30 % of
brokered requests were 403'ing as a result, surfacing to
customers as the long-running "white screen on Open" bug.
Removed the peer check entirely; the existing auth + owner_id
checks cover all real threats.

### Fixed · kiosk-share modal stuck on "Loading…"

`fmt.ago(...)` typo in the cloud kiosk-shares list called an
undefined function, throwing inside the render loop after the
fetch resolved. Result: modal opened, fetched the list, then
silently swallowed the render and left the spinner in place.
Fixed the call site (`fmtAgo`) and wrapped the render in a
try/catch so any future render error logs to console instead of
freezing the panel.

### Fixed · cloud backup gate bypass on LIST endpoint

The Hobby-tier cloud backup feature gate was applied to the
UPLOAD endpoint but not the LIST endpoint — a Hobby user could
toggle backups on in the UI and the appliance would happily
list (empty) cloud backups, masking the upgrade prompt.
LIST now honours `is_staff` / `is_comped` the same way UPLOAD
does, via a shared `_user_can_cloud_backup` helper.

### Fixed · stale UI shell after service-worker eviction

Service-worker `CACHE_VERSION` and the `?v=` cache-busters on
`index.html`, `app.js`, and `styles.css` were bumped to force
eviction of the donut-era + kiosk-exit-era stale shells. iOS
Safari in particular was serving the wrong version of the topbar
and kiosk exit-button hide-logic long after `docker compose pull`.

### Added · cloud error tracking via self-hosted GlitchTip

`sentry-sdk` is now wired into the cloud Litestar app behind a
`SENTRY_DSN` env var (silent no-op if unset). Catches 5xx
tracebacks + integrates with Litestar + the logging chain.
DSN is paste-only at the VPS — code path is live.

## [0.1.32] · 2026-05-20

### Fixed · #225 dual-format broker-auth verifier

The #225 kiosk-share work landed on `main` between the v0.1.31
release commit and the v0.1.31 tag, so the appliance side of the
change never made it into a shipped build. v0.1.31 appliances
still only understood the legacy two-part `X-WP-Broker-Auth:
<ts>.<sig>` header, even though the cloud started emitting the
three-part `<ts>.<scope>.<sig>` shape on every brokered request.
Result: cloud "Open" button bounced every customer to the
"Sign in via wattpost.cloud" page after the cloud deployed.

The wire-format hotfix already shipped on the cloud (emit legacy
two-part for owner sessions, three-part only for kiosk scope).
v0.1.32 carries the corresponding appliance change: the verifier
accepts both shapes and routes by scope (owner = full access,
kiosk = read-only allow-list).

Customers on v0.1.32+ unlock the kiosk-share feature properly.
Customers on v0.1.31 keep working via the cloud's wire-compat
emit until they upgrade.

### Added · `header_prefix` in broker-auth diagnostics

`/api/diagnostics/broker-auth` now records the first ~80 bytes
of the raw `X-WP-Broker-Auth` header on non-ok verdicts. Lets
operators diagnose cloud↔appliance wire-format drift without
re-instrumenting the daemon. Captured only on `bad-format`,
`bad-mac`, or `expired` — zero overhead on the happy path. Ring
stays local: behind appliance auth, never leaves the box.

## [0.1.31] · 2026-05-20

### Added · #36 Atomic-swap auto-apply updater

Updates are now crash-safe and self-healing. Pi installs gain an
A/B slot layout (`/opt/wattpost-slots/{a,b}/`); `wattpost-update`
installs into the inactive slot, runs a health probe with a
sandbox daemon, atomically flips the `/opt/wattpost` symlink, and
auto-rolls back if the post-swap daemon doesn't come up. Even if
the daemon boots fine in the probe but later crashloops against
real hardware/config, a systemd `OnFailure=` watchdog catches it
(`StartLimit` 3 failures in 60s) and fires `wattpost-rollback`.

What this gets you:
- Power loss mid-update can't brick the device; the symlink swap
  is atomic (single `rename(2)`).
- A bad release parks you on the previous working version within
  ~60s, no SSH required.
- Installer-tier accounts can flip "Auto-apply updates fleet-wide"
  on the cloud dashboard — zero-touch updates across every site,
  with the same safety net per appliance.

New surface:
- `/opt/wattpost-slots/{a,b}/` slot directories
- `/usr/local/bin/wattpost-update` (rewritten end-to-end)
- `/usr/local/bin/wattpost-rollback` (new)
- `wattpost-rollback.service` systemd OnFailure unit
- `GET /api/system/slots` + `POST /api/system/slots/rollback`
- `wattpost-config` menu entries 11 + 12 (slot status, rollback)
- Cloud-side: `appliances.auto_apply_updates` boolean (migration
  0036), dashboard toggle, heartbeat-handler auto-queue, dedup
  against in-flight updates.
- New docs page at `/docs/atomic-swap-updates` walking through
  the full flow.

### Fixed (side effects of building #36)

- `solar_monitor.cli._resolve_db_path` and `storage.sqlite.open`
  both choked on `:memory:` (treated as a filesystem path).
  Fixed — SQLite-special paths now pass through verbatim. Needed
  by the atomic-swap health probe.
- Cloudflare was caching `releases.wattpost.io/source/latest.tar.gz`
  longer than `publish-source.yml` expected, leaving the tarball
  and its `.sha256` out of sync. `wattpost-update` now appends a
  cache-buster query string. Proper Caddy-side `Cache-Control`
  fix tracked separately (#224).

## [0.1.30] · 2026-05-20

### Fixed

- **`wattpost-update` was silently doing nothing on Pi installs**
  — `pyproject.toml` hardcodes `version = "0.0.1"` while the
  daemon's `__version__` bumps in `solar_monitor/__init__.py`.
  pip's `--upgrade` saw "0.0.1 already installed, skip" and did
  not swap the venv contents. The on-disk source got swapped, the
  `/etc/wattpost/version` file got rewritten, and the UI said
  "updated to vX.Y.Z" — but the running code stayed on whatever
  the user originally installed. install.sh now passes
  `--force-reinstall --no-deps` so the venv actually moves. Verified
  end-to-end on a fresh Ubuntu host (v0.1.28 → v0.1.29 round-trip
  via `wattpost-update`).
- **`/api/system/update/apply` returned `Internal Server Error`**
  on Docker installs (or any host without `/usr/local/bin/wattpost-update`)
  because Litestar hides `HTTPException.detail` on 5xx. Changed to
  400; users now see the actionable text ("Docker installs should
  run `docker compose pull && docker compose up -d`…") instead of
  a generic 500. The UI hides the button on Docker so this is an
  edge case, but curl users + broken-helper Pi installs now get a
  useful response.

## [0.1.29] · 2026-05-20

### Added · #217 Anonymous local-install beacon

Fleet visibility for the un-paired population. The appliance
generates a random UUID at first boot (`/var/lib/wattpost/install-id`),
then once a day — piggy-backed onto the existing update-check —
POSTs three fields to `wattpost.cloud/api/local_installs/beacon`:
the install_id, the daemon version, and `pi` vs `docker`.
Cloudflare's country header is read server-side and persisted as
a 2-letter ISO code; no IP, no email, no MAC, no battery data.

Default ON. Opt out with `local_telemetry.enabled: false` in
`config.yaml` (the update check still fires — we need it for the
`Update available` badge — just without the install_id query).

Customer-visible: a new `Privacy & telemetry` page in the docs
spelling out every outbound flow and how to switch each one off.

Internal: new `local_installs` table (migration 0034) + admin
Overview tile showing total / 7-day-active / version distribution
/ install method / country breakdown.

### Fixed

- **`/api/snapshot` 500 in demo mode** — `build_snapshot` accessed
  `self._poller._transports` directly, but the synthetic poller
  used in demo / dev installs has no such attribute. Defaults
  configured/open transport counts to 0 when the poller doesn't
  expose them. Found during the appliance smoke sweep.
- **Appliance 500s now log the traceback** — added an
  `after_exception` hook on the Litestar app so unhandled
  exceptions print the full stack to stdout instead of vanishing
  into a generic "500 Internal Server Error". Mirrors what cloud
  got in #194; the snapshot bug above is what made the gap
  obvious.
- **install.sh on non-Pi Debian/Ubuntu hosts** — the systemd unit
  declares `SupplementaryGroups=bluetooth`, which fails with
  `216/GROUP` and crash-loops the daemon on hosts where bluez
  hasn't created the group yet (notably Ubuntu Server cloud-init
  images). install.sh now creates the `bluetooth` group if it's
  missing, so the unit can always resolve it. Pi OS, which ships
  bluez, is unaffected. Found during a fresh-VM Phase F smoke.

## [0.1.28] · 2026-05-19

### Added · #208 Admin oversight (release / billing / support actions)

Third of the SaaS polish trio (after the alerts inbox #206 and
the energy analytics page #207). The admin portal gets:

- **Overview tab** — release adoption (% of fleet on each daemon
  version, sourced from `appliance.appliance_version` which
  heartbeat ingest already keeps fresh) plus a billing block
  (tier breakdown, subscription-state breakdown, estimated MRR
  from the local DB, recent cancels in last 30 days).
- **Reset 2FA** button per user. Clears `totp_secret` +
  `totp_enabled_at` so the user can re-enrol on next login.
  Doesn't drop `require_2fa` — losing your phone isn't a get-
  out-of-policy card. Audit-logged.
- **Comp month** button per appliance. Pushes
  `subscription_current_period_end` out by 30 days without
  touching Stripe; the local-DB override for goodwill gestures
  during support. Audit-logged.

Four new endpoints under `/api/staff`:
`release_adoption`, `billing`,
`users/{id}/reset_2fa`, `appliances/{id}/comp_month`.

## [0.1.27] · 2026-05-19

### Added · #207 Cloud energy analytics + savings page

New `/app/energy` page that aggregates every appliance's daily
PV-in and load-out totals into one cross-site view. Three summary
tiles (PV generated, load consumed, optional savings vs grid) +
a stacked daily bar chart with hover tooltips. Range selector
covers last 7 / 30 / 90 / 365 days; per-site filter selector.

Plumbing:

- New `GET /api/energy/aggregate?days=N&site_id=...` endpoint.
  Buckets heartbeats by UTC calendar day, takes the day's `max()`
  of each `today_wh` field (the appliance counter is monotonic
  within a day before midnight reset). Returns per-day totals
  across the account plus per-site breakdowns.
- Per-account grid tariff column on users (migration 0033) +
  `PATCH /api/account/tariff` to set it. NULL = no savings line;
  any integer in pence = "Saved £X this month" tile lights up.
- Topbar gets an "Energy" link.

### Fixed

- Cloud alerts API was using `Appliance.owner_user_id` (the field
  doesn't exist — the correct column is `owner_id`). Would have
  500'd every request to the inbox; corrected before any traffic
  hit it.

## [0.1.26] · 2026-05-19

### Added · #206 Cloud alerts inbox (cross-site feed)

New `/app/alerts` page in the cloud SaaS — chronological feed of
every alert fired by any appliance the signed-in user owns.
Filter by site, severity, read/unread. Mark-as-read individually
or in bulk. Topbar gets an "Alerts" link with an unread-count
badge.

How it's wired:

- **Appliance side.** `AlertEngine` now keeps a 200-entry ring
  buffer of fired events. The cloud-heartbeat reads from it via
  `recent_events_since(ts)` and ships up to 20 events per
  heartbeat in `extras.recent_alerts`. No per-heartbeat state on
  the appliance.
- **Cloud side.** New `cloud_alerts` table (migration 0032) with
  a UNIQUE constraint on `(appliance_id, rule_id, fired_at_ts)`
  so retransmits on a flaky link are no-ops. The heartbeat
  handler INSERTs ON CONFLICT DO NOTHING. New `CloudAlert`
  SQLAlchemy model.
- **API.** `GET /api/alerts` (cursor pagination + filter by
  site / severity / unread), `POST /api/alerts/{id}/ack`,
  `POST /api/alerts/ack_all`. Owner-scoped via a join on
  `appliances.owner_user_id` so no cross-account leakage.
- **UI.** `alerts.html.jinja` page with filters, infinite-scroll
  "Load more", per-row + bulk ack. Topbar badge reads
  `unread_count` from the API.

Highest installer-tier impact: managing N sites no longer means
clicking through N local dashboards to see "what alarmed last
night".

## [0.1.25] · 2026-05-19

### Added · #201–#205 Tier 1 + Tier 2 driver batch

Five new vendor drivers shipped from public protocol docs +
community reverse engineering. All marked **pending community
validation** — first customer report against real hardware
becomes the real-world confirmation. Synthetic-frame smoke
tests in `scripts/verify_new_drivers.py` lock the parse + field
mapping in place so any regression shows up before customers
see it.

- **#201 JBD / Overkill Solar BMS.** Highest-impact unlock —
  covers the BMS inside most cheap LFP packs (Battle Born,
  LiTime, Power Queen, many Eco-Worthy SKUs, anything sold
  rebranded with a "Smart BMS" app sticker). BLE GATT, FF00
  service, commands 0x03 + 0x04.
- **#202 Daly Smart BMS.** Second-most-common BMS in budget
  packs. BLE, 13-byte fixed-length frames on FFF0 service.
- **#203 EPEVER / EPSolar Tracer MPPT.** #1 budget MPPT in
  DIY van + cabin builds. Modbus RTU over USB-RS485 with
  FC04 (input registers) for live state. Slots into the
  existing `serial_modbus` transport.
- **#204 AiLi smart shunt.** Sub-£40 BLE shunt; the first
  piece of telemetry most DIY van builders buy.
- **#205 Junctek KH-F / KG-F shunt.** ASCII-framed BLE shunt.

### Added · `Section.function_code` (FC03 / FC04)

The Section descriptor used by Modbus-style drivers now picks
the function code at read time. Default FC03 (matches every
existing Renogy driver). EPEVER uses FC04 for live state and is
the first user of the new path. Drivers that don't set it
behave identically to before.

### Added · `modbus.build_read_input`

FC04 frame builder for the new Section path.

## [0.1.24] · 2026-05-19

### Added · #199 Setup wizard support for VE.Direct

v0.1.23 shipped VE.Direct as config-yaml-only. This release wires
it into the setup wizard so customers don't have to edit YAML to
add a Victron device over cable:

- `/api/setup/usb_scan` now sniffs at 19200 baud as a second pass
  when 9600 yields unrecognised bytes. VE.Direct frames identify
  themselves via the literal `PID\t` / `Checksum\t` substrings;
  no false positives from random serial noise.
- The wizard's USB results list now renders a "Victron VE.Direct"
  chip for any port emitting frames, with a primary action to
  add it directly. No more "Use as Modbus" guess for a Victron
  cable.
- `POST /api/setup/transports/add` accepts `type: ve_direct` +
  validates that baudrate is 19200 + dedupes on port.
- The transports list in the wizard now displays VE.Direct
  entries with a "Victron VE.Direct" label instead of falling
  back to the default Bluetooth tag.

### Docs · README refresh

The top-level README was still saying "Renogy and, soon, Victron
and JK-BMS" and "Victron SmartShunt awaits hardware" — both
v0.0.x-era. Refreshed: top section reflects what actually ships,
component table is current through v0.1.23, the architecture
tree shows the new transport + adapter layers (smart_plug,
solar_pause, ve_direct).

## [0.1.23] · 2026-05-19

### Added · #197 VE.Direct wired transport for Victron read

A second read path for Victron alongside BLE Instant Readout, for
metal-van installs and dense-RF environments where BLE isn't
reliable. Three device-kind drivers:

- **VictronVeDirectShunt** — SmartShunt + BMV-700 / 702 / 712
- **VictronVeDirectMppt** — SmartSolar MPPT (every model with a
  VE.Direct port)
- **VictronVeDirectPhoenix** — Phoenix Inverter VE.Direct (the
  small pure-sine line; MultiPlus / Quattro need VE.Bus + MK3
  and stay out of scope)

Same dashboard fields as the BLE drivers, so the flow strip and
bank aggregation render identically regardless of transport.
Registered under a sibling vendor id `victron_vedirect` so the
config-yaml entry distinguishes BLE from wired explicitly.

Cable: Victron's "VE.Direct to USB interface" (~£25), or a DIY
JST + FTDI rig for ~£12. See `docs/wired-setup.md` for the
pinout and config-yaml example.

Read-only by design. VE.Direct doesn't expose writes for normal
settings (VictronConnect / VRM / Cerbo only), and our Victron
scope memo keeps writes off the table regardless.

End-to-end smoke test in `scripts/verify_ve_direct.py`. Uses
pty.openpty + a thread emitting canned text frames to exercise
the transport + each driver round-trip without hardware. Wires
into CI later.

## [0.1.22] · 2026-05-19

### Added · #163 followup, smart-plug output adapter

The solar-pause rule from v0.1.21 had no actual output it could
drive. v0.1.22 adds two local-HTTP smart-plug adapters that
WattPost talks to directly, no MQTT broker, no Home Assistant,
no cloud:

- **Shelly Gen2** (Plug S, Plus, Pro). JSON-RPC at
  `/rpc/Switch.Set` + `/rpc/Switch.GetStatus`. Basic-auth
  optional. The recommended option for new installs.
- **Tasmota**. `/cm?cmnd=Power...` on any flashed Sonoff / Athom
  / similar.

Configure via a new `smart_plugs:` block in config.yaml; each
entry becomes one controllable output and shows up in the
solar-pause dropdown under Settings → Solar-aware charger pause.

The rule's "respect manual override" gate works the same way it
does for Modbus outputs: toggle the plug yourself from the
dashboard or its own app and the rule backs off.

### Fixed · v0.1.21 release notes called it "Renogy AC charger only"

The original v0.1.21 changelog and release-notes blog implied
Renogy AC chargers were the supported solar-pause target. They
weren't (Renogy doesn't publish a verified write map for the
AC-charger side, and writing to a guessed register is the kind
of thing that bricks customers' gear). Corrected here and in
the blog post.

## [0.1.21] · 2026-05-19

### Added · #138 Reset-to-defaults for Docker parity

Settings → Diagnostics → Reset wipes transports, devices,
exporters, alerts, output schedules and rules back to first-boot
state. Web password, SQLite history, branding and (by default)
cloud pairing are preserved. Atomic config.yaml replace with a
`.bak` for manual recovery. Type-to-confirm gate; danger-zone
styling. Closes the last wattpost-config TUI gap for Docker
users.

### Added · #163 Solar-aware AC charger pause (Pro)

New rule that pauses an AC charger when PV is covering the
load and wakes it before the bank drops too low. Four safety
gates: hard SoC floor that beats every forecast, configurable
cooldown to stop relay flap, "respect manual override" when
the user just toggled the output themselves, and a per-rule
validator that blocks misconfigurations from landing in
config.yaml. Settings → Solar-aware charger pause; off by
default. v0.1.21 ships the controller engine + Settings UI;
the smart-plug output adapter that wires it to real hardware
lands in v0.1.22.

### Added · `/api/snapshot`

One-shot REST endpoint mirroring the SSE first frame. Returns
devices + poll_run + today atomically read from the store, so
the polling fallback (used through the cloud broker on iOS
Safari) can't straddle a poll cycle the way the old three-fetch
form could.

### Fixed · #162 Hero / Flow snapshot disagreement

The hero, flow strip and alerts panel each derived their own
freshness floor against a freshly-read `Date.now()`. On the 90 s
boundary a battery could be counted by one tile and excluded by
another, leaving the dashboard visibly inconsistent. Bank +
flow model are now memoised per-frame with one stamped
`nowSec`.

### Docs

- New strategic doc `docs/coverage-roadmap.md` with the
  prioritised driver queue (Tier 1 JBD / Daly / EPEVER,
  Tier 2 AiLi / Junctek / Battle Born, Tier 3 MPP Solar /
  Sterling / REDARC) plus the explicit out-of-scope list.
  Linked from `adding-a-vendor.md`.
- New blog post `ha-mqtt-external-broker`: how to connect
  WattPost to Home Assistant when you can't use the Mosquitto
  add-on (HA Container, Core, or a standalone broker).

## [0.1.20] · 2026-05-19

### Added · #184 wizard hint for "BT-2 held by another LAN host"

A Renogy BT-2 dongle only allows one BLE central at a time. If
another WattPost on the same LAN (laptop docker, NAS, garage
Pi) has the same dongle paired, the dongle stops advertising
entirely and a fresh appliance's scan finds nothing. The setup
wizard now probes the local /24 when a scan turns up zero
Renogy devices and surfaces a yellow panel naming the suspect
peer.

Detection uses `/api/health` which now includes
`service: "wattpost"` + version. Self-exclusion is via the
kernel's outbound-route IP (the obvious "first non-loopback NIC"
approach picks the docker0 bridge inside host-mode containers
and silently scans the wrong subnet).

### Added · #158 BLE diagnostic endpoint

New "Run BLE diagnostics" button in the setup wizard runs
bleak and `bluetoothctl --timeout 3 scan on` side-by-side and
reports the divergence. Catches the Realtek + BlueZ 5.72
silent-failure case where `bluetoothctl` returns zero device
hits but bleak finds plenty. Six verdicts covered:
`ok` / `scan_silent_failure` / `bleak_silent_failure` /
`bleak_failed` / `bluetoothctl_failed` / `no_devices_seen`,
each with a one-paragraph suggestion the UI renders verbatim.

### Added · #172 editable retention tiers + poll interval

Settings → History & polling tile lets users edit polling cadence
and per-tier history retention:

- Poll interval · 5–3600 s · default 60
- Raw samples · 1–90 d · default 7
- 1-min aggregates · raw–365 d · default 30
- 1-hour aggregates · 1-min–3650 d · default 365

Values apply live (next poll cycle / next maintenance pass) and
persist to `config.yaml` under a new `history:` block. Tier
ordering is enforced server-side: raw ≤ min ≤ hour.

## [0.1.19] · 2026-05-19

### Added · #170 writable-settings fan-out (phase 3, Renogy DCC50S/30S)

The per-device settings work started in phase 1 (Rover MPPT) and
phase 2 (PATCH endpoint + confirm modal + FC06 with BT-2 ack-
swallowing fallback) now covers the second Renogy charger family.

The DCC50S / DCC30S are the same charging silicon as the Rover with
an alternator front-end bolted on. Renogy reuses the
`0xE004 / 0xE008..0xE00C` register block across both products, so
the same five settings work identically:

- **Battery type**. Flooded / sealed / gel / lithium / custom
- **Absorption (boost) voltage** · 12.0–16.0 V
- **Float voltage** · 12.0–15.0 V
- **Low-voltage disconnect** · 10.0–12.8 V
- **Low-voltage reconnect** · 10.5–13.5 V

Read-back comes from a new `charge_voltages` Section (registers
`0xE008..0xE00D`, 6 words) added to the DCC50S poll cycle so the
confirm modal can show the current value before each change.

### Held back for hardware validation

Renogy inverter-chargers + smart shunts have a writable surface
documented in cyril/renogy-bt and Renogy's public Modbus PDF, but
their write-register addresses aren't in our codebase yet and a
wrong guess could brick a customer's 3 kW inverter or scramble a
shunt's SoC tracking. Tracked as follow-up tasks (#185, #186);
will ship once we have a unit in the Proxmox lab or a brave
customer to test against. Smart lithium batteries don't expose a
documented user-writable surface. Read-only is the right answer
there indefinitely.

## [0.1.18] · 2026-05-18

A debugging marathon on Ritual North's appliance produced a long list of
real fixes. Almost all of them invisible until you hit them, then
extremely visible. Grouped by area.

### Fixed. Renogy BT-2 + Victron BLE coexistence on one HCI adapter
BlueZ only allows one in-flight discovery per HCI adapter. When a
Victron passive listener AND a Renogy BT-2 transport are both
configured (the common multi-vendor case), they fight for the
discovery slot. The losing one returns `org.bluez.Error.InProgress`
every poll cycle, indefinitely. Ritual North's logs were spitting it
every minute for hours.

Three changes collaborate to fix it:

1. **`_SharedVictronScanner.pause()` / `resume()`**. The singleton
   scanner can now yield its discovery slot on demand and re-grab
   it afterwards. Renogy and the wizard's manual BLE scan ask for
   it before they do their own `BleakScanner.discover()`.
2. **`HCI_DISCOVER_LOCK`** in `ble_modbus.py`. A module-level
   `asyncio.Lock` serialises Renogy reconnects against the manual
   `/api/setup/ble_scan` endpoint. Without this they competed
   with each other (same InProgress error, different actors).
3. **The wizard's `ble_scan`** acquires the same lock and pauses
   Victron before running its scan. Returns Victron afterwards.

Net result: three-way coexistence on one adapter. Log shows the
ballet · `victron scanner paused (peer transport scanning)` →
discovery → `victron scanner resumed`.

### Fixed. Clean BLE disconnect on shutdown (the BT-2-stuck root cause)
The single most-reported Renogy BT-2 failure mode (cyrils/renogy-bt
#97, #45, multiple Renogy-HA threads, even Renogy's own KB): the
dongle accepts one BLE master at a time, and if the previous
process exited WITHOUT issuing a clean GATT disconnect, the BT-2
holds the phantom session in its own RAM and refuses every future
scanner until physically replugged.

`BleModbusTransport.close()` is now defensive on three counts:

- `stop_notify(notify_char)` capped at 2 s
- `client.disconnect()` capped at 5 s
- ALWAYS follows with a subprocess `bluetoothctl disconnect <mac>`
  as a belt-and-braces backstop, even when bleak reports OK. The
  subprocess path drops the session at BlueZ level even when the
  bleak Python object got wedged.

Logs `close: bleak={ok|forced}, bluetoothctl=ok` at INFO so
operators can verify clean exits via `journalctl`.

Also bumped Docker stop_grace_period to 20 s on the new compose
template. Gives the daemon enough headroom for the worst-case
close() path (12 s) without Docker SIGKILLing mid-disconnect.

### Fixed. Renogy data shown as live when transport is stale
v0.1.16 / v0.1.17 added "treat the device as silent when fresh
broadcasts say it's off" for Victron. But Renogy doesn't stamp
`advertisement_age_s`, so when the Renogy transport was offline
the dashboard happily kept rendering the last-known V/A/W from 55
minutes ago as if it were live (Solar 2 W · 22.5 V · 0.08 A,
Battery 96.8%, Load 81.7 W estimated. All stale).

Generic vendor-agnostic check added to `buildFlowModel` AND
`aggregateBank`: if any device's `_updated_at` is more than 90 s
old, treat as silent (power forced to 0, sub-label "Stale ·
last poll N min ago"). Renogy / JK BMS / any future vendor that
doesn't stamp its own age now gets the same honest treatment.

### Improved. Wizard copy + status pill
- "transport" is gone from every user-facing string in the setup
  wizard. The internals (API endpoints, CSS classes, Python module
  names) still say "transport" but the UI says "Bluetooth
  connection" or just "connection". Step 1 → "Your Bluetooth
  connection". Step 2 → "Find your devices".
- First-time empty state leads with **"First time? Let's connect
  your gear"** and a one-line explainer about which vendors need
  dongles vs broadcast directly.
- Offline status pill: was `offline · will reconnect on scan`
  (misleading. Daemon auto-retries every poll regardless of
  user action). Now `offline · retrying`, with a tooltip on hover
  explaining the auto-retry.
- Trash + pencil action buttons on narrow viewports (iPhone-width)
  no longer crowd the status pill off-screen.

### Cache busters
`app.js` 167 → 168, sw.js `CACHE_VERSION` →
`wattpost-v78-app168-css109`.

## [0.1.17] · 2026-05-18

### Fixed. Donut centre disagreement hint overflowing on mobile
The SoC donut on the dashboard renders SoC + label + flow pill +
(optionally) a "BMS X% · shunt Y%. Showing shunt" disagreement
hint. On narrow viewports (390 px iPhone) the hint wrapped to
two lines via `max-width: 12rem` and pushed past the bottom of
the green ring, looking like the donut had spilled.

Two fixes:
- Compact JS format: `${activeSource} ${activeSoc}% ·
  ${otherSource} ${otherSoc}%`. Active source first (the one
  the donut is displaying), other source second, drop the
  "· showing X" suffix since the active source comes first in
  the order itself. ~14 chars total vs ~30 before.
- CSS clamp: `.donut-disagreement` now `white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis`. Guards against
  future overflow if a four-digit shunt count or longer source
  label ever appears.

Tooltip on hover/long-press still carries the full "we're showing
the shunt because…" explanation.

`app.js` 166→167, `styles.css` 108→109, sw.js CACHE_VERSION
bumped to `wattpost-v77-app167-css109`.

## [0.1.16] · 2026-05-18

### Fixed. AC charger Power Flow tile: also treat "explicitly off" as silent
v0.1.15 caught the stale-broadcast case (no advert in 60 s →
power forced to 0, Silent badge). It missed the related case
where the device IS still broadcasting fresh adverts but the
payload says it's not producing.

Reproduction: Ritual North's Victron Blue Smart AC Charger is unplugged
from the AC source. The BLE radio keeps beaconing the LAST V/A
readings every ~1 s (so `advertisement_age_s` stays < 60 s), but
`charging_state` switches to `"off"`. v0.1.15's silent check
relied solely on advertisement age, so the Power Flow tile kept
rendering 13.7 V · 15.00 A · 206 W indefinitely.

Fix in `buildFlowModel` (app.js): collapse "stale broadcast" and
"explicitly idle" into one `isSilent` branch. The idle check
treats Victron `ChargerState` values `OFF` / `LOW_POWER` /
`FAULT` as not-actively-producing. Everything else (BULK, ABS,
FLOAT, STORAGE, EQUALIZE, INVERTING, POWER_SUPPLY) stays active.
When idle, the tile sub-label becomes "Off" / "Standby (low
power)" / "Fault. Not producing" instead of the misleading
"Silent. Last heard X s ago" (because the device is talking,
just not producing).

`app.js` cache buster 165 → 166, sw.js CACHE_VERSION
`wattpost-v75-app165-css108` → `wattpost-v76-app166-css108` so
browsers re-fetch the new logic.

## [0.1.15] · 2026-05-18

### Fixed. Power Flow tile rendering stale silent-device watts (#171 follow-on)
Backend was correctly stamping `advertisement_age_s` past 60 s when
a Victron BLE device stopped broadcasting (v0.1.11 + the 86400
sentinel in v0.1.12), and the Devices-tab cards were greying out
properly. But the Dashboard's Power Flow tile was reading
`output_1_power_w` straight from the latest snapshot and rendering
it as live. So Ritual North's screen showed "AC Charger 206 W · 13.7 V ·
15.00 A" for a charger he'd had switched off for hours, with the
fake watts inflating Source totals + Load estimate downstream.

Fix: `buildFlowModel` in app.js now checks each device's
`advertisement_age_s` before contributing to sources / loads /
battery. Stale devices (> 60 s) get `power: 0`, a `silent: true`
flag, and a "Silent. Last heard X ago" sub-label. The tile still
renders (the device is configured; hiding it would be confusing)
but visually mutes via `.flow-tile.is-silent`. Opacity .5,
greyscale .6, sub-label switched to the age. Source totals and
the bank's Load estimate no longer include the phantom watts.

CACHE_VERSION bumped to v75-app165-css108.

## [0.1.14] · 2026-05-18

### Fixed. Migration v1 crashloop on partially-migrated DBs
Caught the demo container in a crashloop: `sqlite3.OperationalError:
duplicate column name: display_name`. The v1 schema migration ran
ALTER TABLE on a DB that already had the column (a prior daemon
must have ALTERed but crashed before the `PRAGMA user_version`
bump landed). Every reboot then re-tried the migration, hit the
duplicate, and exited with `Application startup failed`.

Fix: rewrote v1 as an idempotent callable that reads
`pragma_table_info` first and only adds the column when missing.
The migration framework already supported callables (line 274
in storage/sqlite.py); v1 was just the only entry that used a
plain SQL string. Future schema additions should follow the
same pattern.

The demo container will self-heal on the next image pull.

## [0.1.13] · 2026-05-18

### Added. Editable device settings (#111 phase 2)
The disabled "Edit" buttons on the device-detail Settings panel
are now live. Click → modal opens with the current value, the
right input shape for the descriptor (select for enum, number with
min/max/step for numeric) + the help text. Apply hits FC06 via the
same code path the Rover load-output adapter has used in
production since #104. Write, optional read-back to confirm,
BT-2 ack-swallowing tolerated.

Customers running Renogy Rover charge controllers can now edit
the 5 declared settings from WattPost without opening the BT-2
app: battery type, absorption voltage, float voltage,
low-voltage disconnect, low-voltage reconnect. Edit modal
validates client- AND server-side against the descriptor's
range / choice list before issuing the write.

Path:
- New `solar_monitor/settings_write.py`. Reusable FC06 helper
  with the same ack-swallowing fallback the load-output uses.
- `PATCH /api/devices/{label}/settings/{key}` validates body
  `{value: ...}` against the descriptor, encodes via scale,
  resolves the live transport, writes, read-backs, audit-logs,
  pushes the new value into the `latest` store so the UI
  reflects it before the next regular poll cycle.
- Transports that don't support `request()` (Victron BLE
  broadcast-only) return 409. The descriptors don't exist on
  Victron drivers per scope, but defence-in-depth.

Phase 3 (#170) will fan the descriptor catalog out across the
rest of the Renogy line + the JK BMS write surface (charge /
discharge MOS, cell-balance).

CACHE_VERSION bumped to v74-app164-css107.

### Note. V0.1.12 was a hot-patch
Sentinel-age fix for the never-seen Victron device case (the
86400 sentinel that makes the dashboard grey out tiles correctly
even on first-poll after a daemon restart). Shipped without a
`__version__` bump by accident; v0.1.13 corrects the label.

## [0.1.11] · 2026-05-18

### Fixed. Stale Victron BLE data shown as live (#171)
When a Victron BLE device (AC charger, MPPT, SmartShunt, Orion XS,
etc.) stops broadcasting. Output switched off, dongle out of
range, charger unplugged from mains. The transport's
`get_latest()` correctly starts returning None after 60 s. But the
driver's early-return path only stamped an `_errors` string and no
numeric fields, so the `latest` table kept serving the *previous*
successful row indefinitely. `/api/devices` reported a frozen
`advertisement_age_s: 27` plus 15 A bulk-state forever, even
2 hours after the charger had actually gone silent. Discovered
when Ritual North asked why his History chart was empty for an AC
charger that "the dashboard still showed running".

Fix:
- `BleVictronAdvertiseTransport.last_advertisement_age_s()` returns
  the real-time age in seconds (ticks up while silent), unlike
  `get_latest()` which gates on the 60 s threshold.
- New `vendors/victron/_silent.py` helper that every Victron
  driver routes its stale-path through. Stamps the always-fresh
  age + a descriptive _errors entry; no payload fields so the
  dashboard knows we have nothing live.
- Dashboard device card detects `advertisement_age_s > 60` and:
  - greys out the tile (.dev-card-silent, opacity .55)
  - shows a "Silent. Last heard X min ago" badge

All 8 Victron drivers updated: SmartShunt, SmartSolar, Orion XS,
DC-DC, SmartBatteryProtect, AcCharger, SmartLithium, LynxSmartBMS.

CACHE_VERSION bumped to v73-app163-css106.

## [0.1.10] · 2026-05-18

### Added. Per-device settings panel (#111 phase 1, read-only)
First phase of the per-device write-back story. The device-detail
page now renders a "Settings" card showing the user-tunable
parameters the driver declares. Current battery type, absorption
/ float voltages, low-voltage disconnect / reconnect thresholds
for Renogy Rover today, more drivers + edit support to follow.

Framework:
- `WritableSetting` descriptor added to `vendors/base.py` ·
  drivers declare key / label / kind (enum|float|int) / register /
  scale / min / max / step / choices / help_text / read_from.
  Defaults to `()` so existing read-only drivers are unaffected.
- `DeviceDriver.writable_settings()` is the override point;
  Renogy Rover is the first implementer with 5 settings.
- `GET /api/devices/{label}/settings` surfaces the descriptors
  paired with the current value pulled from the latest poll
  snapshot. Empty `items` for any device whose driver hasn't
  opted in (Victron. Read-only forever per product scope ·
  JK BMS + the rest of Renogy will opt in over subsequent
  phases).

UI: new "Settings" card on the device-detail page below the
existing outputs section. Each row shows label, current value,
help text, and a disabled "Edit" button with a tooltip pointing
at v0.2.0 when the PATCH path lands.

Phase 2 (next): PATCH endpoint that hits FC06 with confirm modal
+ readback + cloud-side audit logging. Phase 3 fans the
descriptor catalog out across the Renogy line + JK BMS.

CACHE_VERSION bumped to v72-app162-css105.

## [0.1.9] · 2026-05-18

### Added. Cloud "Restore from cloud" rescue flow (#166)
Closes the backup arc started in #146 (local backup/restore),
extended in #164 (cloud-side view of uploaded backups) and #165
("Take backup now"). Owners on Pro+ can now restore any cloud-
stored backup onto the originating appliance via a button on
`/app/site/{id}`. Same heartbeat command-queue plumbing as the
other appliance-side actions.

Sequence:
1. Click "Restore" next to a backup row, confirm the destructive-
   action prompt.
2. Cloud queues a `restore_from_cloud` command with
   `target_backup_id` pinning the specific backup.
3. Appliance picks it up on its next heartbeat, downloads via the
   bearer-authed `/api/internal/backups/{id}/download`, runs the
   same `_stage_and_swap` + verify path the local restore button
   uses, PATCHes the command to `success`, then re-execs the daemon
   so the new SQLite + config load fresh.
4. Pairing survives the restore · `_stage_and_swap` preserves the
   live `cloud.bearer_token` / `sso_secret` / `tunnel_token` (the
   #146-phase-2 fix). The appliance comes back online still linked
   to this account.

Cloud-side: Hobby tier 402, target_backup_id required, backup
must belong to this appliance (cross-appliance restore is a
separate v2. Would let "rebuild on new SD card" customers pull
a previous appliance's backup, but adds a layer of "which one is
this" UX). Migration 0031 adds `appliance_commands.target_backup_id`
as a nullable FK with ON DELETE SET NULL so deleting a backup
doesn't cascade-kill the command audit trail.

## [0.1.8] · 2026-05-18

### Added. Renogy Battery Monitor + Shunt driver (#113)
The RBM-S100 / S300 / S500 Battery Monitor with Shunt is the
budget-upgrade entry point for "I just want to know my real bank
state". Single clamp on the negative terminal, no BMS required.
Persona B unlock per the target-customer notes: someone buying
their first shunt to get visibility.

Driver speaks Modbus FC03 over the same BT-1 / BT-2 / USB-RS485
transports the rest of the Renogy line uses. No new transport
work. Surfaces voltage, current, SoC, temperature, remaining /
full capacity (Ah), cumulative charge + discharge (Ah), and
time-to-empty / time-to-full so the hero donut, flow strip,
Remaining tile, Battery health (#109) and runtime forecast (#99)
all light up the moment a customer adds one.

Wizard auto-detection: model strings starting with `RBM` or
containing `SHUNT` route to the new `vendor=renogy, kind=shunt`
driver. Slot 48 (0x30) is already in the slave-id scan list so
existing customers won't need to know the addressing detail.

Register map is from cyril/renogy-bt's `BatteryMonitorClient.py`
+ the public Renogy Modbus PDF; flagged provisional until a paying
customer's unit is verified. Discovery telemetry (#129) will flag
any field-decoding mismatches.

Same bank-aggregation logic as the Victron SmartShunt (#112) ·
mix-and-match is automatic via shared `device_kind = "shunt"`.

## [0.1.7] · 2026-05-18

### Added. Broker-auth diagnostic ring buffer (#167)
Every request that arrives at the appliance bearing an
`X-WP-Broker-Auth` header now lands in an in-memory ring (last 200).
Each entry records the verdict (ok / no-secret / bad-format /
expired / bad-mac), the path, the method, the header age in seconds,
and the originating CF-Ray. Surfaced at
`GET /api/diagnostics/broker-auth` and folded into the existing
support-bundle download at `/api/system/diagnostics`.

Pays for itself the next time the white-page-on-broker bug recurs:
gaps in the timeline = upstream (Caddy / CF / tunnel) problem,
flood of `expired` = clock drift, `bad-mac` = sso_secret drift
(#148-class), `ok` for the failing path = bug post-auth. No more
SSH-into-Caddy-and-grep-JSON during incidents.

### Added · "Take backup now" button on cloud appliance detail (#165)
Pro-tier owners can request an immediate cloud-stored snapshot from
`/app/site/{id}` instead of waiting for the weekly tick. Reuses the
existing heartbeat command-queue: cloud queues a `backup_now`
command, the appliance picks it up on its next heartbeat (≤5 min),
runs `BackupService.snapshot_now()` through the exact same code path
as the scheduled snapshot, and the new row appears in the cloud
backups list once the upload arm completes. The dashboard polls
every 30 s after the click and surfaces the result.

Cloud rejects `backup_now` for Hobby tier with 402; appliance
takes the local snapshot whether or not cloud_upload is enabled,
so clicking the button is never a silent no-op.

### Added. Anonymous device-discovery telemetry (#129)
Off-by-default opt-in (Settings → Discovery telemetry on the
appliance). When enabled, the appliance posts fingerprints of
unrecognised BLE devices its setup-wizard scan picks up to the
cloud. OUI only (never full MAC), advertised local name, first
manufacturer ID + leading 4 bytes, service UUIDs. Nothing
identifying: no FK back to appliance, no owner, no IP.

Cloud rolls observations up by fingerprint hash in
`discovery_observations` and exposes a staff-only roll-up at
`/app/admin/discovery`. The next-driver pipeline reads the top of
that list when prioritising work. Migration 0030 creates the
table; pg_insert ON CONFLICT bumps observation_count + last_seen
on repeats so a popular dongle becomes more visible the more it's
seen, not noisier.

CACHE_VERSION bumped to v71-app161; shell cache-buster ?v=161.

## [0.1.6] · 2026-05-18

### Fixed. White page recurrence after v0.1.4 (stale-shell trap)
Even after the v0.1.4 iOS Safari SSE+tunnel fix, Ritual North kept
hitting the white page on broker view (8:13 this morning, with
a transient "API error: 404" pill before refresh). Caddy logs
confirmed the iPhone was *still* opening `/api/stream` against
the broker host. Meaning the v0.1.4 client code (`IS_BROKER_VIEW`
SSE skip) had never reached the device. The appliance itself
was serving the new app.js (`?v=159`, `IS_BROKER_VIEW` present)
and new sw.js (`wattpost-v69-app159-css104`); the stuck piece
was the iPhone's cached shell.

Root cause: the service worker was **cache-first for navigation
requests**. So:

1. Old SW (cached when v0.1.3 shipped) controls the page.
2. Navigation to `/` → cache hit → old `index.html` served.
3. Old `index.html` requests `/web/app.js?v=158` → cache hit →
   old `app.js` served.
4. Old `app.js` knows nothing about `IS_BROKER_VIEW`, opens
   SSE, white page.
5. `?v=159` never reaches the device because the *shell* asking
   for it is itself stale. The cache-buster strategy only works
   if a fresh shell hands it out. And the SW never lets a
   fresh shell through.

A stale shell self-perpetuates forever, regardless of how many
fixes ship server-side.

Fix: flip the SW to **network-first for navigation**, cache-first
for everything else.

- Online clients always get the latest `index.html` (and thus
  the latest `?v=` cache-buster, which pulls the latest app.js).
  Any future client-side fix self-heals on the next page load.
- Sub-resources (CSS, JS, icons) stay cache-first. They're
  already version-keyed by `?v=`, so a fresh shell brings a
  fresh URL that misses the old cache anyway.
- Offline navigation still falls back to the cached shell, so
  install-to-home-screen launch on plane mode still boots.

This is the same pattern Home Assistant, GitHub, and most major
PWAs use. Pure cache-first navigation is only safe if you never
need client-side code to evolve, which is never the case in
practice.

Bumped `CACHE_VERSION` to `wattpost-v70-app160-css104` and
shell cache-buster to `?v=160` so this rollout itself evicts
the bad shell on every existing device.

## [0.1.5] · 2026-05-18

### Added. Setup wizard: edit existing transport (#159)
Customers can now change a transport's BLE MAC, Victron
encryption key, or serial port path without the
delete-and-recreate dance every time Victron rotates the key
after a factory reset, a BT-2 dongle gets replaced, or a
USB-RS485 adapter moves to a different tty.

- New `PATCH /api/setup/transports/{id}` endpoint with the same
  validation rules as the add path (MAC format, key length,
  /dev/... prefix). Transport `id` is the stable handle and is
  never renamed. Devices and history reference it.
- Pencil-icon button on every transport row in the setup wizard.
  Click → window.prompt for the new value(s) → PATCH → hot-reload.
  Empty answers keep the existing value; Cancel exits cleanly
  without touching config.
- Same .bak rotation + hot-reload as add and delete, so the
  BLE connection re-opens with the new credentials without a
  daemon restart.

### Closed without action · #161 AC charger / DC-DC "today" totals
The victron-ble Instant Readout protocol doesn't surface daily
energy counters for `AcCharger` or `DcDcConverter` device types
(verified directly against the appliance's installed library ·
only `get_temperature`, `get_charge_state`, output V/A are
exposed). Trapezoid integration over power samples is the best
we can do upstream-side. Re-open if Victron adds the fields in a
future firmware/protocol revision.

## [0.1.4] · 2026-05-18

### Fixed. White page on iOS Safari broker view (root cause this time)
The recurring white-page-on-remote-session bug has a confirmed
root cause now, caught via live debugging: it's **not** the
sso_secret drift (#148), it's the iOS Safari SSE + Cloudflare
Tunnel trap.

Sequence:
  1. iPhone loads `<slug>.wattpost.cloud` (broker URL)
  2. App.js immediately opens an `EventSource("/api/stream")`
     so the dashboard updates live as the appliance polls.
  3. iOS Safari serialises HTTP/2 connections in its tiny per-
     host pool around long-lived bodies. The SSE in flight to
     the appliance via the CF tunnel hogs the only slot.
  4. Every other `/api/*` fetch the dashboard JS makes
     immediately afterwards queues behind that SSE.
  5. The dashboard sits at "Loading…" forever → white page.
  6. After ~4 s the `/api/system/auth-status` request finally
     resolves (it was queued behind the SSE), notices
     `origin === "broker"`, and closes the SSE. By then the
     race has already happened. Safari doesn't recover the
     queued requests.

Fix: never open SSE at all when `IS_BROKER_VIEW` is true (we
know from the URL hostname, no need to wait for the API to
confirm). Broker visitors poll every 5 s via the existing
fallback timer, which dodges the trap entirely. Local LAN
keeps SSE. Works fine on a fresh connection without an
intermediate tunnel.

The Caddy access log surfaced this: every SSE attempt logged
`error: "reading: context canceled"` because Safari aborted
the in-flight EventSource almost immediately, but the damage
to the connection pool was already done.

## [0.1.3] · 2026-05-18

### Added. Weather-aware tint on the Right now tile
The Right now panel now picks a subtle background gradient + border
colour from the current WMO weather code so a sunny day reads
warm-yellow, an overcast day reads neutral grey, rain reads slate
blue, snow reads icy pale blue, and a thunderstorm reads deep
purple. Clear conditions also branch on `is_day` (golden during
the day, indigo at night).

Mood lighting only. All variants cap at &lt;=10% alpha so the
panel still reads as part of the dashboard's tinted-tile family
and doesn't fight the actual numbers. New `wx-bg-*` classes
swap with an 800 ms CSS transition so the tint shifts gracefully
when the weather changes between polls.

## [0.1.2] · 2026-05-17

### Security. Cyber backlog clear-down

Three security tickets boxed off in one ship.

**#148. Fixed: sso_secret in-memory drift after Settings save.**
CloudService cached `self.cfg = cfg` (a CloudCfg reference) at
construction. When the Settings → Cloud → Save handler did
`config.cloud = new_c`, it reassigned the parent's pointer to a
freshly-built CloudCfg. But CloudService still held the OLD
reference. Heartbeats firing after a save would mutate and
persist the stale object while `/sso` + broker forward-auth read
the new one, drifting the in-memory SSO secret away from what
appliance request handlers actually used.

Fix: CloudService now holds the parent `Config` and accesses
`self.cfg` via a property that resolves through `config.cloud`
on every read. Same call site change in both the scheduler and
the post-pair hot-start. Legacy callers passing a bare CloudCfg
still work. The property falls back to a direct reference.

**#145. Added: broker DDoS hardening.**
Two layers added on top of the existing 600/min/IP cap on
forward-auth:
- Per-IP cap tightened to 300/min (5/s. Still plenty for
  legit users with many tabs, halves the headroom a single
  attacker has to play with).
- Per-slug cap of 1200/min added (20/s/appliance). One
  harvested slug being hammered from a botnet can no longer
  starve other appliances' tunnel ingress even when every
  contributing IP stays under its per-IP limit.
- Caddyfile: `request_body { max_size 4MB }` on the broker
  block as a slow-POST defence. Legit dashboard POSTs are KB-
  range; cloud-backup upload uses a different host so its
  500 MB ceiling isn't constrained.

**#143. Added: sso_secret encrypted at rest cloud-side.**
New `secrets_kek` module wraps appliances.sso_secret in
AES-GCM, key derived from `SESSION_SECRET` via HKDF with a
purpose-specific `info` salt. Encrypted values carry a `v1:`
prefix so legacy plaintext rows pass through unchanged and get
lazily re-encrypted on next write through the heartbeat, mint,
or pair paths. Migration 0029 widens the column from 64 → 160
chars to fit `v1:` + base64(nonce + ciphertext + tag) (≈83
chars) with headroom for future envelope schemes.

Three read sites updated to decrypt before HMAC operations:
heartbeat response, /api/sites/{id}/sso mint, /api/internal/
can-access broker forward-auth signature. Pair-exchange decrypts
before returning to the appliance (which still stores plaintext
hex in its local config.yaml. Encryption is cloud-side only).

What this protects against: a future cloud Postgres move to
managed hosting (RDS / Aiven / Neon) where operators on the
managed plane would otherwise see plaintext sso_secrets in
`SELECT * FROM appliances`. Today's same-host deployment isn't
affected either way.

What it doesn't protect against: anyone with read access to
both Postgres AND `SESSION_SECRET` (e.g. root on the VPS or the
running cloud container). The KEK lives next to its ciphertext;
this is a step toward proper KMS-backed encryption, not the end
state.

## [0.1.1] · 2026-05-17

### Fixed. Hourly weather strip ran dry after early evening
The Open-Meteo provider requested `forecast_days: 1`, meaning the
hourly array only contained timestamps within the current local
calendar day. By ~18:00 the rolling 12-hour preview was already
showing 4 future hours; by 22:00 it was down to 1 (or zero, since
the cutoff filter also rejected the just-passed hour). Result:
the Right-now tile's hourly strip looked broken late in the day.

Bumped to `forecast_days: 2` so the API returns the next 48 hours
of forecast · _HOURLY_KEEP=12 still caps what we render. ~50
hourly rows on the wire (vs ~24) is negligible payload growth.

## [0.1.0] · 2026-05-17

### Milestone release. What shipped today

A long single-day burst rolled together as the first 0.1
milestone. No new code beyond the v0.0.99 tag; this is a
version-only bump to round-trip past 0.0.99 cleanly without
the awkward 0.0.100. The patch-tag chain below is the summary.

**Local appliance**
- Charger value-add stats on the device-detail page: lifetime
  kWh delivered, today active, 24h charging-state ribbon
  (v0.0.85)
- Device-detail page recovers from a hard refresh that races
  the first snapshot (v0.0.86)
- Victron AC charger detail page: friendly icon, no `slave null`,
  hide empty fields, NO_ERROR case-insensitive (v0.0.87)
- Rename devices from the UI (display-name override; original
  label stays as the immutable storage key) (v0.0.88)
- One-click backup &amp; restore on Settings. Full SQLite +
  config + password tarball (v0.0.89)
- Weekly local rotating snapshots (default-on; keep last 4)
  (v0.0.90)
- Cloud backup upload + restore. Pro/Installer tier only
  (v0.0.91), with UI fixes to stop letting customers enable
  it without paying (v0.0.92, v0.0.93) and a pairing-preserve
  fix so restore-from-cloud on a fresh box doesn't clobber
  the new pair's tokens (v0.0.94)
- Kiosk Exit button no longer breaks out of the broker view
  into full chrome (v0.0.95)
- Today tile gains Stored + SoC envelope cells (v0.0.99)

**Cloud SaaS (wattpost.cloud)**
- "Today in" now counts AC charger + DC-DC contributions, not
  just PV (v0.0.96)
- Per-site cards gain Stored cell + per-source breakdown
  ("1.7 PV · 0.9 AC") (v0.0.97)
- Per-site cards gain SoC envelope subline, time-to-empty /
  time-to-full ETA, charger-state pill (v0.0.98)
- Rules page: layout tidy + 6 starter templates as one-click
  chips (also v0.0.97 ship)

**Cloud-side filed for next session**
- #164 Backups view on appliance detail page
- #165 "Take backup now" from cloud
- #166 "Restore from cloud" rescue flow

**Infrastructure**
- CI concurrency on all tag-fired workflows so back-to-back
  tags coalesce cleanly. No more pi-gen container collisions
  and the stream of failure emails that came with them
- Stuck `pigen-builder` Docker container cleared from the
  self-hosted runner; v0.0.94 was the first run to pick up
  the fix

## [0.0.99] · 2026-05-17

### Added. Appliance Today tile: Stored + SoC envelope
Bringing two of the new cloud-card cells home to the local
dashboard, where the same questions matter just as much:

- **Stored**. Bank net today (`↑ 1.84 kWh` or `↓ 500 Wh`).
  Uses `today_aggregate.bank_net_today_wh` when present;
  falls back to (sources − load) on older builds.
- **SoC today**. Today's min – max envelope
  (`28.4 – 70.1 %`). Powered by a new
  `GET /api/today/soc-envelope` endpoint that wraps the
  `bank_soc_minmax` method shipped in v0.0.98 for cloud
  heartbeats.

Both render alongside Charged / Peak / Load in the existing
Today tile. No new screen real-estate needed.

## [0.0.98] · 2026-05-17

### Added. Cloud dashboard cards: SoC envelope, ETA, charger pill, forecast
Five small data surfaces I previously had on the deferred list,
shipped together:

1. **Today's SoC envelope**. Small grey subline under the SoC cell:
   "today: 65.2 – 70.1%". Answers "did the bank get critically low
   overnight?" without opening History.
2. **Time-to-empty / time-to-full**. Subline under Net now:
   `~ 8h 20m to empty` when discharging, `~ 3h 15m to full` when
   charging. Powered by the same rolling-hour load average as the
   local /api/runtime_forecast so the two are consistent. Hidden
   when bank is idle (-5 .. +5 W).
3. **Charger state pill**. Coloured chip in the card head: orange
   `bulk`, yellow `absorption`, green `float`, red `equalize`, grey
   `storage`. Tells you whether the system is actively pushing or
   just trickling.
4. **Tomorrow's PV forecast**. Already shipped in extras, already
   rendered when present; no UI change required (rendered as
   "Tomorrow X.X kWh" in the existing weather row once Solcast or
   Open-Meteo forecast is available).
5. **Active alerts**. Already rendered as a chip when alert_count
   > 0; no change.

Three new heartbeat extras fields ship from the appliance:
  - `soc_min_today_pct`
  - `soc_max_today_pct`
  - `time_to_empty_min` (when discharging > 5 W)
  - `time_to_full_min`  (when charging > 5 W)
  - `charger_state`     (first device that reports one. Typically
    the MPPT or AC charger)

New `Store.bank_soc_minmax(since, until)` to power the envelope.
All backwards compatible. Older cloud builds ignore the new
fields, older appliances just hide the new UI bits.

## [0.0.97] · 2026-05-17

### Added. Cloud dashboard: "Stored today" + per-source breakdown
Two follow-ons to v0.0.96's sources_today_wh fix, both surfacing
information the appliance already had but the cloud didn't show:

1. **Stored today**. New third cell on each per-site card and on
   the fleet summary strip. Signed: `↑ +1.84 kWh` (bank gained
   today) or `↓ 0.5 kWh` (bank depleted). The "did my system win
   today?" headline you previously had to compute by subtracting
   in − out. Powered by `bank_net_today_wh` from the appliance's
   today_aggregate; for legacy appliances the per-site card falls
   back to (in − out), the fleet aggregate suppresses the cell
   entirely to avoid misleading zeros.

2. **Source mix subline**. Today in now shows a small grey
   second line like "1.7 PV · 0.9 AC" when more than one source
   contributed today. PV-only installs are unchanged. Tells you
   at a glance whether solar or mains topped you up. The
   difference between a great solar day and a quiet one carried
   by the AC charger.

Three new heartbeat extras fields ship from the appliance:
  - `ac_charger_today_wh`
  - `dcdc_today_wh`
  - `bank_net_today_wh`

(`pv_today_wh` and `load_today_wh` are unchanged; everything is
backwards-compatible with older cloud builds.)

## [0.0.96] · 2026-05-17

### Fixed. Cloud "Today in" silently excluded AC charger + DC-DC contributions
The cloud-side dashboard (multi-site summary + per-site card) was
reading `pv_today_wh` from heartbeat extras and labelling the
total as "Today in". For PV-only installs that's correct, but on
multi-source installs (Victron Blue Smart AC charger, Orion DC-DC
alternator charger) the AC charger and DC-DC contributions were
invisible. A bench appliance showing 1.7 PV + 0.9 AC = 2.6 kWh
actually delivered today rendered as "1.7 kWh" on the cloud tile.

Now the appliance also ships `sources_today_wh` (all sources
summed; the value already lived in `today_aggregate` since v0.0.81
but wasn't surfaced over heartbeat). Cloud dashboards prefer it
and fall back to `pv_today_wh` for appliances on older builds,
so no upgrade ordering hazard.

## [0.0.95] · 2026-05-17

### Fixed. Kiosk "Exit" button breaks out of broker view into full chrome
Follow-on to #150. That fix hid Exit for anonymous kiosk-token
visitors but the button was still available on cloud-broker
sessions (`xyz.wattpost.cloud`), which let an authed customer
exit out of the SoC kiosk into the full Settings / Devices /
Setup chrome. Chrome that's owned by app.wattpost.cloud, not by
the appliance's broker hostname.

Now: hide Exit + neutralise its click when the page is loaded
over a `*.wattpost.cloud` or `*.wattpost.io` hostname (the
broker pattern). Direct local access (LAN IP, wattpost.local)
keeps the button. Van/cabin operators legitimately need to
swap between kiosk and dashboard from a single device.

New `IS_BROKER_VIEW` constant in app.js for any future UI bits
that should be broker-aware.

## [0.0.94] · 2026-05-17

### Fixed. Restore now preserves the appliance's pairing
Before this fix, restoring a backup taken on a different install
(the actual rescue scenario. SD card died, fresh box paired to
same account, restore the old DB) would clobber the fresh pair's
`cloud.bearer_token`, `cloud.sso_secret`, `cloud.tunnel_*` etc.
with the dead appliance's. Result: 401 on next heartbeat, SSO
redirects fail, CF tunnel is wired to the wrong tunnel id.

`_stage_and_swap` now reads the live `cloud:` block from the
current config.yaml BEFORE applying the swap and re-injects it
on top of the restored config. Same-appliance rollback is
unaffected (the preserved values match what was in the backup
anyway). The local-UI password files are also no longer
overwritten when one already exists on disk. Operator's
current password on the fresh box wins over an old one they may
not remember.

### Tracked
Three new backlog items for the cloud-side backup story:
  - #164 Cloud UI: backups view on appliance detail page
  - #165 Cloud UI: "Take backup now" button (queues backup_now
    ApplianceCommand)
  - #166 Cloud UI: "Restore from cloud" for fresh-appliance
    rescue (queues restore_from_cloud:{id} ApplianceCommand)

## [0.0.93] · 2026-05-17

### Fixed. Cloud-upload toggle ignored tier
Toggle let Hobby-tier (and unpaired) accounts flip cloud_upload to
true even though every subsequent upload would 402. Now:

- Cloud-toggle endpoint pre-flights against the cloud and rejects
  enable with explicit 402 / 503 / 401 when the account isn't
  eligible. Defence in depth. UI can't bypass via curl.
- Settings UI always probes `/cloud-list` on render, so even before
  the toggle is touched the button reflects reality: greyed out and
  labelled "Upgrade to enable" for Hobby, "Pair to wattpost.cloud
  first" for unpaired, "Cloud account not ready" while the cloud
  is on an older build. Clicking the Hobby variant jumps straight
  to the upgrade page.

## [0.0.92] · 2026-05-17

### Fixed. Cloud backups UI was customer-hostile
Two papercuts from v0.0.91:

1. The cloud-backups blurb told customers to edit `config.yaml`
   to turn the feature on. Now there's an in-UI
   **Enable cloud upload** / **Disable cloud upload** button that
   flips `backup.cloud_upload` for them and re-wires the running
   service in-process &mdash; no restart, no YAML.
2. When the cloud was on an older build that didn't yet have the
   new ingest endpoint, the appliance proxied the raw upstream 404
   straight to the page (literal `{"status_code":404,"detail":"Not Found"}`
   in a red box). Replaced with: "Cloud account is on an older build
   that doesn't accept backup uploads yet" &mdash; explicit, with no
   raw error JSON. Same for 402 (tier required) and 503 (not paired).

New endpoint: `POST /api/system/backup/cloud-toggle {enabled: bool}`.

## [0.0.91] · 2026-05-17

### Added. Cloud backup upload + restore (Pro/Installer tier)
Phase 2 of the backup story (#146). When `backup.cloud_upload: true`
in config.yaml on a paired Pro/Installer appliance, every scheduled
local snapshot is also pushed to wattpost.cloud and retained per
the configured `cloud_keep_count`.

Cloud side (new):
  - migration 0028: `appliance_backups` table (metadata only; bytes
    on the VPS filesystem under `/srv/wattpost-cloud-backups` via
    bind mount added to `vps-infra/docker-compose.yml`)
  - `POST /api/internal/backups/upload` (bearer-auth, Pro/Installer
    gate, 402 with explicit upgrade copy for Hobby tier, capped at
    500 MB per upload, rate-limited 6/h per IP)
  - `GET /api/internal/backups/list`
  - `GET /api/internal/backups/{id}/download`
  - `DELETE /api/internal/backups/{id}`
  - per-appliance retention enforced on upload; hard cap of 12
    backups per appliance regardless of customer request

Appliance side (new):
  - `solar_monitor/backup/cloud_uploader.py`. HMAC-bearer POST to
    cloud after each successful local snapshot
  - Wired automatically as the BackupService's `cloud_uploader`
    hook when both `cloud_upload: true` and the appliance is paired
  - `GET /api/system/backup/cloud-list`. Proxy showing the
    appliance's cloud-side rows
  - `POST /api/system/backup/cloud-restore/{id}`. Downloads from
    cloud + feeds straight into the local restore swap

Settings UI: "Cloud backups (Pro)" subsection mirrors the local
table with per-row Restore buttons that fetch from cloud + apply.

### Operator notes for the next vps-infra deploy
- Create `/srv/wattpost-cloud-backups` on the VPS (the bind mount
  target) before bringing the cloud back up:
  `sudo mkdir -p /srv/wattpost-cloud-backups`.
- Migration 0028 runs automatically on container start.

## [0.0.90] · 2026-05-17

### Added. Scheduled local backups (Settings → Backup &amp; restore)
A weekly rotating snapshot loop now runs alongside the on-demand
download added in v0.0.89:

- Every `interval_hours` (default 168 = weekly) the daemon writes
  a `wattpost-auto-YYYY-MM-DD-HHMMSS.tar.gz` to `<db_dir>/backups/`
  (override with `backup.dir` in config.yaml).
- Oldest snapshots beyond `keep_count` (default 4) are pruned
  after each successful capture.
- Boot-anchored: on start the loop checks the newest snapshot's
  age and either fires immediately or waits out the remainder of
  the interval, so a Pi that reboots every couple of days still
  gets the configured cadence without drift.
- Same archive format as the on-demand download → any scheduled
  snapshot can be fed straight into the restore flow.

Settings UI shows the current cadence, last-run age, next-run
ETA, plus a table of on-disk snapshots with per-row Download /
Delete and a "Run backup now" button.

New endpoints, behind the same session-cookie auth:
  - `GET    /api/system/backup/schedule`   . Status + listing
  - `POST   /api/system/backup/run-now`    . Manual trigger
  - `GET    /api/system/backup/file/{name}`. Download one
  - `DELETE /api/system/backup/file/{name}`. Operator cleanup

New optional config block (defaults preserve the previous
"no-scheduled-backup" behaviour iff you explicitly disable it ·
default is enabled at weekly):
```yaml
backup:
  enabled: true            # default true
  interval_hours: 168      # weekly
  keep_count: 4
  dir: ""                  # "" → <db_dir>/backups
  cloud_upload: false      # Pro/Installer tier, see next release
  cloud_keep_count: 4
```

### Coming next
Phase 2 of this work. Pushing each new local snapshot to
`wattpost.cloud` for Pro/Installer customers so it survives an
SD-card death. Lands in a separate release. The `cloud_upload`
config field is staged but the appliance-side uploader is a no-op
stub until the cloud-side ingest endpoint lands.

## [0.0.89] · 2026-05-17

### Added. Backup &amp; restore (Settings → Backup &amp; restore)
One-click download of a tar.gz containing:
  - the full SQLite database (history, samples, devices, alerts,
    kiosk tokens, web sessions). Taken via SQLite's online-backup
    API so it's safe to download mid-poll without locking writers
  - `config.yaml` (devices, transports, alert rules, schedules)
  - `web-password.hash` (local-UI password)
  - a `MANIFEST` recording version + capture timestamp

Restore takes the same archive back, validates it (must be a real
tar.gz, must contain a SQLite file that passes `PRAGMA
integrity_check`), atomically swaps everything into place, and
re-execs the daemon. The pre-restore config is kept alongside as
`config.yaml.restored.bak` so a bad restore is reversible.

Two new endpoints, both behind the same session-cookie auth as
the rest of Settings:
  - `GET /api/system/backup` → streams the archive
  - `POST /api/system/restore` → accepts the raw `.tar.gz` body

UI lives next to the existing Diagnostics block on the Settings
page. Download / Restore-from-file… buttons + a destructive-action
confirm before any swap, and the dashboard auto-reloads once
`/api/health` comes back after the restart.

## [0.0.88] · 2026-05-17

### Added. Rename devices from the UI
Open a device from Devices → click the pencil next to the title →
type a new display name → save. The original label is still the
storage key (history, samples, alerts, exporters all reference it)
so renaming is non-destructive. No migration, no orphaned data,
clear the name and you're back to the original. The detail-page
meta line shows the real underlying label as a chip when a custom
name is in use, so you can always see what's what.

- New `POST /api/devices/{label}/display-name` endpoint (body
  `{display_name: "…"}`. Pass empty or null to reset).
- New `device_meta.display_name` column (schema migration v1).
- Device cards on the Devices page now show the display name.
- BLE-only devices (no Modbus slave) no longer show `slave null`
  in the device card. Show the vendor name instead.

## [0.0.87] · 2026-05-17

### Fixed. Victron AC charger device page rendering
- Header showed `slave null` for BLE-only devices (no Modbus slave).
  Hide the slave segment when `slave_id` is unset.
- The "?" question-mark icon next to the device title now resolves
  to a proper glyph for `ac_charger`, `dcdc`, `dcdc_xs`, `bms`,
  and `load_disconnect` kinds (KIND_ICON entries were missing).
- Hide the AC INPUT and TEMP cells when the BLE advertisement
  doesn't carry those fields (Blue Smart IP22 doesn't report AC
  input current or temperature). They were rendering as bare
  "· A" / "· °C" rows.
- Hide the ERROR cell when `charger_error` is `"NO_ERROR"`
  (case-insensitive. Victron returns it uppercase, the old check
  only matched lowercase).

## [0.0.86] · 2026-05-17

### Fixed. Hard refresh on a #/device/<label> page lost the device
On a cold load (hard refresh or paste-the-URL), the router fired
`renderDeviceDetail()` before the first snapshot had populated the
`devices` array, so the page stuck on "No device named …" until you
clicked Back. `applySnapshot()` now re-renders the device-detail
route if the placeholder is showing and the requested device has
since arrived.

## [0.0.85] · 2026-05-17

### Added. Charger value-add stats on the device-detail page
Open a Victron AC Charger or any MPPT/charge-controller from
Devices and the new **Charger stats** panel appears below the
hero strip:

- **Lifetime delivered (kWh)** + **today delivered** odometer
  tiles. Integrated from the device's stored power samples since
  first poll.
- **Active today**. Total seconds today the charger was
  meaningfully on (power > 5 W, so sleep ticks don't pad it).
- **24-hour charging-state ribbon**. Single horizontal bar
  showing the day's progression through bulk (orange) →
  absorption (yellow) → float (green), with hover tooltips that
  call out each segment's duration.
- **Per-state breakdown legend** · "Today: 45m bulk, 2h abs,
  5h float" so you can see at a glance what a full charge cycle
  looked like and whether the charger ever made it to float.

New endpoint `GET /api/devices/{label}/charger-stats` powers all
of the above. Auto-detects the right power metric from the
device's latest fields (`output_1_power_w` for AC chargers,
`pv_power_w` for MPPTs).

Dedicated `buildAcChargerDetail` renders Victron AC chargers
with all three output channels surfaced (Blue Smart IP65 3-bank
models. Engine / aux / start) plus AC input current, temperature
and any active charger error.

## [0.0.81] · 2026-05-17

### Fixed. Today's LOAD always showed 0 Wh on multi-source installs
`today_aggregate` had two bugs that conspired to hide load:

1. PV "today" came from a hardcoded `device = 'rover_mppt'` SQL
   query. But the actual MPPT device label varies (most installs
   it's just `charge_controller`). For everyone except a vanishing
   subset of legacy installs, `pv_today_wh` was always 0.

2. The load formula `pv + discharged − charged` only counted PV
   as energy in. AC chargers, DC-DC converters, anything else
   feeding the bank was invisible. On an install with the bank
   net-charging (charged > discharged), the formula computed
   negative and clamped to 0 → "Load today: 0 Wh" while the live
   power-flow strip clearly showed real load happening.

Rewrite uses the energy-balance identity correctly:
  `load_out = sources_in − bank_net`

PV `sources_in` reads the device's own daily counter (Renogy
`energy_today_wh`) because 60 s-poll trapezoid integration
under-counts fast PV peaks by ~40% on a real install. AC charger
and DC-DC don't have a device-side daily counter so they're
integrated (good enough for "is the load real" sanity, less
accurate than the device counter for big totals).

New `/api/today` fields:
  - `sources_today_wh`. Total energy into the bus today
  - `ac_charger_today_wh`, `dcdc_today_wh`. Per-source breakdown
  - `pv_today_wh`. Unchanged name, now correct

The "Today" headline kWh on the dashboard now uses
`sources_today_wh` instead of just PV. A Victron-only or
AC-charger-only install reads correctly instead of "0.0 kWh".

Verified on Ritual North's install: was showing 0 Wh load, now shows
338 Wh. Matches the ~100 W background draw over the hours
his Victron has been online.

### Known minor. Hero vs Flow 12 W sampling skew
The hero "Net power" tile and the flow strip's "Battery bank"
read from the same `devices` array but render on slightly
different ticks. A live install that's actively MPPT-tracking
will show small (~5-15 W, ~0.1 % SoC) drift between the two
tiles for a poll cycle. Cosmetic; both numbers are correct for
the snapshot they were taken from. Future task to lock both
renders to a single snapshot.

## [0.0.80] · 2026-05-17

### Fixed. Victron AC charger labelled "Other source" in flow strip
The dashboard's Power-Flow strip mapped device kinds to source/load
tiles via the `FLOW_MAPPING` table. None of the Victron-specific
kinds shipped in #112/#118 (ac_charger, dcdc_xs, bms,
load_disconnect) were in that table, so the Victron's real
output_1_power_w reading was ignored and the energy-balance
inference kicked in instead. Showing "Other source · estimated"
for the wattage gap. Ritual North saw 232 W Solar + 104 W "Other source"
adding to the 336 W charging his bank, while the Victron driver
was actually reading 202 W → the missing ~100 W was load draw
the inference subtraction couldn't account for.

Added flow mappings for:
  - ac_charger → "AC Charger" tile using output_1_power_w
  - dcdc, dcdc_xs → "DC-DC" tile using output_power_w
  - bms → battery (same as smart_battery / shunt)
  - load_disconnect → no flow tile (state-only telemetry, shows in
    device cards)

Result: the Victron now appears as its own correctly-named source
tile with real wattage. The inferred "Other source" only fires
when there's a GENUINE energy gap we can't measure. Which is
its actual job.

Multi-output AC chargers (output_2 / output_3 in addition to
output_1) only render output 1 right now. Rare in van/cabin
installs; expand if a customer requests it.

## [0.0.79] · 2026-05-17

### Fixed. Victron transports left no device polled
"Pair Victron" added a transport row to config.yaml but never the
corresponding device row. The transport happily decoded
advertisements but the poller had nothing to bind the data to, so
the dashboard showed no Victron metrics. The wizard's "Scan for
devices" button is Modbus-only (sweeps slave IDs) so users had no
path to add the device manually either. Stuck-without-data state.

Three coordinated changes:
  - `DeviceCfg.slave_id` is now `int | None = None` instead of a
    required int. Victron BLE Instant Readout devices are MAC-
    addressed at the transport layer; demanding a fake slave_id
    just to satisfy the schema was wrong.
  - `add_transport` for `ble_victron_advertise` now also appends
    a device row to config.yaml, mapping the victron-ble class
    name (AcCharger / SolarCharger / BatteryMonitor / OrionXS /
    DcDcConverter / SmartLithium / LynxSmartBMS / SmartBattery
    Protect) to the WattPost device_kind. Falls back to
    `ac_charger` if class is unknown.
  - `AddTransportRequest.device_class` field added; the wizard's
    Pair button now stashes the class from scan results and the
    Save handler passes it through.

Customer impact: adding a Victron via the wizard is now actually
one-click. Pair → key → Save → data flowing. No second config
edit needed. Existing customers who paired before this release
need to add a device row manually OR delete + re-pair via the
new wizard flow.

Hit by Ritual North pairing his BSC IP22 12/15 on the new VM appliance.

## [0.0.78] · 2026-05-17

### Fixed. Victron transport perpetually reported OFFLINE
The `/api/setup/transports` endpoint determined "open" state by
checking `transport._client.is_connected`, which only Modbus-style
transports have. The passive Victron transport
(`ble_victron_advertise`) has no GATT client to "open". It just
registers a listener with the shared scanner and waits for
broadcasts. So a perfectly healthy Victron transport that was
actively decoding the customer's device showed OFFLINE in the
wizard's transport list. Hit by Ritual North pairing his BSC IP22.

Class-aware open-state probe added: for ble_victron_advertise we
check `_registered` (listener attached to shared scanner) AND
`_latest_at` (last advertisement was within 60s). Modbus-style
transports keep the old `_client.is_connected` path. Both branches
documented inline; a future Transport.is_connected property on the
base class would let us drop the per-class switch.

## [0.0.77] · 2026-05-17

### Fixed. Victron encryption-key form unusable on mobile
The "Pair Victron" form had its key input + label in a horizontal
flex row with the label at `min-width:7rem`. On a phone-sized
viewport that left maybe 3cm for the input field, with no easy way
to see what was typed and a tiny Save button below. Customer
couldn't actually save the encryption key from VictronConnect.

Rewrite of the form:
  - Label stacks above the input (vertical, full-width)
  - Input is `type="text"` not `password` so iOS doesn't truncate
    for autocomplete suggestions; key is a long random hex string
    that's already on the user's phone in VictronConnect, password-
    masking adds no security
  - `inputmode="latin"`, `autocapitalize="none"`, `autocorrect="off"`,
    `spellcheck="false"` so iOS doesn't fight the user
  - `pattern="[0-9a-fA-F]{32}"`, `maxlength="32"` for browser-level
    validation hints
  - Save button is 2.4rem tall, full-width-flex on narrow screens
  - Client-side validation strips spaces / colons / dashes the user
    might paste from notes, lowercases, then matches `/^[0-9a-f]{32}$/`.
    Bad input shows an error in the form, no API round-trip.
  - Updated instructions to reflect VictronConnect's actual menu
    path (Settings → Product info → Instant readout encryption data,
    not just "Show device key")

### Known limitation
There's still no way to edit an existing Victron transport's key
from the transport list. If you added it with the wrong key (or no
key), trash-icon-delete + rescan + re-add. PATCH endpoint on
existing transports is a backlog task.

## [0.0.76] · 2026-05-17

### Security. Kiosk-share URL no longer leaks dashboard chrome (#150)
The public kiosk URL (`/kiosk?key=<token>`) captured the token into
KIOSK_KEY_PARAM in memory, then the "Exit Kiosk" button just changed
the SPA hash to `#/`. Token stayed in memory, every subsequent api()
call appended `?key=`, the Caddy @kiosk_open bypass + appliance
kiosk allow-list happily served data. So a kiosk-share visitor
could exit into the dashboard chrome and see all the panels that
the kiosk allow-list happens to cover. Mutations + Settings + sensitive
endpoints were still gated, but the "share this link and they see the
kiosk only" UX promise was broken.

Fix:
  - Hide the Exit Kiosk button entirely when KIOSK_KEY_PARAM is set
    (the visitor has no real session, exit is meaningless to them).
  - Belt-and-braces: if the button is somehow clicked while a key is
    still in memory, do a FULL page reload to `/` (not a hash change),
    which drops KIOSK_KEY_PARAM. api() calls then go through normal
    auth and 401 anything the user isn't entitled to.

Found in the pre-launch pentest, Ritual North spotted it manually.

### Security. Cloud-side hardening (#155, #156)
Ship in the cloud (auto-deploys to wattpost.cloud on push), documented
here for visibility:
  - #155: /healthz/deep no longer leaks raw user / appliance counts.
    Returns a boolean `checks.heartbeats = "ok" | "stale"` instead.
    Public /status page reworked to show health states, not numerics.
  - #156: /api/heartbeat now rate-limited to 60/5min/IP. Real
    appliances heartbeat 1/5min so this is 60× headroom; brute-force
    against bearer tokens hits a wall quickly.

## [0.0.75] · 2026-05-17

### Fixed. Appliance sessions wipe on container restart (#149)
The local-auth session dict lived in process memory only, so every
restart (Update now, Settings → Restart daemon, customer power-cycle)
silently logged everyone out. The SPA's cached "you're authed" state
then disagreed with the empty server-side store and any state-changing
API call returned "login required". Surfaced via a customer reporting
that "Send heartbeat" failed even though Settings was open.

Sessions now persist to /etc/wattpost/sessions.json (same config dir
as web-password.hash). Read-through cache: every issue/revoke writes
the dict to disk via atomic write-temp-then-rename. Module-import
loads the file back, expired entries dropped on load. Storage cost
is trivial (a typical install holds a handful of sessions, ~100 bytes
each). Disk write failures degrade to in-memory-only with a warning
log. Never breaks login.

Side benefit: also fixes the related #148 sso_secret divergence ·
restart-to-recover is no longer needed because no state is held only
in memory.

### Security. Cloud-side hardening (#152, #153, #154)
These ship in the cloud (auto-deployed to wattpost.cloud on push to
main), not the appliance. Documented here for visibility:
  - #152: signup is now always-202 regardless of email existence;
    eliminates user-enumeration signal.
  - #153: password policy = 10+ chars, ~50-entry common-password
    blocklist, HIBP k-anonymity check.
  - #154: /schema (OpenAPI) gated behind a SESSION_SECRET-derived
    randomised path in prod; default `/schema` in dev only.
Found in the pre-launch pentest.

## [0.0.74] · 2026-05-17

### Fixed. Dashboard stuck at "connecting" when accessed via cloud broker on iOS Safari
On the broker URL (`<slug>.wattpost.cloud`), the dashboard would load
the shell + tabs but every tile stayed empty and the status pill
stayed at "connecting…" forever. Confirmed in headless Chromium that
the JS code was fine. Render succeeds when the page is allowed to
breathe. The trap was iOS Safari's HTTP connection pool: a long-lived
EventSource through Cloudflare Tunnel holds a connection open, and
Safari serialises subsequent /api/* fetches behind it, so refresh()
never resolves and the pill never flips.

Fix: in `wireSignout`'s auth-status callback, detect `origin === "broker"`,
close any open EventSource, and start the 5 s polling fallback instead.
LAN access keeps SSE (fresh local connection, no CF in the path, no
pool starvation). The reroute is transparent. Same data shape via the
same `applySnapshot`, just delivered by REST poll instead of stream.

Caught while writing a headless-Chromium reproduction with synthesized
broker headers (CF-Ray + freshly-minted HMAC). Before fix: page navigation
timed out waiting for networkidle, status pill stuck on initial HTML
default. After fix: clean 200, pill flips to "Healthy", real data renders.

## [0.0.73] · 2026-05-17

### Fixed · "Idle" shown when slow-charging from PV
The 1.5 A "Idle" guard added in v0.0.70 was applied symmetrically
to both charge and discharge currents. That broke the charging
case: a battery taking +1 A from a low-output MPPT was labelled
"Idle". But it's charging, just slowly. Customer-confusing:
"Idle" implies nothing is happening, when in fact the bank is
recovering from a low SoC.

Made the guard discharge-only:
  - charging at <0.2 A → "Charging · trickle"
  - charging at ≥0.2 A → "X h until full" (existing math)
  - discharging at <0.1 A → "Idle"
  - discharging at <1.5 A → "Light load · 0.XX A draw"
  - discharging at ≥1.5 A → "X h until empty" (with 10% reserve)

The "Light load" variant also surfaces the actual draw current, so
the user can see how close they are to the standby threshold and
why we're declining to project hours.

## [0.0.72] · 2026-05-17

### Fixed. Battery health endpoint 500'd on any window > 6 hours
`battery_health_aggregate` referenced an `avg_value` column when
falling back to the rollup tables (samples_1min / samples_1hour /
samples_1day). But those tables store the averaged value in a
column called `avg`. SQLite returned "no such column: avg_value"
and the endpoint 500'd for the default 30-day window (the only
window the UI ever requests). Customers saw a permanently broken
Battery health tile and. Because the JS dashboard refresh fires
the same call on boot. A blank dashboard until the request
eventually settled. Net effect: feature shipped in #109 silently
broken for everyone on the rollup window. Caught while debugging
a "blank dashboard after broker login" report.

## [0.0.71] · 2026-05-17

### Fixed · "Remaining" tile showed instant rate, not realistic forecast
The forecast-aware overlay ("Forecast: ~6 h until 10% at 19:30")
that walks PV forecast vs avg load was supposed to handle the
"2d 5h until empty" misleading-instant-rate case. But
`/api/runtime-forecast` was returning 404 because the function
existed in api/app.py but was never added to the route_handlers
list. Same bug class as #109. JS silently hid the forecast line
on the 404, leaving only the naive instant-rate reading. Pure
registration fix.

## [0.0.70] · 2026-05-17

### Changed. Saner "until empty" estimate
At standby loads (sub-1.5 A net), the naive `capacity ÷ current`
estimate produced laughably long times ("2 d 5 h until empty"
when the server was just idling). Bumped the "idle, don't show
runtime" threshold from 0.5 A to 1.5 A and subtract a 10 %
reserve from the headline number, so the displayed figure
matches what the user can practically use.

## [0.0.69] · 2026-05-17

### Fixed. Battery health panel was rendering empty (route never registered)
The `/api/battery-health` handler existed in api/app.py since #109
shipped, but I never added it to the Litestar `route_handlers`
list. Result: panel-battery-health on the dashboard called it,
got 404, JS gracefully fell back to "·" placeholders, panel
looked permanently broken. Added the registration; cycles +
lifetime + window cycles + SoC residency histogram now populate
from the BMS + heartbeat history.



## [0.0.64] · 2026-05-17

### Changed. Removed Sign In / Sign Out buttons from appliance header
Both were ugly clutter. The auth model since v0.0.58 only gates
Settings + Setup; tapping either bounces to /login automatically.
A Sign In button at the top was redundant. Sign Out moved inside
Settings → System (only visible when actually signed in). That's
where you'd realise you want to drop the session anyway.

Dashboard / History / Devices / Kiosk / Docs all stay completely
anonymous-readable on LAN. No chrome, no buttons, just data.

app.js v=130, sw.js CACHE_VERSION bumped.

## [0.0.63] · 2026-05-17

### Fixed. CRITICAL: db_path field was missing from Config, so v0.0.60 fix did nothing
v0.0.60 added `cli._resolve_db_path` to honour `config.db_path`
over the CLI default. But `Config` (msgspec.Struct) didn't declare
a `db_path` field, so msgspec silently dropped the YAML value
and `getattr(config, 'db_path', None)` was always None. Result:
the resolve helper always fell through to `args.db` and Docker
users were STILL writing the DB to /app/solar-monitor.db
(ephemeral image layer). The v0.0.60 "fix" was cosmetic.

v0.0.63 actually adds `db_path: str = "solar-monitor.db"` to
Config. Default matches the historical CLI default so existing
installs without the YAML key keep their current behaviour
(though all reference configs DO set the key, so Docker users
get the persistent path).

Smoke-tested: container now opens DB at
/var/lib/wattpost/solar-monitor.db (the bind-mount target), WAL
files visible on the host side, data survives restart.

### Changed. Service worker evicts old caches on activate
Was leaving every prior cache version on disk forever. Now
deletes anything that isn't the current CACHE_VERSION on
activate. Belt-and-braces alongside skipWaiting + clients.claim
to keep "stale UI being served from cache" from biting.

## [0.0.62] · 2026-05-17

### Added. Settings → Kiosk share URL panel
Surfaces the per-appliance public share URL the cloud dashboard
already builds, plus a Rotate button for one-click revocation
when the URL leaks. Reads via GET /api/system/kiosk; rotates via
POST /api/system/kiosk/rotate (already shipped in v0.0.61).
Block hides itself when the appliance has no cloud tunnel (no
slug = no public URL).

app.js v=129, styles.css v=103, CACHE_VERSION bumped.

## [0.0.61] · 2026-05-17

### Added. Tokened kiosk share URL (Option C)
The cloud's "Kiosk" button used to copy a raw tunnel URL that
didn't actually work via the internet (HTML loaded but every
data fetch 401'd at the appliance. See earlier analysis). Now:

- Appliance auto-generates `cloud.kiosk_token` (URL-safe 24-byte)
  on first load_config. Persisted to config.yaml + survives
  re-pair (preserved in cloud_admin.py).
- Heartbeat sends `kiosk_token` in extras so the cloud dashboard
  knows it.
- Share URL is now `https://<slug>.wattpost.cloud/kiosk?key=<token>`.
  Goes through the cloud broker; Caddy's forward_auth is skipped
  for /kiosk* + the read-only data endpoints kiosk-mode uses
  (/api/devices, /api/poll_run, /api/today).
- Appliance middleware validates `?key=<token>` against the local
  kiosk_token via `hmac.compare_digest`. Allow-list of GET paths
  the kiosk page actually reads. Strict, no API back-door.
- Kiosk-mode JS captures the `?key=` once at page load + appends
  it to every subsequent /api/* fetch.
- POST /api/system/kiosk/rotate generates a fresh token + returns
  the new share URL. Old token immediately stops working
  (revocation = "I leaked the URL, kill it").

Pre-v0.0.61 appliances haven't shipped a kiosk_token yet; the
cloud dashboard falls back to the legacy direct-tunnel URL (LAN-
only). Updating the appliance + waiting one heartbeat fixes the
share button.

## [0.0.60] · 2026-05-17

### Fixed. CRITICAL: Docker users lost ALL history on every image pull
config.db_path was settable but the daemon completely ignored it.
`cmd_serve` always passed `args.db` (default `solar-monitor.db`)
to build_app, which resolved to `/app/solar-monitor.db` inside
the container. I.e. the IMAGE's ephemeral writable layer, not
the bind-mounted /var/lib/wattpost volume. Every
`docker compose pull && up -d` swapped the image → /app gone →
every metric the user had ever collected, vanished.

`_resolve_db_path` now picks (in order): explicit --db arg →
config.db_path → CLI default. Pi installs are unaffected (their
default db_path lands in /var/lib/wattpost anyway via the
systemd unit). Docker installs with a v0.0.60+ image will now
write to the bind-mounted volume + survive image upgrades.

### Migrated. Legacy in-image-layer DB → persistent path
On startup, if config.db_path points somewhere new but the legacy
./solar-monitor.db exists at the daemon's CWD, the file gets
copied to the new location and the source renamed to
.legacy.bak (preserved for one container restart in case
anything goes wrong). One-shot, idempotent.

Anyone whose container has been crash-looping since v0.0.56
(see v0.0.59 hotfix) and has no DB at the legacy path either:
nothing to migrate, fresh start unfortunately.

## [0.0.59] · 2026-05-17

### Fixed. CRITICAL: appliance crash-loop on startup (v0.0.56–v0.0.58)
The auth_status handler I added in v0.0.56 declared `async def
auth_status(request)` without a type annotation. Litestar's
signature scanner refuses to start the app when a route parameter
lacks a type. Every container running :latest after v0.0.56 has
been crash-looping (alembic-style "ImproperlyConfiguredException:
'request' does not have a type annotation"). Anyone on Update-now
since this morning needs v0.0.59 immediately.

Annotated `request: Request` and imported it. Local smoke test
passes. Stable on every install path again.

## [0.0.58] · 2026-05-17

### Changed. Settings / Setup tabs require sign-in (UX gate)
The previous READONLY_PUBLIC model lets GET requests through on
LAN without a session and gates only mutations. That worked but
landed users in a confusing state: tap Settings → page renders →
click Save → 401 → no signal of what went wrong.

New model: Settings + Setup tabs are gated client-side. Tapping
either when not signed in redirects to /login?next=<route> and
bounces back after auth. Dashboard / history / devices / docs /
kiosk are still anonymous-readable on LAN. Kiosk-on-wall
deployments and family-on-WiFi viewing still work without a
password.

This is a UX guard, not a new security boundary. The server-side
mutation gate (POST/PATCH/DELETE → session required) remains.

app.js v=127, CACHE_VERSION bumped.

## [0.0.57] · 2026-05-17

### Added. Sign Out button in the appliance header
There wasn't one. The dashboard had a Sign In button (broken in
its own way until v0.0.56) but no way to *un*-sign-in. Ritual North
reported the appliance UI never asked him to log in (the
READONLY_PUBLIC bypass lets GET requests through on LAN without
a session, by design. So the SPA loads, no login prompt) and
that there was no logout affordance.

Both Sign In + Sign Out live in the header now. Auth-status
endpoint decides which one to reveal: authed → Sign Out, not
authed → Sign In. Demo mode suppresses both.

### Added. Diagnostics bundle download (#138)
"Download bundle" button on Settings → Diagnostics. Single JSON
file: version, deployment (pi|docker), platform, uptime, disk,
redacted config (bearer_token / tunnel_token / sso_secret /
api_key / password values scrubbed), transport+device counts,
last poll result, ~500 lines of recent logs. Suitable to attach
to a support ticket.

## [0.0.56] · 2026-05-17

### Fixed. Sign-in button always shown to authenticated users
The header's "Sign in" affordance gated on `document.cookie.includes
("wp_local_session=")` to decide whether to show. Trouble: the
session cookie is HttpOnly (XSS protection. Correct), so JS can
never see it. Every authenticated user saw the button.

Replaced the cookie sniff with a tiny `/api/system/auth-status`
endpoint (anonymous-readable, returns `{authed, origin}` from the
real session table). JS fetches it on load and only reveals the
button when there's genuinely no session. Affects SSO-via-cloud
users + LAN password sign-ins equally.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

## [0.0.55] · 2026-05-17

### Fixed. Appliances paired pre-rebrand silently failed heartbeats
Appliances paired before the wattpost.io → wattpost.cloud rebrand
have `cloud.endpoint: https://app.wattpost.io` baked into their
local config. That hostname now 301s at the Cloudflare edge, and
httpx (correctly) strips the `Authorization` header when following
a cross-host redirect. So the bearer never reached wattpost.cloud
and every heartbeat 401'd. Appliance showed offline despite working
locally + having a valid bearer + the cloud being healthy.

config.load_config now auto-upgrades any legacy endpoint
(`https://app.wattpost.io`, `https://wattpost.io`) to
`https://wattpost.cloud` and persists the change back to the YAML.
Affected appliances heal themselves on next daemon start. New
pairings already default to wattpost.cloud (CloudCfg.endpoint).

### Fixed. Cloud theme defaulted to dark regardless of device
The inline theme bootstrap in _base.html.jinja defaulted to "dark"
(via the `default_theme` block) when no localStorage preference was
set. So a light-mode user landing on the dashboard saw dark forever
until they manually visited /app/account and picked "System". Now
defaults to "system" so OS preference is honoured from first visit.

## [0.0.54] · 2026-05-16

### Added. Staff admin page (#103, MVP)
New /app/admin (staff-only, returns 404 to non-staff so the page's
existence isn't leaked). Three tabs:

- **Users**. Last 500. Toggles for `require_2fa` and `is_staff`.
  Critically, this is the in-app escape hatch for a 2FA-enrolment
  lockout. The only previous fix was SSH + psql (see today's
  incident). Self-demotion via the UI is refused. Has to be done
  manually to prevent accidental admin-lockout.
- **Appliances**. Last 500 with owner email, online flag (any
  heartbeat in 15 min), tunnel link. "Is the fleet healthy"
  eyeballing.
- **Audit log**. Last 200 events across all users, filterable by
  email / event_type / IP. Failed sign-ins highlight in amber.

All staff-side writes get their own audit entry (`staff.user.patch`)
recording who changed what on whom, so admin actions on real users
have a clean trail.

The topbar now reveals an "Admin" link client-side via /api/me
when the user is staff. Non-staff and anonymous visitors never
see it.

### Fixed. Audit_events FK actually altered in prod (migration 0024)
v0.0.52 edited the already-applied migration 0023 to flip CASCADE
→ SET NULL, but that edit had no effect on the production DB.
Migration 0024 performs the real ALTER so account.delete records
actually outlive the user row in prod.

## [0.0.53] · 2026-05-16

### Fixed · 2FA enforcement could 403 /api/login itself
Defense-in-depth on top of v0.0.52: auth-transition endpoints
(/api/login, /api/logout, /api/signup, /api/account/password/forgot,
/api/account/password/reset and their HTML page counterparts) are
now always reachable, regardless of `require_2fa` enrolment state.

Previously, a user with `require_2fa=true`, no TOTP enrolled, and
a stale session cookie would get 403'd on `/api/login`. Meaning
they couldn't even start a fresh login from the same browser to
escape the loop. Now they always can.

## [0.0.52] · 2026-05-16

### Fixed · 2FA enforcement allowlist locked users out of enrolment
The require-2FA middleware's allowlist used the wrong path prefix
(`/api/twofa/` instead of the actual `/api/account/2fa/`), so a
user flagged `require_2fa=true` who hadn't yet enrolled TOTP would
get 403'd from the very endpoints needed to enrol. Locking them
out of their own account with no escape hatch. Fixed the prefix +
added a comment loud enough to prevent a repeat.

### Added. Security events page on /app/account (#144)
- "Recent security activity" card on the account page renders the
  last 50 audit events from the API stood up in v0.0.50: sign-ins
  (success + failure), 2FA changes, password updates, appliance
  pair/delete, account deletion.
- Failed sign-ins highlight in amber; each row shows IP +
  user-agent + timestamp. Friendly event labels + per-category
  icons. Refreshes after revoke-sessions to confirm the action.

### Added. Audit log wired into the remaining security events
- `twofa.enrol`, `twofa.disable`, `twofa.backup_codes_regen` from
  /api/account/2fa/*.
- `account.delete` recorded before the cascade.
- `appliance.pair` from the anonymous /api/pair/exchange endpoint
  (opens its own session, captures IP/UA).
- `appliance.delete` from /api/sites/{id}.

### Changed. AuditEvent FK now SET NULL not CASCADE
So account.delete records survive the user row's deletion. Admin
and fraud workflows still get the "when did account X get
deleted, from where" trail. PII is already gone with the user, so
no privacy regression.

## [0.0.51] · 2026-05-16

### Fixed. Rate limiter was bucketing on CF edge IP, not real client
Once the middleware actually fired (v0.0.49), smoke-test logs
showed every login attempt arrived with a different CF edge IP
(172.68.x.x, 172.69.x.x, 141.101.x.x) because CF rotates which
edge serves each request. Every request went into a fresh bucket
→ rate limiter never tripped → bypassable.

Fix: prefer `CF-Connecting-IP` (Cloudflare's authoritative
real-client header; CF strips it on ingress so it can't be
forged) over `X-Forwarded-For`. Falls back to XFF for direct
non-CF hits, then TCP peer.

After this deploys, brute-force from a single attacker IP hits
the 5-per-minute limit regardless of CF edge distribution.

## [0.0.50] · 2026-05-16

### Added. Audit logging for security-relevant events (#144)
Common SaaS feature (Stripe, Linear, GitHub all show this). Two
purposes: customer-facing visibility into account activity +
ops-facing forensics for incident review.

- **Schema**: new `audit_events` table via migration 0023.
  Composite index on `(user_id, created_at DESC)` for the
  per-user-timeline query.
- **Helper**: `cloud/wattpost_cloud/audit.py::log_event()` ·
  swallows failures (audit writes never break the operation
  they're auditing).
- **Wired into** (this release):
  - `login.success` (with `twofa_used` flag)
  - `login.failure` (records attempted email)
  - `password.change` (records sessions_revoked count)
  - `logout.others` (sessions_revoked count)
- **More wire-ups to follow** (twofa enrol/disable, account
  delete, email change, appliance pair/delete, billing changes):
  trivial follow-up since the helper is in place.
- **New endpoint** `GET /api/account/security/events` returns
  the last 50 events for the signed-in user. UI page can render
  this; backend ready.

## [0.0.49] · 2026-05-16

### Fixed. Middleware actually fires now (was silently no-op'd)
v0.0.48 added `DefineMiddleware(...)` wrappers thinking that
fixed the v0.0.46/v0.0.47 issue of plain ASGI middleware classes
being silently ignored. It didn't. Litestar's `middleware=[…]`
expects `litestar.middleware.ASGIMiddleware` subclasses with a
`handle(scope, receive, send, next_app)` method. NOT
`__call__(scope, receive, send)`.

All three security middlewares converted:
- `rate_limit.RateLimitMiddleware` → ASGIMiddleware
- `csrf.CSRFMiddleware` → ASGIMiddleware
- `twofa_enforce.TwoFactorEnforcementMiddleware` → ASGIMiddleware

Registered as INSTANCES not classes:
`middleware=[RateLimitMiddleware(), CSRFMiddleware(), TwoFactorEnforcementMiddleware()]`.

Saved as memory [[litestar-middleware-shape]] so future me / future
contributors don't burn time on the same trap.

### What this means in practice
After this deploys, all three gates fire on every request for real:
- 5 bad logins/minute → 429 on the 6th
- POST to cookie-auth state-changing endpoint without
  `X-Requested-With: WattPost` → 403
- Staff session without 2FA enrolled hits any non-allowlisted
  endpoint → 403 + `must_enroll_2fa: true` payload → frontend
  redirects to enrolment

## [0.0.48] · 2026-05-16

### Fixed. Middleware registration (rate-limit was silently ignored)
v0.0.46 / v0.0.47 registered ASGI middleware in Litestar's
`middleware=[Cls, Cls]` list, assuming Litestar accepts plain
ASGI middleware. It doesn't. They were loaded but never invoked
on actual requests. Caught during smoke test (6 bad logins all
returned 401 instead of 429 by the 6th). Fix: wrap each in
`DefineMiddleware(...)`. Now ALL three security middlewares are
actually in the request chain:

  - RateLimitMiddleware (since v0.0.46). Now actually rate-limiting
  - CSRFMiddleware (new this release)
  - TwoFactorEnforcementMiddleware (since v0.0.47). Now actually enforcing

### Added. CSRF protection via custom-header pattern (#142)
- New `cloud/wattpost_cloud/csrf.py` middleware. Requires
  `X-Requested-With: WattPost` on every cookie-auth POST / PUT /
  PATCH / DELETE. Cross-origin form-submits can't set custom
  headers; cross-origin JS would need CORS preflight (which we
  don't grant); same-origin frontend always sets the header via
  the wrapper in `_base.html.jinja`.
- Allowlist exempts bearer/signature-auth endpoints:
  `/api/heartbeat`, `/api/billing/webhook`, `/api/v1/*` (public
  REST API), `/api/pair/exchange`, pre-auth flows (`/api/login`,
  `/api/signup`, `/api/account/password/{forgot,reset}`), and
  the internal Caddy-only endpoint.
- Frontend `fetch()` wrapper in `_base.html.jinja` injects
  `X-Requested-With: WattPost` on every same-origin request.
  Cross-origin requests are passed through unchanged so the
  header doesn't leak to third-party APIs.

### Threat model after this ships
- Credential stuffing → rate-limited
- Account enumeration → rate-limited
- Pair-code brute force → rate-limited
- Form-based CSRF → blocked by custom-header requirement
- Cookie-stealing via XSS → mitigated by HttpOnly + SameSite=Lax
- Staff password leak → 2FA enrolment gate
- Direct tunnel access (leaked URL) → SSO + broker-auth required
- Internal endpoint probing → 404'd unless from Caddy

## [0.0.47] · 2026-05-16

### Security · 2FA enrolment enforcement for staff accounts
Admin password leak shouldn't = WattPost cloud compromise. Staff
users now must enrol TOTP-based 2FA before they can access anything
beyond the enrolment page itself.

- **Schema**: `users.require_2fa BOOLEAN DEFAULT FALSE`
  (migration `0022_user_require_2fa.py`). Backfill sets True for
  every existing `is_staff = TRUE` user.
- **Stateless enforcement** in
  `cloud/wattpost_cloud/twofa_enforce.py`. ASGI middleware checks
  every request:
  - If session resolves to a user with `require_2fa=True` AND
    `totp_enabled_at IS NULL` AND the path isn't on the enrolment
    allowlist → return 403 + `{must_enroll_2fa: true,
    enrol_url: /app/account#enroll-2fa}`.
  - Allowlist: `/api/twofa/*`, `/api/logout`, `/api/me`,
    `/api/account/sessions`, `/app/account`, `/static/*`,
    `/healthz`, `/login`, `/logout`.
- **Frontend handler** in `_base.html.jinja` wraps `fetch()`. Any
  403 with `must_enroll_2fa` triggers `window.location.href =
  enrol_url`. Smooth UX: user logs in, dashboard tries first fetch,
  gets redirected straight to the enrolment flow.

### What this means for the founder account (`is_staff=True`)
After deploy + alembic upgrade, your next visit to `wattpost.cloud`
will:
1. Let you log in normally.
2. Redirect you straight to `/app/account#enroll-2fa`.
3. You scan the QR with your authenticator app, enter a code, save
   the backup codes.
4. From then on, login requires password + 6-digit TOTP code on
   every signin.

Non-staff users keep 2FA optional. Push for the recommendation
later when there's enough mass to bother.

## [0.0.46] · 2026-05-16

### Security. Hardening sprint
Tier-1 gaps post-rebrand. No customers yet so blast radius is zero,
but the work needed to land before any do.

- **Per-IP rate limiting** on auth-adjacent POST endpoints. New
  ASGI middleware (`cloud/wattpost_cloud/rate_limit.py`,
  in-memory sliding window). Policy table:
  - `/api/login`, `/api/login/2fa`, `/api/signup`: 5/min/IP
  - `/api/account/password/forgot`: 3/hour/IP (enumeration)
  - `/api/account/password/reset`: 5/hour/IP
  - `/api/pair/exchange`: 10/hour/IP (8-char code brute force)
  - 429 + Retry-After header on block. Trusts
    `X-Forwarded-For` from Caddy (everyone else is behind
    Caddy so can't spoof from the public internet).
- **`/api/internal/can-access` lockdown**. Caddy's `forward_auth`
  is the only legitimate caller. Endpoint now:
  - Rejects with 404 (not 400) on any non-Caddy request, so
    attackers can't tell the route exists.
  - Verifies the TCP peer is a private-IP address (10/8, 172/12,
    192.168/16). Public hits get 404.
  - Still requires `X-Forwarded-Host` to end in `.wattpost.cloud`
    so we don't leak ownership info via 401-vs-403 timing.
- **Security headers on `wattpost.cloud` + `*.wattpost.cloud`** in
  Caddyfile. HSTS-preload, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy. Verified live.

### Verified
- `curl https://wattpost.cloud/app -I` → headers present
- `curl https://wattpost.cloud/api/internal/can-access` from public
  → 404 (was 400 in v0.0.45)
- Rate limiter live in middleware chain. First 5 logins/min OK,
  6th gets 429

### What's still on the audit list
- CSRF tokens on cookie-auth POSTs
- `sso_secret` column encryption at rest
- 2FA enforcement option for staff accounts
- Stripe webhook replay protection audit
- External uptime monitoring + status page (#140, #141)
- End-to-end signup → email-verify → pair re-test post-rebrand

## [0.0.45] · 2026-05-16

### Changed. Phase 3 of cloud rebrand: appliance side (#139)
Final code/doc sweep of `app.wattpost.io` references in the appliance.

- `solar_monitor/config.py` · `CloudCfg.endpoint` default flips from
  `https://app.wattpost.io` → `https://wattpost.cloud`. Existing
  pairings keep their on-disk value (still works via 308); new
  pairings point at the new domain from first heartbeat.
- `solar_monitor/update/checker.py` · `DEFAULT_MANIFEST_URL` flips
  to `https://wattpost.cloud/api/releases/latest`. Same back-compat
  story.
- `solar_monitor/api/cloud_admin.py`. Pair-flow defaults and
  PUT payload defaults flipped.
- `solar_monitor/web/login-tunnel.html`. Direct-tunnel-access
  block page now points at `wattpost.cloud` for "sign in here".
- `solar_monitor/web/app.js`. Integrations panel's "Pair with"
  fallback URL.
- Comments + docstrings across appliance modules sweep-updated.

### Docs
- `docs/pairing.md`, `docs/kiosk.md`, `docs/release-pipeline.md`,
  `docs/cloud-architecture.md`. Every customer-visible reference
  to `app.wattpost.io` swapped to `wattpost.cloud`. The 32 hits
  in the docs tree are mechanical replacements; no behavioural
  content changed.

### Done with the rebrand
This is the last code/doc commit in the migration. Future phases:
- Phase 5: watch heartbeat logs over the next ~week to confirm
  every paired appliance has hit `wattpost.cloud/api/heartbeat`
  at least once; then remove the `app.wattpost.io` 308 block from
  the Caddyfile entirely.
- No customer-visible cleanup left after that.

## [0.0.44] · 2026-05-16

### Changed. Phase 2 of cloud rebrand: app.wattpost.io → wattpost.cloud (#139)
Following v0.0.43 (which stood up wattpost.cloud + the Caddy broker
in parallel), this commit completes the URL migration:

- **Cloud code sweep** · 22 hardcoded `https://app.wattpost.io/...`
  references across 13 files (verification emails, password-reset
  links, billing return URLs, marketing copy, referral URLs)
  flipped to `https://wattpost.cloud/...`. Future emails / new
  bookmarks all use the new domain.
- **Caddy app.wattpost.io block**. Replaced with a single
  `redir https://wattpost.cloud{uri} 308`. 308 preserves method +
  body, so existing paired appliances heartbeating to
  `app.wattpost.io/api/heartbeat` keep working without any
  appliance-side change. Heartbeat POST → 308 → POST to wattpost.cloud.

### Verified
- `https://app.wattpost.io/login` → 308 → loads at wattpost.cloud/login
- `POST https://app.wattpost.io/api/heartbeat` (fake auth) → 308 →
  POST lands at wattpost.cloud, returns 401 (auth checked, method
  preserved)
- `https://wattpost.cloud/app` serves the dashboard

### Next
- Phase 3: update appliance default cloud endpoint to
  `https://wattpost.cloud` in `solar_monitor/config.py`. New pairings
  use the new endpoint directly; existing pairings stay on
  `app.wattpost.io` (heartbeat 308 keeps them working).
- Phase 4: README + docs sweep.
- Phase 5: remove the app.wattpost.io block entirely once heartbeat
  logs confirm every paired appliance has hit wattpost.cloud at
  least once.

## [0.0.43] · 2026-05-16

### Changed. Cloud broker rebuilt with Caddy on wattpost.cloud (#139)
Field-scan + Ritual North's pushback on the v0.0.42 Python broker
("I don't like that the tunnel is hitting the appliance directly")
led to a proper architectural redo. Nabu Casa, Tesla, Sonos and
Cloudflare Access all converge on the same pattern: cloud-relay
through the vendor SaaS, with the data-plane proxy in a battle-
tested HTTP layer (not custom Python).

**Domain rebrand**: `wattpost.io` keeps marketing/landing/download;
**`wattpost.cloud` is now the SaaS dashboard + broker hostname**.
- Apex `wattpost.cloud` = the SaaS (was `app.wattpost.io`).
- `<slug>.wattpost.cloud` = brokered appliance dashboards.
- Cloudflare Universal SSL covers `wattpost.cloud` + `*.wattpost.cloud`
  for free (single-level wildcards). No paid Advanced Cert needed.
- Single eTLD+1 means session cookies set on the apex are sent to
  every subdomain (including the broker) automatically. No cross-
  domain auth dance.

**Data path** (replaces v0.0.42's Python httpx proxy):
1. Browser → `<slug>.wattpost.cloud` with the `.wattpost.cloud`
   session cookie.
2. Caddy `forward_auth` calls cloud's new
   `/api/internal/can-access` endpoint.
3. Cloud verifies session + ownership, returns 200 with
   `X-WP-Broker-Auth = <ts>.<hmac>` signed using the per-appliance
   `sso_secret`.
4. Caddy copies the header into the upstream request and
   reverse-proxies to `<slug>.wattpost.io` (the existing CF tunnel).
5. Appliance's auth middleware verifies the broker header and
   bypasses normal session checks (unchanged from v0.0.42).

**Native SSE/WebSocket** pass-through, **no HTML rewriting shim
needed** (subdomain pattern means the appliance's absolute `/api/*`
and `/web/*` paths resolve correctly).

**Deleted**:
- `cloud/wattpost_cloud/api/broker.py`. The Python proxy.
- HTML shim injection logic. Moot under subdomain pattern.

**Cookie domain change**: `.wattpost.io` → `.wattpost.cloud`. Existing
sessions on app.wattpost.io get invalidated; one re-login per
browser. Acceptable: only the founder has an account today.

**`app.wattpost.io`** stays serving the same backend for back-compat
(heartbeats from paired appliances etc) until a future release
turns it into a 308 redirect to `wattpost.cloud`.

### Infrastructure
- Caddyfile (vps-infra) adds `wattpost.cloud` and `*.wattpost.cloud`
  site blocks alongside the existing `app.wattpost.io` block.
- DNS (CF wattpost.cloud zone): A record apex → `REDACTED-ORIGIN-IP`
  (proxied), CNAME `*` → `wattpost.cloud` (proxied).

### Strategy lock-in (memory)
- [[project-cloud-tier]] updated: local dashboard stays canonical
  (it's the marketing budget we don't pay for); cloud-exclusive
  features are the moat. Broker is convenience-layer not moat.
- [[project-target-customer]] updated: paying personas vs marketing
  personas distinction. Cabin guy is marketing, not P&L.
- [[feedback-dont-optimize-for-non-payers]] new: don't bend
  architecture for non-paying personas.

### Next
- Phase 2: turn `app.wattpost.io` → 308 redirect to `wattpost.cloud`.
- Phase 3: update appliance default endpoint + email templates.
- Phase 4: remove `app.wattpost.io` after grace period.

## [0.0.42] · 2026-05-16

### Added. Cloud broker (#139)
- **Cloud now proxies appliance dashboards.** Previously the
  cloud's "Open" button bounced the user to
  `<slug>.wattpost.io/sso?token=…`; the browser then ran every
  subsequent request directly against the appliance's tunnel
  hostname. Ritual North didn't like that the tunnel was hitting the
  appliance directly even with SSO in front: the appliance
  hostname was still in the URL bar, and every byte of the
  dashboard rode through Cloudflare's tunnel rather than the
  cloud's perimeter.
- **New design.** The cloud serves the appliance dashboard at
  `app.wattpost.io/site/{appliance_id}/...`. Every request is
  reverse-proxied via `httpx` from the cloud through to
  `<slug>.wattpost.io` server-side. The user's browser only
  ever talks to `app.wattpost.io`. Tunnel hostname is invisible.
- **Authentication chain.** Cloud verifies the user's session
  cookie + appliance ownership cloud-side, then stamps each
  outbound request with `X-WP-Broker-Auth: <ts>.<hmac>` signed
  by the per-appliance `sso_secret`. The appliance's auth
  middleware verifies the header and bypasses session/SSO
  checks. So the appliance auth wall is still up if anyone
  tried to bypass the cloud by hitting the tunnel directly.
- **SSE bridged.** httpx's `aiter_raw()` + Litestar's `Stream`
  forwards chunks as they arrive, so the live `/api/stream`
  endpoint works through the broker.
- **HTML shim.** Proxied HTML responses get a tiny script
  injected at `<head>` that monkey-patches `fetch()`,
  `XMLHttpRequest`, and `EventSource` to prefix absolute paths
  (`/api/devices` → `/site/{id}/api/devices`). Without this,
  the appliance dashboard's hard-coded paths would hit the
  cloud root and 404. Service-worker registration is no-op'd
  under the broker (the SW caches stale appliance assets at
  the wrong scope).
- **Open button** in the cloud dashboard now points at the
  broker URL directly; no JS click handler, native link with
  `target="_blank"`.

### Trade-offs
- Direct tunnel access still works (`<slug>.wattpost.io/sso?token=…`)
  for the time being. Once we're confident in the broker we'll
  deprecate it.
- WebSocket bridging isn't implemented. The appliance doesn't use
  WS today (only SSE). Add when needed.
- HTML rewriting + the JS shim are belt-and-braces; cleaner long-
  term is to ship appliance HTML/JS with relative URLs and drop
  the shim. Track as a polish item.

## [0.0.41] · 2026-05-16

### Fixed. Tunnel `/login` no longer pretends to work
- Direct tunnel URL access (e.g. someone bookmarked the tunnel
  hostname, or shared the link) used to render the LAN password
  form, accept the user's password, issue a session… that the
  middleware then rejected for every subsequent tunnel request
  because the session's origin was `local`, not `sso`. Dead end
  with no explanation.
- Tunnel-origin hits to `/login` now serve `login-tunnel.html`:
  a dedicated page that says "sign in at app.wattpost.io and
  click Open" with a CTA to the cloud dashboard. No password
  field on tunnel. There's nothing to fill in.
- `/api/login` also refuses tunnel-origin POSTs (403). Belt and
  braces in case a client-side script or a manually-crafted
  request hits it directly.

### Next
- Cloud-side broker (#139): instead of the tunnel exposing the
  appliance dashboard at `<slug>.wattpost.io`, the cloud serves
  it transparently at `app.wattpost.io/site/{id}/`. User never
  leaves the cloud session; tunnel hostname is invisible. Multi-
  day build. HTTP proxy + SSE bridging + appliance shared-secret
  for defense-in-depth. Issue tracking the design.

## [0.0.40] · 2026-05-16

### Added. In-app password reset + Sign in header link
- **Settings → System → "Rotate web password" button.** One-click
  password rotation from the dashboard. Generates a ~16-char random
  password, writes the hash + plaintext mirror, shows the new
  password once with a Copy button. Closes the gap for Docker
  users who don't have `wattpost-config` TUI access on the host.
  Existing browser sessions on OTHER devices stay valid until
  natural-expiry (30 days) so rotation doesn't sign you out of
  the tab you're rotating from.
- **Header "Sign in" pill.** Shown next to the status pill when
  the user has no local session cookie AND a password is set on
  the appliance. Jumps to `/login?next=<current-hash>`. Demo mode
  hides it. Previously the login flow was hidden behind the red
  "login required" error on attempted writes. Now it's a visible
  affordance the second you load the dashboard.

### API
- New `POST /api/system/web-password/rotate`. Auth: requires
  existing session (the standard write-gated path). Returns
  `{ok: true, password: <new>}` exactly once.

### Coming
- `wattpost-config` parity for Docker is a bigger lift (web port,
  reset-to-defaults, log dumps); password reset is the first
  slice. Track #138 for the rest.

## [0.0.39] · 2026-05-16

### Fixed. Settings → Cloud Save was wiping tunnel + SSO state
- The cloud config edit handler (`PUT /api/cloud/config`) rebuilt
  the in-memory `CloudCfg` from scratch using only the form fields
  the user submitted (endpoint + heartbeat_minutes), preserving
  `bearer_token`, `appliance_id`, and `label` but DROPPING
  `tunnel_token`, `tunnel_hostname`, and (newly in 0.0.38)
  `sso_secret`. Then `_serialize_cloud` wrote the slimmed-down
  CloudCfg back to `config.yaml`. Wiping all three on disk.
- Symptom: tunnel + SSO worked right after pair / heartbeat,
  then any "Save" click in Settings → Cloud silently broke
  both. Caught by Ritual North after pulling v0.0.38: heartbeat
  populated `sso_secret`, then Settings-save reset it, then
  tunnel hits returned 401 because the appliance had no key to
  verify cloud-signed redirects against.
- Fix: handler now carries every existing field across, not just
  the headline three. One-line addition per field.

### Recovery for installs hit by this in 0.0.38
The pre-Save backup at `/etc/wattpost/config.yaml.bak` (inside the
container; bind-mount on Docker) still has the lost fields. Restore
it + restart the container:

```
sudo cp /opt/wattpost/wattpost-config/config.yaml.bak \
        /opt/wattpost/wattpost-config/config.yaml
docker restart wattpost
```

Or: do nothing, re-pair from the cloud Sites page. Fresh
`bearer_token` + `sso_secret` arrive in the pair response.

## [0.0.38] · 2026-05-16

### Added. Cloud→appliance SSO (#137)
- **Cloud-signed redirect tokens replace the "give everyone the
  tunnel URL" model.** When a logged-in cloud user clicks "Open"
  on the dashboard, the cloud now mints a short-lived
  HMAC-SHA256 token bound to (`user_id`, `appliance_id`,
  `exp=now+60s`, random `jti`), and redirects the user to
  `https://{slug}.appliances.wattpost.io/sso?token=…`. The
  appliance verifies the signature against a per-appliance
  `sso_secret` exchanged at pair time, issues a session cookie
  tagged `origin=sso`, and bounces to `/`. Transparent to the
  user: cloud login is the front door, no local-password prompt.

- **Tunnel-origin requests now require an SSO-issued session.**
  The middleware separates `is_session_valid()` (any local
  session OK) from `is_session_valid_for_tunnel()` (SSO origin
  required). Local password is still usable for LAN / kiosks /
  break-glass; it just can't grant tunnel access on its own.
  Closes the threat: a leaked tunnel URL is now harmless. The
  recipient has to log into your cloud account first to mint
  a valid token.

- **Replay protection**: the appliance caches `jti` claims of
  recently-consumed tokens until exp+10s; a second use within
  that window is rejected even with a valid signature. Tokens
  are effectively single-use.

### Storage / schema
- **Cloud**: new `appliances.sso_secret` column (32-byte hex).
  Migration 0021 backfills all existing rows with a fresh
  secret; new rows get one from a `default=` on the model.
- **Appliance**: new `cloud.sso_secret` field on `CloudCfg`,
  persisted to `config.yaml`. The cloud heartbeat response
  always includes the current cloud-side value; the appliance
  picks it up on first heartbeat post-update so legacy pairs
  don't need to re-pair.

### Endpoints
- Cloud: new `GET /api/sites/{id}/sso` (cookie-auth, owner-only)
  returning `{redirect_url, expires_in}`.
- Appliance: new `GET /sso?token=…` (anonymous) verifying the
  token + issuing the session cookie.

### Threat model notes
- The tunnel itself stays always-on (cloudflared maintains a
  permanent connection); we don't try to gate the tunnel
  lifecycle. Auth lives at the appliance. Anyone with the URL
  reaches the auth wall, doesn't sneak past it.
- Local password becomes the LAN fallback / break-glass route;
  no more single-token-grants-everything. See [[docker-pi-parity]]
  in agent memory.

## [0.0.37] · 2026-05-16

### Security. Docker installs were also wide open (urgent follow-up to 0.0.36)
- **The bug:** v0.0.36 closed the tunnel-via-loopback bypass, but
  Docker installs have a SECOND hole the SD image didn't have:
  `packaging/install.sh` (Pi-only) is what generates the first-boot
  password. Docker installs never ran install.sh, so
  `password_is_set()` returned False. And the auth middleware
  used to bypass entirely on "no password set." Net effect: every
  Docker customer's appliance was open to anyone with the URL,
  tunnel or LAN. Caught by Ritual North after he updated to 0.0.36 and
  said "I don't have a local password, I'm pretty sure we don't
  ship a password on Docker."
- **The fix, two parts:**
  1. New `ensure_first_boot_password()` runs at daemon startup
     (cli.py `cmd_serve`). If no hash exists, it generates a random
     ~16-char password, writes the hash, mirrors the plaintext to
     `/etc/wattpost/web-password`, and logs the plaintext at
     WARNING level so Docker users can find it via
     `docker compose logs wattpost | grep -A2 FIRST-BOOT`.
     Idempotent: existing hash → no-op.
  2. Auth middleware no longer treats "no password set" as a
     bypass. It now fail-closes: every non-anonymous path returns
     503 (API) or redirects to /login (HTML) until a password is
     configured. The startup hook guarantees one exists in normal
     operation; if hash-write fails (permissions, read-only mount),
     the operator gets a loud error log AND a 503 wall. No quiet
     wide-open state.
- **What customers need to do:**
  - SD-card users: nothing. Install.sh already set a password.
  - Docker users: `docker compose pull && docker compose up -d`,
    then `docker compose logs wattpost | grep -A2 FIRST-BOOT` to
    find the generated password. Save it; bookmark Settings →
    System → Reset web password to rotate later.
- New `WATTPOST_PASSWORD_DIR` env var lets you point the hash +
  plaintext files at a different directory than `/etc/wattpost`
  if you've got an unusual data layout.

### Docs
- `docs/docker-install.md` now has a "First-boot password" section
  with the exact `docker compose logs | grep FIRST-BOOT` command.

## [0.0.36] · 2026-05-16

### Security. Tunnel URL no longer grants anonymous access (urgent)
- **The bug:** the appliance's auth middleware treated source IP
  `127.0.0.1` as fully trusted ("the request must have come through
  the authenticated cloud session"). But cloudflared on the
  appliance proxies tunnel traffic to localhost, so EVERY tunnel
  request appeared as loopback. Net effect: anyone with the
  `{slug}.appliances.wattpost.io` URL got full unauthenticated
  read + write access to the appliance. Including settings, alert
  rules, and write-through endpoints. Reported by Ritual North after he
  shared the URL with a friend who could read his appliance from
  another house.
- **The fix:** `is_loopback_source()` now sniffs for Cloudflare's
  `CF-Ray` / `CF-Connecting-IP` / `CF-IPCountry` headers and returns
  False when present. Real loopback (curl from the Pi, SSH
  port-forward, the daemon talking to itself) has none of those, so
  legitimate local-trust paths still work. New helper
  `is_tunnel_origin()` is also used to disable the `READONLY_PUBLIC`
  GET bypass for tunnel requests. A leaked URL would otherwise
  still leak every metric anonymously.
- **What customers will see after this update:** clicking "Open" on
  the cloud dashboard now lands them on the appliance's local
  login page. They'll need their local appliance password (printed
  on the first-boot MOTD; also visible at Settings → System →
  Reset web password). Session cookie persists 30 days.
- **Coming next** (#137): transparent SSO via a short-lived cloud-
  signed token, so the "Open" button works without re-prompting
  for a password. Until that lands, the password prompt is the
  correct, safe trade-off.

## [0.0.35] · 2026-05-16

### Added. Forecast-aware runtime prediction (#99)
- **New sub-line on the Hero's Remaining tile.** The existing
  "until empty" was always naive: current instant power × current
  SoC. A 2 kW kettle on for 30 seconds would drag it down to a
  scary number, then bounce back. Two replacements ride below it:
  - **Forecast-aware** (when an Open-Meteo or Solcast forecast is
    cached): walks hourly through the next 48h, subtracts forecast
    PV from a rolling 1-hour avg load, and reports either an
    absolute depletion time ("~14h until 10% · 02:30 Tue") or
    "holds for the 48h window" when PV input covers the draw.
  - **Naive rolling fallback** (no forecast configured): same
    1-hour avg load but no PV · "1h-avg: ~3.2 days to 10%".
- **Why the 10 % floor**: LFP wants headroom; predicting to 0 % is
  both alarming and academic since loads cut out before then.
- **Hidden gracefully** when there's no bank capacity to predict
  from (fresh install) or no historical load to average from.

### API
- New `GET /api/runtime-forecast` returning `now`, `naive`, and
  `forecast` blocks. The forecast walk is best-effort. Failures
  return `forecast.available=false` and the UI falls back to the
  naive line.

### Storage
- New `Store.rolling_load_avg(window_seconds=3600)` returning mean
  bank power over the trailing window. Negative when discharging.
  Single-query AVG across the V×I join. Cheap on the rollup
  tables.

## [0.0.34] · 2026-05-16

### Added. Battery health tile (#109)
- **New dashboard panel** above Cell balance: four headline stats
  + a 10-bar SoC residency histogram showing where the bank lives
  over the last 30 days.
- **Cycles (BMS)**: cycle count reported by the BMS. Worst-pack-
  wins (max across packs in a multi-pack bank). Empty when no BMS
  is paired.
- **Lifetime energy**: cumulative kWh that's flowed through the
  bank since the BMS started counting. Computed from the BMS-
  reported total_charge_ah × current mean pack voltage. Renders
  as "kWh" up to 1 MWh, then "MWh" above.
- **Window cycles**: equivalent full cycles over the last 30
  days, computed by integrating discharged kWh ÷ bank capacity.
  Works *without* a BMS. Every shunt + battery setup gets this.
- **Days online**: time since the earliest bank sample. Useful
  for "is the BMS cycle counter saying 247 cycles in only 30
  days?" sanity checks.
- **SoC residency histogram**: 10 vertical bars, one per 10 %
  band. Red at the low end → green at the high end. A healthy
  LFP bank lives in the 50-95 % bands; visible weight at 0-30 %
  means the customer's draining too deep and shortening lifespan.
  Stat header surfaces the peak band: "mostly 70-80% (32 % of
  the time)".

### Strategic context
Renogy Smart Shunt 300 surfaces cycle count on its tiny screen
but nowhere else. Victron VRM has a "battery life" widget but
it's locked to BMV/SmartShunt installs and lives behind their
SaaS. WattPost surfaces it free, BMS-or-shunt-driven, with the
residency histogram added on top. Aligns with the moat per
[[project-coverage-commitment]] in agent memory.

### API
- New `GET /api/battery-health?days=N` (default 30, clamp 1-365)
  returning the aggregate. Read-only; no auth changes.

## [0.0.33] · 2026-05-16

### Fixed. Demo.wattpost.io broken since 0.0.31
- **Synthetic poller crash loop.** `_compute_bank_aggregate` started
  emitting non-numeric fields (`source: "shunt"|"bms"` and the
  `source_disagreement` dict) in 0.0.31. record_poll's bank-persist
  loop assumed every value was numeric and crashed with
  `ValueError: could not convert string to float: 'shunt'` on every
  poll. The store stayed empty; the dashboard saw zero devices and
  fell through to "Setup needed" + the wizard redirect. Fix: route
  bank fields by type. Floats to `samples`, strings to
  `samples_str`, dicts JSON-encoded into `samples_str`.
- **Demo dashboard yanking visitors into the setup wizard.** Even
  with the persist fix, the demo container has zero configured
  transports (it uses a synthetic poller), so the dashboard fired
  its first-boot redirect into `#/setup`. Now gated on the
  `is-demo` body class via a new `_maybeFirstBootRedirect` helper
  that awaits the `/api/system/info` promise before deciding.

### Added. Battery health plumbing (groundwork for #109)
- Bank aggregate now surfaces `cycle_count`, `lifetime_throughput_ah`,
  and `lifetime_throughput_kwh` when one or more BMSes report them
  (JK BMS, Lynx Smart BMS. Anything with `cycle_count` +
  `total_charge_ah`). Cycle count is the max across packs (worst-
  pack-defines-bank); throughput is the sum. Empty when no BMS.
- New `Store.battery_health_aggregate(since, until)` returns a 10-
  bucket SoC residency histogram + window equivalent cycles +
  days-online. No tile yet. That lands in 0.0.34.

## [0.0.32] · 2026-05-16

### Added. First-class alert rules audit (#107)
- **One-tap alert templates.** Settings → Alerts now has a "Quick
  templates" pill row. Tap a chip → add-rule form opens with the
  metric path, comparison operator, threshold, severity, and
  cooldown all pre-filled with sensible defaults. Users don't
  have to learn the metric-path schema or invent thresholds.
  Shipped templates:
  - Low SoC (< 30%). Warn, 1h cooldown
  - Critical SoC (< 15%). Alarm, 15min cooldown
  - Low voltage (< 11.5V for 12V). Alarm
  - Bank over-temp (> 50°C). Alarm
  - Cell drift warning (> 100 mV). Warn
  - Cell drift alarm (> 200 mV). Alarm
  - Shunt-vs-BMS disagreement (> 10 percentage pts). Warn,
    catches battery monitoring drift before customers complain
- **Expanded metric suggestions** in the dropdown. New entries:
  bank.time_to_go_minutes, bank.cell_min_v, bank.cell_max_v,
  bank.source_disagreement.delta_pct (all from #121),
  devices.charge_controller.pv_power_w, battery_temperature_c,
  controller_temperature_c, load_status.

### Strategic context
Renogy's Smart Shunt 300 has on-device alarms (low/high V, low SoC,
temp, deep-discharge) with per-alarm enable/disable. WattPost now
has parity at the rule-engine layer + crosses every device's metrics
(not just the shunt's own readings) and routes through any number
of notification transports (push, email, MQTT, Discord, ntfy,
Pushover). That's the alarm wedge per
[[project-renogy-competitive]] in agent memory.

## [0.0.31] · 2026-05-16

### Added. Victron pairing in the setup wizard (#118 + #120 Phase 1B)
- **BLE scan now identifies Victron, Renogy, and JK devices** by
  manufacturer ID + name patterns. Each device row in the scan
  results gets a colour-coded vendor badge:
  - 🔵 Victron. Additionally shows the decoded device class
    (SmartShunt, SolarCharger, DcDcConverter, etc.) when the
    advertisement payload makes that possible (no decryption
    needed. Model ID is in the public header).
  - Renogy BT-2 / BT-1. Kept the existing badge.
  - JK BMS. Surfaced as a recognised device with a "manual
    config needed" placeholder (driver shipped in v0.0.21;
    GATT-handshake wizard support will land in a follow-up).
- **One-tap Victron pairing.** Tap "Pair Victron" on a Victron
  scan row → inline form expands → paste the encryption key from
  VictronConnect's "Show device key" dialog → Save. Daemon
  hot-reloads, transport appears in the list within ~2 seconds.
  No more manual YAML editing.
- **`add_transport` endpoint accepts `type=ble_victron_advertise`**
  with a `encryption_key` field (32 hex chars, tolerant of the
  spaces / colons VictronConnect sometimes shows). MAC dedupe
  works across all transport types so a customer can't
  accidentally double-pair the same device.

### Closes the Persona B unlock
Together with #112 (SmartShunt driver, v0.0.13) and the bank
reconciliation in #121, the entire "budget upgrader who buys a
shunt for visibility" workflow is now one-tap-installable from
the wizard. No CLI, no YAML, no Python.

## [0.0.30] · 2026-05-16

### Added · "No-BMS" dashboard mode (#115)
- **Shunt-only installs (Persona B. See `project_target_customer`
  in agent memory) now read cleanly.** The bank aggregator in
  #121 already handled the data path; this finishes the UI:
  - **Bank-meta tile** drops the "0× " prefix when no BMS is
    declared. Renders just the shunt model name (e.g.
    "SmartShunt 500A/50mV") instead of the confusing "0× …".
  - Cell-balance panel auto-hides cleanly (already did, just
    confirmed).
  - Time-to-go reads from the shunt's Coulomb-counted estimate
    (via #121).
  - Per-device detail page already had a dedicated
    `buildShuntDetail` renderer. Verified it still works.
- After this lands, a customer with a Victron SmartShunt + a
  Renogy MPPT (no BMS) gets a complete coherent dashboard. The
  budget-upgrader segment we're targeting per
  `project_target_customer` finally has the full experience.

## [0.0.29] · 2026-05-16

### Added. BMS-vs-shunt reconciliation (#121)
- **Two-layer bank aggregator.** Cell-level metrics (per-cell V,
  worst-pack drift, cell min/max) always come from BMSes. Shunts
  don't have per-cell data. System-level metrics (V, A, SoC,
  remaining Ah, time-to-go) prefer the shunt when present,
  fallback to BMS pack-sum otherwise. **Previously the shunt
  branch returned early, dropping all cell-level data. Fixed.**
- **Source-disagreement hint.** When both shunt and BMS report SoC
  and they differ by more than 5 percentage points, the hero tile
  shows a quiet sub-line: *"BMS 72% · shunt 65%. Showing shunt"*.
  Renogy DC Home makes users pick manually; we pick the right
  source automatically *and* tell them when we're unsure.
- **Time-to-go from shunt.** When the shunt reports a Coulomb-
  counted `time_to_go_minutes`, the Remaining tile uses that
  instead of the V·I extrapolation. Much better accuracy on
  variable loads.
- **Manual override** via new optional `bank:` config block:
  ```yaml
  bank:
    source: auto      # auto | shunt | bms
    disagreement_threshold_pct: 5.0
  ```
  Defaults to `auto` (recommended). Set `shunt` or `bms` to force
  a side when your hardware is misconfigured.

### Fixed
- Previously, the bank aggregator's shunt branch returned early
  and dropped `worst_pack_drift_v`, `cell_min_v`, `cell_max_v`
  from the snapshot when both a shunt and BMSes were present ·
  meaning customers with a hybrid install lost the cell-balance
  panel data. The aggregator now keeps both layers independent.

## [0.0.28] · 2026-05-16

### Fixed
- **`pyproject.toml` pinned `victron-ble>=0.10`, which PyPI doesn't
  have** (the latest published version is `0.9.3`). GitHub-hosted
  runners had been cache-hitting through this since v0.0.13, but
  the freshly-spun-up self-hosted runners on the VPS resolved deps
  from scratch and failed the appliance + demo Docker builds.
  Relaxed to `victron-ble>=0.9`.

### Appliance code unchanged from v0.0.27.

## [0.0.27] · 2026-05-16

### Changed
- **Every offgrid-monitor workflow now runs on the self-hosted VPS
  runners**, not just pi-gen. Previously the Docker, source-tarball,
  cloud, and demo workflows stayed on GitHub-hosted runners. Looked
  cheap (~1-3 min each) but the appliance-image build fires twice
  per release (main push + tag push) so the real per-release cost
  was ~8.7 min, not the ~1.5 I'd estimated. At our shipping pace
  that would have burned the remaining GitHub Actions allowance in
  3-5 days.
- **Second runner container added** (`github-runner-wattpost-2`) so
  a long pi-gen build doesn't block the fast Docker / source-tarball
  builds that fire on the same tag push.
- Effective GitHub Actions minutes per release: **0**. (Plus
  redundancy on the VPS. Either runner can pick up either kind of
  job.)

### Appliance code unchanged from v0.0.26.

## [0.0.26] · 2026-05-16

### Changed
- **Pi-gen SD-image build now runs on our self-hosted Contabo VPS
  runner**, not the GitHub-hosted shared pool. Eliminates the
  ~90-minute hit each release was taking on the ritualnorth
  account's 3000 GH Actions min/mo allowance. Pi-gen is now
  effectively free.
- **Docker GHCR build + source-tarball publish** stay on GitHub-
  hosted runners. They're fast (~45 s + ~30 s) so the minutes
  cost is negligible, and keeping them on GitHub means Docker
  releases still ship even if the VPS is down.
- Restored the pi-gen trigger to all `v*` tags (we'd briefly
  restricted to `v<major>.<minor>.0` only as a minute-saver ·
  no longer needed).

### Appliance code unchanged from v0.0.25.

## [0.0.25] · 2026-05-16

### Added. USB GPS support (#125)
- **New `gps:` config block** for mobile/van installs. Daemon
  reads NMEA-0183 from a configured serial port (typically
  `/dev/ttyACM0` for a USB-CDC receiver like the VK-162 G-Mouse).
  No external dependency on `gpsd`. Pyserial + a minimal RMC
  decoder in `solar_monitor/gps/nmea.py`.
- **Significant-move detection** via the haversine distance from
  the last applied fix. Defaults: >5 km from previous applied
  fix OR >30 min stale → triggers a one-shot re-fetch of weather
  + Open-Meteo PV forecast at the new coordinates.
- **Solcast is intentionally not re-fetched on moves**. It's
  site-based (see `project_target_customer` in agent memory and
  the #130 release notes). When GPS is active, switch your
  forecast provider to `openmeteo` for moving-van support.
- **In-memory location updates only.** We mutate
  `config.weather.lat/lon` and (for Open-Meteo) `config.
  forecast.lat/lon` at runtime; we DON'T rewrite config.yaml on
  every move (would write hundreds of files a day in a moving
  van). The original config-file values are the cold-start
  fallback.
- **`GET /api/gps` status endpoint**. Surfaces `configured`,
  latest fix, fix age, last-applied lat/lon. Settings UI panel
  will land in a follow-up commit; for now enable by adding a
  `gps:` block to config.yaml and restarting the daemon.

### Configuration example
    gps:
      port: /dev/ttyACM0
      baudrate: 9600           # default; usually fine for u-blox

### Notes
- VK-162 G-Mouse (£8 puck w/ magnetic base, 1 m USB cable) is the
  recommended receiver. Better satellite reception than a USB
  stick because the puck can sit on the van roof.
- Wizard support (the "GPS support coming soon" button currently
  shown after USB-scan detects an NMEA-emitting device) will be
  wired in a follow-up once a customer has end-to-end-tested the
  serial → fix → re-fetch path with real hardware.

## [0.0.24] · 2026-05-16

### Added. Output schedules (Phase B of #104)
- **Cron-style local schedule engine** for any controllable output.
  Three trigger kinds: `time` (fires at fixed HH:MM in the
  appliance's local timezone), `sunrise`, `sunset` (both with a
  ± minute offset, sourced from the cached Open-Meteo sunrise/
  sunset timestamps. Sun-relative triggers silently skip when
  weather isn't configured). Day-of-week mask (MTWTFSS bitmask)
  gates which days a rule fires.
- **Ticks once per poll cycle** alongside the existing outputs
  state refresh. Schedules dedupe within a day via `last_run_at`
 . A daemon restart won't re-fire today's already-run rules.
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
  Lazy-loaded. The schedule list isn't fetched until the user
  taps the section, so users who only want the instant toggle
  pay no overhead.

### Closes the #104 saga
Phase A (instant toggle) shipped in v0.0.12. Phase B (schedules)
ships now. Phase C (cloud-fire. Pro tier) is the only remaining
piece, deferred until cloud-side roadmap pulls it in.

## [0.0.23] · 2026-05-16

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

## [0.0.22] · 2026-05-16

### Added. Renogy coverage finished
- **Renogy 1000W/2000W/3000W pure-sine inverter driver (#135).**
  Covers RIV/RNG-INVT inverter-charger family. Exposes AC input +
  AC output (V/A/Hz), battery side, integrated MPPT side (some
  models include solar), and AC load percentage. Modbus FC03
  over the existing BT-2 / USB-RS485 transports. Register map
  from cyril/renogy-bt's `InverterClient.py`. Registered as
  `(vendor=renogy, kind=inverter)`.

### Changed. Model classifier sweep (#134)
- **Model-string classifier now recognises the full Renogy line.**
  Probe + setup-wizard now routes:
  - `RVR/WND/ADV/VNG` (any model code) → `charge_controller`
   . Covers Rover (40A/60A/100A), Rover Elite, Rover Boost,
    Wanderer (10A/30A/Li/PG), Adventurer (30A), Voyager (20A
    waterproof) and any newer SKU using the same prefix.
  - `DCC*` with a digit anywhere → `dcdc`. Covers DCC50S,
    DCC30S, DCC25S, DCC15S (plus `RNG-DCC*` variants).
  - `RBT*` or `*LFP*` → `smart_battery`.
  - `RIV*` or `*INV*` → `inverter`.
- **Load-output discovery** in `outputs/renogy_rover.py` now
  matches bare prefixes (`RVR`, `WND`, etc.) too, so older
  firmware that drops the `RNG-CTRL-` vendor tag still gets a
  load toggle on the dashboard.

### Renogy coverage status
Effectively complete. The only gap is the Smart Shunt 300
(#113). Blocked on the lack of a community-documented register
map, will be unblocked via the discovery telemetry pipeline
(#129) or a customer-contributed Modbus capture.

## [0.0.21] · 2026-05-16

### Added. JK BMS (JiKong) support (#114)
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
  LFP crowd · 16x EVE 280Ah builds, 48V house banks, vanlife.
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
  this release. The fixture I had was hand-transcribed and
  inconsistent. First-customer validation will flush out any
  alignment issues; the code path is in place, the JK protocol
  is well-documented, and any field-position fixes are
  surgical.

## [0.0.20] · 2026-05-16

### Added. Victron coverage sweep
- **Victron SmartSolar MPPT driver (#131).** Every model from
  75/15 through 250/100. They all share one `SolarCharger` BLE
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
Phoenix / Quattro inverters). Needs a separate transport,
deferred until first customer asks.

## [0.0.19] · 2026-05-16

### Added
- **Renogy DCC50S / DCC30S driver (#123).** The DC-DC + MPPT combo
  charger that dominates mid-tier van builds. Single device with
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
 . Well-validated against real DCC50S hardware in production at
  multiple van builders.

### Notes for the user
- Two vendors now register `device_kind: dcdc`: Victron (Orion-Tr)
  and Renogy (DCC50S/DCC30S). The orchestrator resolves by
  `(vendor, kind)` tuple, so both can coexist on the same Pi.

## [0.0.18] · 2026-05-16

### Added
- **Victron Orion-Tr Smart DC-DC support (read-only).** Trivial
  follow-up to #112. Reuses the existing `ble_victron_advertise`
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
- **#113 Renogy Smart Shunt 300**. No widely-documented OSS
  register map exists; shipping a guessed driver risks silently
  returning wrong values. Deferred until either a customer
  contributes a Modbus capture, or #129 (anonymous device-discovery
  telemetry) gives us enough samples to reverse-engineer.

## [0.0.17] · 2026-05-16

### Added
- **Open-Meteo PV forecast provider**. Free, unlimited, lat/lon-
  based PV forecast that doesn't require a Solcast account. Solar
  irradiance from Open-Meteo is combined with the user's array
  geometry (capacity_kW, tilt, azimuth, system_efficiency) via a
  simple solar-position + tilt-cosine model to estimate PV output
  hourly for 7 days. Validated end-to-end against a real UK
  location. Physically sensible peak watts + day totals.
- **Settings → Integrations → PV forecast** form now has a
  provider dropdown: pick "Solcast (site-trained ML)" for fixed-
  roof installs with a registered account, or "Open-Meteo
  (irradiance estimate)" for moving vans / no-account installs.
  Each provider shows only its own field set; the picker
  swaps them. Lat/lon left blank inherits from the weather
  integration's location.
- **Why this matters**: Solcast is fundamentally site-based
  (free tier = 10 calls/day, max 2 sites, no API to register
  sites). A non-starter for moving vans + a real barrier-to-
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

## [0.0.16] · 2026-05-16

### Added
- **USB-scan now classifies each device by protocol.** The wizard's
  wired-adapter list opens each `/dev/ttyUSB*` / `/dev/ttyACM*`,
  reads briefly, and tags it as:
  - `Modbus`. Silent serial (the typical case) · "Use as Modbus" button
  - `NMEA GPS`. Emitted `$GP…` / `$GN…` sentences (preparation for
    #125 USB GPS support; button disabled with "coming soon" hint)
  - `unknown output`. Bytes seen but no recognised pattern
  - `port busy`. Already held by another process
- Stops users accidentally adding a GPS receiver as a Modbus
  transport. A £8 VK-162 G-Mouse GPS would otherwise show up
  alongside legitimate RS-485 adapters and silently fail every poll
  after pairing.

### Notes
- Detection is read-only (no Modbus probe write at scan time). The
  existing `/api/setup/probe` endpoint does an active slave-ID
  sweep once a Modbus transport is selected. That's where real
  device confirmation happens.

## [0.0.15] · 2026-05-16

### Added
- **"Add another transport" in the setup wizard.** Once you've got
  a transport configured, a collapsible tile under the list lets
  you wire up a second one without deleting the first. Same two
  buttons (Bluetooth / Wired USB-RS485) as the empty-state
  picker. Pairs cleanly with the underlying architecture ·
  BLE and USB serial subsystems are completely independent on
  the Pi, so a single host can run a Renogy BT-2 for the MPPT
  *and* a USB-RS485 dongle for a JK BMS at the same time without
  contention.

## [0.0.14] · 2026-05-16

### Added
- **Setup wizard now also finds USB-RS485 adapters.** Phase 1 of
  the unified-wizard work (#120). The "no transports configured"
  empty state now has two paths: "Bluetooth (e.g. Renogy BT-2)"
  (existing) and "Wired (USB-RS485 adapter)" (new). The wired
  path enumerates every `/dev/ttyUSB*` / `/dev/ttyACM*` the host
  sees, labels each with the chip (FTDI FT232 / WCH CH340 /
  Prolific PL2303 / Silicon Labs CP210x), and the user picks one
  with a single tap. Add-transport writes a `serial_modbus` block
  with sensible defaults (9600 baud, 8N1. Renogy/Epever standard).
- **Why this matters**: replacing the BT-2 dongle with a wired
  USB-RS485 dongle (~£10) gives sub-millisecond round-trips, no
  BLE timeouts, and proper FC06 ack frames (fixing the silent-ack
  quirk we hit during #104 de-risk). It also opens the door for
  customers who don't have line-of-sight BLE to their kit. Cabin
  installs, gear in a metal-roof barn, etc.

### Notes
- Phase 1B of #120 (Victron / JK BMS pattern-specific forms in
  the wizard) lands in a follow-up. The current Victron driver
  (v0.0.13) still needs manual YAML config; #118 tracks that gap.
- See the wizard's new tooltip: the RJ45 port on chargers is
  **RS-485, not Ethernet**. Cat5 from there terminates at a
  USB-RS485 dongle on the Pi, NOT the Pi's network jack.

## [0.0.13] · 2026-05-16

### Added
- **Victron SmartShunt support (read-only).** The BMV-style
  battery monitor. Voltage, current, SoC, time-to-go, consumed
  Ah, aux input (starter/midpoint/temperature), model + alarm
  state. Now lights up the same dashboard tiles as our other
  vendors. New BLE transport `ble_victron_advertise` runs a
  passive BleakScanner that decrypts Victron's Instant Readout
  advertisements via the per-device key (find it in
  VictronConnect → Product info → Show device key). New vendor
  `victron` with driver `shunt`. Validated end-to-end against
  the `victron-ble` library's upstream test fixtures. Every
  field decodes correctly.
- Adds `victron-ble>=0.10` as a dependency.

### Notes for early adopters
- v0.0.13 ships the engine. A wizard flow for adding a Victron
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
  Heavy-Victron customers live on VRM/Cerbo. Chasing them is a
  rabbit hole we won't go down. See `project_victron_scope` in
  the AI's memory for the strategic call.

## [0.0.12] · 2026-05-16

### Added
- **Renogy MPPT load-output toggle.** Rover-family chargers (Rover
  / Wanderer / Adventurer / Voyager) now expose their 12 V load
  terminal as a controllable output on the device-detail page.
  Toggle button writes register 0x010A via FC06 and confirms the
  new state via an explicit FC03 read-back inside the same BLE
  session. Works around the BT-2 dongle quirk where Rover
  firmware 3.x silently swallows FC06 ack frames. Confirmed
  end-to-end against a real RNG-CTRL-RVR40 FW 3.1.0.
- **One-shot safety gate** before the first toggle on any output:
  the panel explains what's about to happen ("write command to
  your charger, the load terminal will switch") and the user has
  to acknowledge before any control surface appears. Persisted
  per-output. Won't nag on every visit.
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
  uses it (Phase B of #104). The schema's lighter to evolve
  if it ships in one shot.

## [0.0.11] · 2026-05-16

### Added
- **Right-now tile now shows the next 8 hours.** Apple-Weather-
  style hourly strip at the bottom of the panel: HH:00 label,
  a tiny WMO icon (sun / partly cloudy / cloud / rain / snow /
  thunder, switched to a moon for night-time clear skies), and
  the predicted °C for each cell. Pulled from the same
  Open-Meteo fetch as the current conditions. One extra HTTP
  param, no new provider. So refresh cadence + auth-free
  setup is unchanged. The strip is hidden if the provider
  doesn't return hourly data, and scrolls horizontally on
  narrow viewports rather than wrapping.

## [0.0.10] · 2026-05-16

### Changed
- **Dashboard tile redesign. Today is now the headline.**
  The standalone "Tomorrow" tile is gone; its content folds
  into the Today panel as a sub-line. The Today panel now
  shows kWh-so-far as a big hero number, with a forecast
  sparkline running across the day (solid for the past,
  dashed for "still to come") and a faint "now" marker. The
  sub-line tells you what was expected and what's still to
  come ("Of 3.8 kWh expected · 1.4 kWh still to come"). The
  Tomorrow preview drops to a one-line footer at the bottom
  of the tile.
- **Sunset flip.** After dusk. When no PV is forecast for
  the rest of today and tomorrow's window has data. The
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

## [0.0.9] · 2026-05-16

### Fixed
- **Bumped the `/web/app.js?v=` cache-buster** in index.html.
  Several recent appliance fixes (Settings → About row visibility,
  history chart forecast bound, Check-now button focus state)
  were sitting unread in the container because the script-tag's
  version query hadn't moved since v0.0.5. Cloudflare's edge
  was serving the same URL out of its 4h cache regardless of
  what the container actually held. From now on the index.html
  `?v=` must move in lockstep with `sw.js` CACHE_VERSION so
  every JS update gets a fresh URL that bypasses any CDN cache.

## [0.0.8] · 2026-05-16

### Fixed
- **History chart: the forecast overlay no longer stretches the
  x-axis past the selected range.** Picking "6h" used to render
  a week-wide axis because Solcast's full 7-day forecast was
  appended. The forecast horizon now mirrors the chosen history
  window (1h history → 1h forecast, 24h → 24h, etc).
- **"Check now" button on Settings → About stops looking
  pressed** after the action completes. It was the iOS focus
  ring sticking; we now blur the button when the work returns.
- **iOS Safari picks up appliance updates faster.** Service
  worker registration now uses `updateViaCache: 'none'`, so
  Safari fetches the SW file fresh on every page load instead
  of holding the previous version's cached copy for hours.

## [0.0.7] · 2026-05-15

### Fixed
- **Settings → About uptime now reports the daemon's uptime**,
  not the host's. The previous `/proc/uptime` read leaked the
  host machine's uptime through Docker. A freshly-restarted
  container could show "3d 23h" if that's how long the laptop
  had been booted.
- **"Updates: docker compose pull..." row only shows when
  there's actually an update pending.** Used to render
  permanently on every Docker install, even with nothing to
  apply. Read as a nag.
- **Fresh installs land in the setup wizard automatically.**
  First-time users opening the dashboard with zero transports
  configured used to see an empty dashboard with a "Setup
  needed" pill top-right and no signpost. They now get
  redirected straight to `#/setup` on first paint.

## [0.0.6] · 2026-05-15

### Added
- **Cloud dashboard shows weather + PV forecast per site.**
  Each heartbeat now ships the appliance's cached weather
  snapshot (temperature, conditions, sunset) and Solcast
  forecast totals (today + tomorrow kWh). The cloud card
  surfaces a quiet strip. E.g. *"☀ 16°C · Mostly clear ·
  Sunset 19:42 · Today 4.2 kWh PV · Tomorrow 5.1 kWh"*. So a
  glance at app.wattpost.io tells you whether your off-grid
  setup is going to make it through the day.
- **Appliance reports its install method** in the heartbeat
  (`pi` vs `docker`). The cloud uses this to hide the
  "Update now" button on Docker installs (where the action
  has to happen on the host via `docker compose pull`).

### Changed
- Update notes pulled from `releases.wattpost.io/CHANGELOG.md`
  remain the same source as before. This entry will appear
  in the dashboard's "Release notes" link.

## [0.0.5] · 2026-05-15

### Changed
- **Setup wizard BLE scan now flags "recently visible but
  missing" dongles**. When a BT-2 was seen in the last 15 min
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
- **Docs grid overflowed viewport** on narrow mobile screens ·
  wide tables now horizontally scroll, grid cells respect
  viewport width.

## [0.0.4] · 2026-05-15

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
- **Per-device delete button on the appliance Devices tab** ·
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
- **BLE self-heals stale BlueZ state** on connect failures ·
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
  appliance. SSE snapshot's `poll_run` was missing the
  `transports` field, so every tick reset the dashboard's
  view to "no transports configured".
- **Pairing flow re-introduced "Restart daemon"** UX after the
  hot-start path was added; UI now respects the
  `restart_required: false` response.
- **About → Update section** showed "Latest available ·" and
  a stuck "Update progress: waiting…" on Docker after earlier
  manual Update-Now clicks. Both rows now hide when there's
  nothing to apply.
- **Setup wizard locked users out** when the BLE link was
  idle-dropped. The transport row went disabled with no
  recovery. Now: row stays clickable, scan auto-reopens the
  link.

## [0.0.3] · 2026-05-15

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
  entry. Bundled docs only cover versions ≤ the running release.
  Falls back to bundled `docs/release-notes.md` when offline.

### Changed
- Settings → About: Docker installs no longer show an in-app
  "Update now" button. They get a persistent hint to run
  `docker compose pull && docker compose up -d` on the host
  (matches Immich / Pi-hole / Vaultwarden conventions). Pi
  installs are unchanged.

## [0.0.2] · 2026-05-15

### Added
- WattPost cloud (wattpost.io). Opt-in. Pair the appliance to a
  cloud account from Settings → Integrations → WattPost cloud,
  paste an 8-character code, daemon exchanges it for a long-lived
  bearer token and starts pushing 5-minute heartbeats. Cloud's
  multi-site dashboard shows online/offline per appliance and
  flags overdue heartbeats. Local appliance keeps working with
  no internet, no cloud, no account. Strictly additive.

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
- Dashboard "7-day outlook" strip below the Tomorrow tile ·
  per-day kWh + mini sparkline across all forecast days, common
  Y scale so quiet days read as quiet next to sunny ones, the
  Tomorrow card highlighted as the focal point.
- History chart's forecast overlay now renders Solcast's
  P10–P90 confidence band as a translucent amber fill between
  the bounds. Wide band = the model isn't sure, narrow = high
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
- Local alert engine. Rule schema (metric / op / threshold / severity /
  cooldown), Settings → Alerts UI editor (rules + transports), per-rule
  Test button
- Notification transports: ntfy, Discord webhook, generic webhook,
  SMTP / email, MQTT-publish (LAN-local), Pushover
- CSV export of any metric over any range
  (`/api/devices/{label}/history.csv`)
- PWA install. Manifest + service worker, dashboard installs to home
  screen on iOS / Android
- Tailscale auto-config. Sudoers entry, `tailscale serve` for HTTPS,
  Settings → System surfaces the auth URL
- In-app docs (`/docs/...`) rendered from bundled Markdown. No
  external site needed
- Diagnostics. Settings → System shows recent log lines + a Restart
  daemon button (no SSH required)
- Kiosk mode · `#/kiosk` chrome-free SoC + flow tiles, Settings toggle
  to default-on for one device, Wake Lock keeps the screen on
- WebSocket / SSE live updates. Dashboard streams snapshots after
  every poll instead of polling every 5s
- BLE discovery wizard. Setup page scans an open transport for new
  slave IDs and appends them to config.yaml
- Home Assistant MQTT discovery topics
- packaging/install.sh + systemd unit + pi-gen stage for SD-image
  builds

### Changed
- BLE transport now auto-recovers from "device not advertising"
  timeouts on daemon restart by clearing BlueZ's stale connection
  state (`bluetoothctl disconnect`) and retrying once
- Tailscale endpoints surface real sudo errors to the UI instead of
  returning `ok:true` and only logging. Enable HTTPS / Connect /
  Disconnect now show a username-aware fix-it hint
  (`packaging/dev-sudoers.sh` for dev shells, re-run `install.sh`
  for production `wattpost` user)

## [0.0.1] · 2026-05-12
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
  - **Power flow strip**. Data-driven from device kinds. Sources →
    Battery → Loads, animated arrows, energy-balance "Load" tile that
    captures bus-wired consumption invisible to the charge controller
  - Today strip (PV / charged Ah / peak / **real load** / lifetime)
  - Cell-balance panel with per-cell chips, min/max highlight, panel
    hue follows drift severity
  - History chart (uPlot, vendored offline) routing to the right
    rollup table by range
  - Device detail cards with kind icons + firmware + serial
  - Section header icons, status pill icon (✓ / ⚠ / ✗)
  - Conditional alert banner. Hidden when healthy; surfaces low SoC,
    cell drift, over-temperature, comms loss, transport errors
- MQTT exporter (aiomqtt-based). Full device snapshots + per-metric
  topics, retained, with LWT `_status` topic for online/offline
- Tailscale-friendly: serves on 0.0.0.0, no TLS required for LAN, no
  cloud touched
- Firmware + serial decoded from registers and surfaced in device
  cards + MQTT + API
- Per-panel color hues (hero follows SoC band, cell-balance follows
  drift, power-flow has source→storage tint gradient)

### Notes
- Bank current rounds at 0.01 A per pack. Small trickle currents
  (< ~0.5 A on a single 100 Ah pack) show as zero. Not a bug, a BMS
  resolution limit.
- Renogy load output (`load_power_w`) is intentionally not used as
  the primary load number; bus-wired loads (the common case) need the
  energy-balance approach.
- The 32-ish watts of "Other loads" you see when nothing's running is
  real phantom draw (inverter standby, BMS overhead × 3 packs, Hub +
  MPPT self-consumption). Most apps hide this. We don't.
