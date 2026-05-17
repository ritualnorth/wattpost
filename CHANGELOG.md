# Changelog

All notable changes to solar-monitor. Format: [Keep a Changelog].
Versions follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

## [0.0.81] — 2026-05-17

### Fixed — Today's LOAD always showed 0 Wh on multi-source installs
`today_aggregate` had two bugs that conspired to hide load:

1. PV "today" came from a hardcoded `device = 'rover_mppt'` SQL
   query — but the actual MPPT device label varies (most installs
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
  - `sources_today_wh` — total energy into the bus today
  - `ac_charger_today_wh`, `dcdc_today_wh` — per-source breakdown
  - `pv_today_wh` — unchanged name, now correct

The "Today" headline kWh on the dashboard now uses
`sources_today_wh` instead of just PV — a Victron-only or
AC-charger-only install reads correctly instead of "0.0 kWh".

Verified on Ritual North's install: was showing 0 Wh load, now shows
338 Wh — matches the ~100 W background draw over the hours
his Victron has been online.

### Known minor — Hero vs Flow 12 W sampling skew
The hero "Net power" tile and the flow strip's "Battery bank"
read from the same `devices` array but render on slightly
different ticks. A live install that's actively MPPT-tracking
will show small (~5-15 W, ~0.1 % SoC) drift between the two
tiles for a poll cycle. Cosmetic; both numbers are correct for
the snapshot they were taken from. Future task to lock both
renders to a single snapshot.

## [0.0.80] — 2026-05-17

### Fixed — Victron AC charger labelled "Other source" in flow strip
The dashboard's Power-Flow strip mapped device kinds to source/load
tiles via the `FLOW_MAPPING` table. None of the Victron-specific
kinds shipped in #112/#118 (ac_charger, dcdc_xs, bms,
load_disconnect) were in that table, so the Victron's real
output_1_power_w reading was ignored and the energy-balance
inference kicked in instead — showing "Other source · estimated"
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
when there's a GENUINE energy gap we can't measure — which is
its actual job.

Multi-output AC chargers (output_2 / output_3 in addition to
output_1) only render output 1 right now. Rare in van/cabin
installs; expand if a customer requests it.

## [0.0.79] — 2026-05-17

### Fixed — Victron transports left no device polled
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
one-click — Pair → key → Save → data flowing. No second config
edit needed. Existing customers who paired before this release
need to add a device row manually OR delete + re-pair via the
new wizard flow.

Hit by Ritual North pairing his BSC IP22 12/15 on the new VM appliance.

## [0.0.78] — 2026-05-17

### Fixed — Victron transport perpetually reported OFFLINE
The `/api/setup/transports` endpoint determined "open" state by
checking `transport._client.is_connected`, which only Modbus-style
transports have. The passive Victron transport
(`ble_victron_advertise`) has no GATT client to "open" — it just
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

## [0.0.77] — 2026-05-17

### Fixed — Victron encryption-key form unusable on mobile
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
from the transport list — if you added it with the wrong key (or no
key), trash-icon-delete + rescan + re-add. PATCH endpoint on
existing transports is a backlog task.

## [0.0.76] — 2026-05-17

### Security — kiosk-share URL no longer leaks dashboard chrome (#150)
The public kiosk URL (`/kiosk?key=<token>`) captured the token into
KIOSK_KEY_PARAM in memory, then the "Exit Kiosk" button just changed
the SPA hash to `#/`. Token stayed in memory, every subsequent api()
call appended `?key=`, the Caddy @kiosk_open bypass + appliance
kiosk allow-list happily served data — so a kiosk-share visitor
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

### Security — cloud-side hardening (#155, #156)
Ship in the cloud (auto-deploys to wattpost.cloud on push), documented
here for visibility:
  - #155: /healthz/deep no longer leaks raw user / appliance counts.
    Returns a boolean `checks.heartbeats = "ok" | "stale"` instead.
    Public /status page reworked to show health states, not numerics.
  - #156: /api/heartbeat now rate-limited to 60/5min/IP. Real
    appliances heartbeat 1/5min so this is 60× headroom; brute-force
    against bearer tokens hits a wall quickly.

## [0.0.75] — 2026-05-17

### Fixed — Appliance sessions wipe on container restart (#149)
The local-auth session dict lived in process memory only, so every
restart (Update now, Settings → Restart daemon, customer power-cycle)
silently logged everyone out. The SPA's cached "you're authed" state
then disagreed with the empty server-side store and any state-changing
API call returned "login required" — surfaced via a customer reporting
that "Send heartbeat" failed even though Settings was open.

Sessions now persist to /etc/wattpost/sessions.json (same config dir
as web-password.hash). Read-through cache: every issue/revoke writes
the dict to disk via atomic write-temp-then-rename. Module-import
loads the file back, expired entries dropped on load. Storage cost
is trivial (a typical install holds a handful of sessions, ~100 bytes
each). Disk write failures degrade to in-memory-only with a warning
log — never breaks login.

Side benefit: also fixes the related #148 sso_secret divergence —
restart-to-recover is no longer needed because no state is held only
in memory.

### Security — cloud-side hardening (#152, #153, #154)
These ship in the cloud (auto-deployed to wattpost.cloud on push to
main), not the appliance. Documented here for visibility:
  - #152: signup is now always-202 regardless of email existence;
    eliminates user-enumeration signal.
  - #153: password policy = 10+ chars, ~50-entry common-password
    blocklist, HIBP k-anonymity check.
  - #154: /schema (OpenAPI) gated behind a SESSION_SECRET-derived
    randomised path in prod; default `/schema` in dev only.
Found in the pre-launch pentest.

## [0.0.74] — 2026-05-17

### Fixed — Dashboard stuck at "connecting" when accessed via cloud broker on iOS Safari
On the broker URL (`<slug>.wattpost.cloud`), the dashboard would load
the shell + tabs but every tile stayed empty and the status pill
stayed at "connecting…" forever. Confirmed in headless Chromium that
the JS code was fine — render succeeds when the page is allowed to
breathe. The trap was iOS Safari's HTTP connection pool: a long-lived
EventSource through Cloudflare Tunnel holds a connection open, and
Safari serialises subsequent /api/* fetches behind it, so refresh()
never resolves and the pill never flips.

Fix: in `wireSignout`'s auth-status callback, detect `origin === "broker"`,
close any open EventSource, and start the 5 s polling fallback instead.
LAN access keeps SSE (fresh local connection, no CF in the path, no
pool starvation). The reroute is transparent — same data shape via the
same `applySnapshot`, just delivered by REST poll instead of stream.

Caught while writing a headless-Chromium reproduction with synthesized
broker headers (CF-Ray + freshly-minted HMAC). Before fix: page navigation
timed out waiting for networkidle, status pill stuck on initial HTML
default. After fix: clean 200, pill flips to "Healthy", real data renders.

## [0.0.73] — 2026-05-17

### Fixed — "Idle" shown when slow-charging from PV
The 1.5 A "Idle" guard added in v0.0.70 was applied symmetrically
to both charge and discharge currents. That broke the charging
case: a battery taking +1 A from a low-output MPPT was labelled
"Idle" — but it's charging, just slowly. Customer-confusing:
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

## [0.0.72] — 2026-05-17

### Fixed — Battery health endpoint 500'd on any window > 6 hours
`battery_health_aggregate` referenced an `avg_value` column when
falling back to the rollup tables (samples_1min / samples_1hour /
samples_1day) — but those tables store the averaged value in a
column called `avg`. SQLite returned "no such column: avg_value"
and the endpoint 500'd for the default 30-day window (the only
window the UI ever requests). Customers saw a permanently broken
Battery health tile and — because the JS dashboard refresh fires
the same call on boot — a blank dashboard until the request
eventually settled. Net effect: feature shipped in #109 silently
broken for everyone on the rollup window. Caught while debugging
a "blank dashboard after broker login" report.

## [0.0.71] — 2026-05-17

### Fixed — "Remaining" tile showed instant rate, not realistic forecast
The forecast-aware overlay ("Forecast: ~6 h until 10% at 19:30")
that walks PV forecast vs avg load was supposed to handle the
"2d 5h until empty" misleading-instant-rate case. But
`/api/runtime-forecast` was returning 404 because the function
existed in api/app.py but was never added to the route_handlers
list — same bug class as #109. JS silently hid the forecast line
on the 404, leaving only the naive instant-rate reading. Pure
registration fix.

## [0.0.70] — 2026-05-17

### Changed — Saner "until empty" estimate
At standby loads (sub-1.5 A net), the naive `capacity ÷ current`
estimate produced laughably long times ("2 d 5 h until empty"
when the server was just idling). Bumped the "idle, don't show
runtime" threshold from 0.5 A to 1.5 A and subtract a 10 %
reserve from the headline number, so the displayed figure
matches what the user can practically use.

## [0.0.69] — 2026-05-17

### Fixed — Battery health panel was rendering empty (route never registered)
The `/api/battery-health` handler existed in api/app.py since #109
shipped, but I never added it to the Litestar `route_handlers`
list. Result: panel-battery-health on the dashboard called it,
got 404, JS gracefully fell back to "—" placeholders, panel
looked permanently broken. Added the registration; cycles +
lifetime + window cycles + SoC residency histogram now populate
from the BMS + heartbeat history.



## [0.0.64] — 2026-05-17

### Changed — Removed Sign In / Sign Out buttons from appliance header
Both were ugly clutter. The auth model since v0.0.58 only gates
Settings + Setup; tapping either bounces to /login automatically.
A Sign In button at the top was redundant. Sign Out moved inside
Settings → System (only visible when actually signed in) — that's
where you'd realise you want to drop the session anyway.

Dashboard / History / Devices / Kiosk / Docs all stay completely
anonymous-readable on LAN — no chrome, no buttons, just data.

app.js v=130, sw.js CACHE_VERSION bumped.

## [0.0.63] — 2026-05-17

### Fixed — CRITICAL: db_path field was missing from Config, so v0.0.60 fix did nothing
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

### Changed — Service worker evicts old caches on activate
Was leaving every prior cache version on disk forever. Now
deletes anything that isn't the current CACHE_VERSION on
activate. Belt-and-braces alongside skipWaiting + clients.claim
to keep "stale UI being served from cache" from biting.

## [0.0.62] — 2026-05-17

### Added — Settings → Kiosk share URL panel
Surfaces the per-appliance public share URL the cloud dashboard
already builds, plus a Rotate button for one-click revocation
when the URL leaks. Reads via GET /api/system/kiosk; rotates via
POST /api/system/kiosk/rotate (already shipped in v0.0.61).
Block hides itself when the appliance has no cloud tunnel (no
slug = no public URL).

app.js v=129, styles.css v=103, CACHE_VERSION bumped.

## [0.0.61] — 2026-05-17

### Added — Tokened kiosk share URL (Option C)
The cloud's "Kiosk" button used to copy a raw tunnel URL that
didn't actually work via the internet (HTML loaded but every
data fetch 401'd at the appliance — see earlier analysis). Now:

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
  the kiosk page actually reads — strict, no API back-door.
- Kiosk-mode JS captures the `?key=` once at page load + appends
  it to every subsequent /api/* fetch.
- POST /api/system/kiosk/rotate generates a fresh token + returns
  the new share URL. Old token immediately stops working
  (revocation = "I leaked the URL, kill it").

Pre-v0.0.61 appliances haven't shipped a kiosk_token yet; the
cloud dashboard falls back to the legacy direct-tunnel URL (LAN-
only). Updating the appliance + waiting one heartbeat fixes the
share button.

## [0.0.60] — 2026-05-17

### Fixed — CRITICAL: Docker users lost ALL history on every image pull
config.db_path was settable but the daemon completely ignored it.
`cmd_serve` always passed `args.db` (default `solar-monitor.db`)
to build_app, which resolved to `/app/solar-monitor.db` inside
the container — i.e. the IMAGE's ephemeral writable layer, not
the bind-mounted /var/lib/wattpost volume. Every
`docker compose pull && up -d` swapped the image → /app gone →
every metric the user had ever collected, vanished.

`_resolve_db_path` now picks (in order): explicit --db arg →
config.db_path → CLI default. Pi installs are unaffected (their
default db_path lands in /var/lib/wattpost anyway via the
systemd unit). Docker installs with a v0.0.60+ image will now
write to the bind-mounted volume + survive image upgrades.

### Migrated — Legacy in-image-layer DB → persistent path
On startup, if config.db_path points somewhere new but the legacy
./solar-monitor.db exists at the daemon's CWD, the file gets
copied to the new location and the source renamed to
.legacy.bak (preserved for one container restart in case
anything goes wrong). One-shot, idempotent.

Anyone whose container has been crash-looping since v0.0.56
(see v0.0.59 hotfix) and has no DB at the legacy path either:
nothing to migrate, fresh start unfortunately.

## [0.0.59] — 2026-05-17

### Fixed — CRITICAL: appliance crash-loop on startup (v0.0.56–v0.0.58)
The auth_status handler I added in v0.0.56 declared `async def
auth_status(request)` without a type annotation. Litestar's
signature scanner refuses to start the app when a route parameter
lacks a type — every container running :latest after v0.0.56 has
been crash-looping (alembic-style "ImproperlyConfiguredException:
'request' does not have a type annotation"). Anyone on Update-now
since this morning needs v0.0.59 immediately.

Annotated `request: Request` and imported it. Local smoke test
passes. Stable on every install path again.

## [0.0.58] — 2026-05-17

### Changed — Settings / Setup tabs require sign-in (UX gate)
The previous READONLY_PUBLIC model lets GET requests through on
LAN without a session and gates only mutations. That worked but
landed users in a confusing state: tap Settings → page renders →
click Save → 401 → no signal of what went wrong.

New model: Settings + Setup tabs are gated client-side. Tapping
either when not signed in redirects to /login?next=<route> and
bounces back after auth. Dashboard / history / devices / docs /
kiosk are still anonymous-readable on LAN — kiosk-on-wall
deployments and family-on-WiFi viewing still work without a
password.

This is a UX guard, not a new security boundary. The server-side
mutation gate (POST/PATCH/DELETE → session required) remains.

app.js v=127, CACHE_VERSION bumped.

## [0.0.57] — 2026-05-17

### Added — Sign Out button in the appliance header
There wasn't one. The dashboard had a Sign In button (broken in
its own way until v0.0.56) but no way to *un*-sign-in. Ritual North
reported the appliance UI never asked him to log in (the
READONLY_PUBLIC bypass lets GET requests through on LAN without
a session, by design — so the SPA loads, no login prompt) and
that there was no logout affordance.

Both Sign In + Sign Out live in the header now. Auth-status
endpoint decides which one to reveal: authed → Sign Out, not
authed → Sign In. Demo mode suppresses both.

### Added — Diagnostics bundle download (#138)
"Download bundle" button on Settings → Diagnostics. Single JSON
file: version, deployment (pi|docker), platform, uptime, disk,
redacted config (bearer_token / tunnel_token / sso_secret /
api_key / password values scrubbed), transport+device counts,
last poll result, ~500 lines of recent logs. Suitable to attach
to a support ticket.

## [0.0.56] — 2026-05-17

### Fixed — Sign-in button always shown to authenticated users
The header's "Sign in" affordance gated on `document.cookie.includes
("wp_local_session=")` to decide whether to show. Trouble: the
session cookie is HttpOnly (XSS protection — correct), so JS can
never see it. Every authenticated user saw the button.

Replaced the cookie sniff with a tiny `/api/system/auth-status`
endpoint (anonymous-readable, returns `{authed, origin}` from the
real session table). JS fetches it on load and only reveals the
button when there's genuinely no session. Affects SSO-via-cloud
users + LAN password sign-ins equally.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

## [0.0.55] — 2026-05-17

### Fixed — Appliances paired pre-rebrand silently failed heartbeats
Appliances paired before the wattpost.io → wattpost.cloud rebrand
have `cloud.endpoint: https://app.wattpost.io` baked into their
local config. That hostname now 301s at the Cloudflare edge, and
httpx (correctly) strips the `Authorization` header when following
a cross-host redirect — so the bearer never reached wattpost.cloud
and every heartbeat 401'd. Appliance showed offline despite working
locally + having a valid bearer + the cloud being healthy.

config.load_config now auto-upgrades any legacy endpoint
(`https://app.wattpost.io`, `https://wattpost.io`) to
`https://wattpost.cloud` and persists the change back to the YAML.
Affected appliances heal themselves on next daemon start. New
pairings already default to wattpost.cloud (CloudCfg.endpoint).

### Fixed — Cloud theme defaulted to dark regardless of device
The inline theme bootstrap in _base.html.jinja defaulted to "dark"
(via the `default_theme` block) when no localStorage preference was
set. So a light-mode user landing on the dashboard saw dark forever
until they manually visited /app/account and picked "System". Now
defaults to "system" so OS preference is honoured from first visit.

## [0.0.54] — 2026-05-16

### Added — Staff admin page (#103, MVP)
New /app/admin (staff-only, returns 404 to non-staff so the page's
existence isn't leaked). Three tabs:

- **Users** — last 500. Toggles for `require_2fa` and `is_staff`.
  Critically, this is the in-app escape hatch for a 2FA-enrolment
  lockout — the only previous fix was SSH + psql (see today's
  incident). Self-demotion via the UI is refused — has to be done
  manually to prevent accidental admin-lockout.
- **Appliances** — last 500 with owner email, online flag (any
  heartbeat in 15 min), tunnel link. "Is the fleet healthy"
  eyeballing.
- **Audit log** — last 200 events across all users, filterable by
  email / event_type / IP. Failed sign-ins highlight in amber.

All staff-side writes get their own audit entry (`staff.user.patch`)
recording who changed what on whom, so admin actions on real users
have a clean trail.

The topbar now reveals an "Admin" link client-side via /api/me
when the user is staff. Non-staff and anonymous visitors never
see it.

### Fixed — audit_events FK actually altered in prod (migration 0024)
v0.0.52 edited the already-applied migration 0023 to flip CASCADE
→ SET NULL, but that edit had no effect on the production DB.
Migration 0024 performs the real ALTER so account.delete records
actually outlive the user row in prod.

## [0.0.53] — 2026-05-16

### Fixed — 2FA enforcement could 403 /api/login itself
Defense-in-depth on top of v0.0.52: auth-transition endpoints
(/api/login, /api/logout, /api/signup, /api/account/password/forgot,
/api/account/password/reset and their HTML page counterparts) are
now always reachable, regardless of `require_2fa` enrolment state.

Previously, a user with `require_2fa=true`, no TOTP enrolled, and
a stale session cookie would get 403'd on `/api/login` — meaning
they couldn't even start a fresh login from the same browser to
escape the loop. Now they always can.

## [0.0.52] — 2026-05-16

### Fixed — 2FA enforcement allowlist locked users out of enrolment
The require-2FA middleware's allowlist used the wrong path prefix
(`/api/twofa/` instead of the actual `/api/account/2fa/`), so a
user flagged `require_2fa=true` who hadn't yet enrolled TOTP would
get 403'd from the very endpoints needed to enrol — locking them
out of their own account with no escape hatch. Fixed the prefix +
added a comment loud enough to prevent a repeat.

### Added — Security events page on /app/account (#144)
- "Recent security activity" card on the account page renders the
  last 50 audit events from the API stood up in v0.0.50: sign-ins
  (success + failure), 2FA changes, password updates, appliance
  pair/delete, account deletion.
- Failed sign-ins highlight in amber; each row shows IP +
  user-agent + timestamp. Friendly event labels + per-category
  icons. Refreshes after revoke-sessions to confirm the action.

### Added — Audit log wired into the remaining security events
- `twofa.enrol`, `twofa.disable`, `twofa.backup_codes_regen` from
  /api/account/2fa/*.
- `account.delete` recorded before the cascade.
- `appliance.pair` from the anonymous /api/pair/exchange endpoint
  (opens its own session, captures IP/UA).
- `appliance.delete` from /api/sites/{id}.

### Changed — AuditEvent FK now SET NULL not CASCADE
So account.delete records survive the user row's deletion — admin
and fraud workflows still get the "when did account X get
deleted, from where" trail. PII is already gone with the user, so
no privacy regression.

## [0.0.51] — 2026-05-16

### Fixed — rate limiter was bucketing on CF edge IP, not real client
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

## [0.0.50] — 2026-05-16

### Added — Audit logging for security-relevant events (#144)
Common SaaS feature (Stripe, Linear, GitHub all show this). Two
purposes: customer-facing visibility into account activity +
ops-facing forensics for incident review.

- **Schema**: new `audit_events` table via migration 0023.
  Composite index on `(user_id, created_at DESC)` for the
  per-user-timeline query.
- **Helper**: `cloud/wattpost_cloud/audit.py::log_event()` —
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

## [0.0.49] — 2026-05-16

### Fixed — middleware actually fires now (was silently no-op'd)
v0.0.48 added `DefineMiddleware(...)` wrappers thinking that
fixed the v0.0.46/v0.0.47 issue of plain ASGI middleware classes
being silently ignored. It didn't. Litestar's `middleware=[…]`
expects `litestar.middleware.ASGIMiddleware` subclasses with a
`handle(scope, receive, send, next_app)` method — NOT
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

## [0.0.48] — 2026-05-16

### Fixed — middleware registration (rate-limit was silently ignored)
v0.0.46 / v0.0.47 registered ASGI middleware in Litestar's
`middleware=[Cls, Cls]` list, assuming Litestar accepts plain
ASGI middleware. It doesn't — they were loaded but never invoked
on actual requests. Caught during smoke test (6 bad logins all
returned 401 instead of 429 by the 6th). Fix: wrap each in
`DefineMiddleware(...)`. Now ALL three security middlewares are
actually in the request chain:

  - RateLimitMiddleware (since v0.0.46) — now actually rate-limiting
  - CSRFMiddleware (new this release)
  - TwoFactorEnforcementMiddleware (since v0.0.47) — now actually enforcing

### Added — CSRF protection via custom-header pattern (#142)
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

## [0.0.47] — 2026-05-16

### Security — 2FA enrolment enforcement for staff accounts
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

## [0.0.46] — 2026-05-16

### Security — hardening sprint
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
    192.168/16) — public hits get 404.
  - Still requires `X-Forwarded-Host` to end in `.wattpost.cloud`
    so we don't leak ownership info via 401-vs-403 timing.
- **Security headers on `wattpost.cloud` + `*.wattpost.cloud`** in
  Caddyfile. HSTS-preload, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy. Verified live.

### Verified
- `curl https://wattpost.cloud/app -I` → headers present
- `curl https://wattpost.cloud/api/internal/can-access` from public
  → 404 (was 400 in v0.0.45)
- Rate limiter live in middleware chain — first 5 logins/min OK,
  6th gets 429

### What's still on the audit list
- CSRF tokens on cookie-auth POSTs
- `sso_secret` column encryption at rest
- 2FA enforcement option for staff accounts
- Stripe webhook replay protection audit
- External uptime monitoring + status page (#140, #141)
- End-to-end signup → email-verify → pair re-test post-rebrand

## [0.0.45] — 2026-05-16

### Changed — Phase 3 of cloud rebrand: appliance side (#139)
Final code/doc sweep of `app.wattpost.io` references in the appliance.

- `solar_monitor/config.py` — `CloudCfg.endpoint` default flips from
  `https://app.wattpost.io` → `https://wattpost.cloud`. Existing
  pairings keep their on-disk value (still works via 308); new
  pairings point at the new domain from first heartbeat.
- `solar_monitor/update/checker.py` — `DEFAULT_MANIFEST_URL` flips
  to `https://wattpost.cloud/api/releases/latest`. Same back-compat
  story.
- `solar_monitor/api/cloud_admin.py` — pair-flow defaults and
  PUT payload defaults flipped.
- `solar_monitor/web/login-tunnel.html` — direct-tunnel-access
  block page now points at `wattpost.cloud` for "sign in here".
- `solar_monitor/web/app.js` — integrations panel's "Pair with"
  fallback URL.
- Comments + docstrings across appliance modules sweep-updated.

### Docs
- `docs/pairing.md`, `docs/kiosk.md`, `docs/release-pipeline.md`,
  `docs/cloud-architecture.md` — every customer-visible reference
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

## [0.0.44] — 2026-05-16

### Changed — Phase 2 of cloud rebrand: app.wattpost.io → wattpost.cloud (#139)
Following v0.0.43 (which stood up wattpost.cloud + the Caddy broker
in parallel), this commit completes the URL migration:

- **Cloud code sweep** — 22 hardcoded `https://app.wattpost.io/...`
  references across 13 files (verification emails, password-reset
  links, billing return URLs, marketing copy, referral URLs)
  flipped to `https://wattpost.cloud/...`. Future emails / new
  bookmarks all use the new domain.
- **Caddy app.wattpost.io block** — replaced with a single
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

## [0.0.43] — 2026-05-16

### Changed — Cloud broker rebuilt with Caddy on wattpost.cloud (#139)
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
  for free (single-level wildcards) — no paid Advanced Cert needed.
- Single eTLD+1 means session cookies set on the apex are sent to
  every subdomain (including the broker) automatically — no cross-
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
- `cloud/wattpost_cloud/api/broker.py` — the Python proxy.
- HTML shim injection logic — moot under subdomain pattern.

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

## [0.0.42] — 2026-05-16

### Added — Cloud broker (#139)
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
- WebSocket bridging isn't implemented — the appliance doesn't use
  WS today (only SSE). Add when needed.
- HTML rewriting + the JS shim are belt-and-braces; cleaner long-
  term is to ship appliance HTML/JS with relative URLs and drop
  the shim. Track as a polish item.

## [0.0.41] — 2026-05-16

### Fixed — Tunnel `/login` no longer pretends to work
- Direct tunnel URL access (e.g. someone bookmarked the tunnel
  hostname, or shared the link) used to render the LAN password
  form, accept the user's password, issue a session… that the
  middleware then rejected for every subsequent tunnel request
  because the session's origin was `local`, not `sso`. Dead end
  with no explanation.
- Tunnel-origin hits to `/login` now serve `login-tunnel.html`:
  a dedicated page that says "sign in at app.wattpost.io and
  click Open" with a CTA to the cloud dashboard. No password
  field on tunnel — there's nothing to fill in.
- `/api/login` also refuses tunnel-origin POSTs (403) — belt and
  braces in case a client-side script or a manually-crafted
  request hits it directly.

### Next
- Cloud-side broker (#139): instead of the tunnel exposing the
  appliance dashboard at `<slug>.wattpost.io`, the cloud serves
  it transparently at `app.wattpost.io/site/{id}/`. User never
  leaves the cloud session; tunnel hostname is invisible. Multi-
  day build — HTTP proxy + SSE bridging + appliance shared-secret
  for defense-in-depth. Issue tracking the design.

## [0.0.40] — 2026-05-16

### Added — In-app password reset + Sign in header link
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
  "login required" error on attempted writes — now it's a visible
  affordance the second you load the dashboard.

### API
- New `POST /api/system/web-password/rotate`. Auth: requires
  existing session (the standard write-gated path). Returns
  `{ok: true, password: <new>}` exactly once.

### Coming
- `wattpost-config` parity for Docker is a bigger lift (web port,
  reset-to-defaults, log dumps); password reset is the first
  slice. Track #138 for the rest.

## [0.0.39] — 2026-05-16

### Fixed — Settings → Cloud Save was wiping tunnel + SSO state
- The cloud config edit handler (`PUT /api/cloud/config`) rebuilt
  the in-memory `CloudCfg` from scratch using only the form fields
  the user submitted (endpoint + heartbeat_minutes), preserving
  `bearer_token`, `appliance_id`, and `label` but DROPPING
  `tunnel_token`, `tunnel_hostname`, and (newly in 0.0.38)
  `sso_secret`. Then `_serialize_cloud` wrote the slimmed-down
  CloudCfg back to `config.yaml` — wiping all three on disk.
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

Or: do nothing, re-pair from the cloud Sites page — fresh
`bearer_token` + `sso_secret` arrive in the pair response.

## [0.0.38] — 2026-05-16

### Added — Cloud→appliance SSO (#137)
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
  Closes the threat: a leaked tunnel URL is now harmless — the
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
  lifecycle. Auth lives at the appliance — anyone with the URL
  reaches the auth wall, doesn't sneak past it.
- Local password becomes the LAN fallback / break-glass route;
  no more single-token-grants-everything. See [[docker-pi-parity]]
  in agent memory.

## [0.0.37] — 2026-05-16

### Security — Docker installs were also wide open (urgent follow-up to 0.0.36)
- **The bug:** v0.0.36 closed the tunnel-via-loopback bypass, but
  Docker installs have a SECOND hole the SD image didn't have:
  `packaging/install.sh` (Pi-only) is what generates the first-boot
  password. Docker installs never ran install.sh, so
  `password_is_set()` returned False — and the auth middleware
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
     the operator gets a loud error log AND a 503 wall — no quiet
     wide-open state.
- **What customers need to do:**
  - SD-card users: nothing — install.sh already set a password.
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

## [0.0.36] — 2026-05-16

### Security — tunnel URL no longer grants anonymous access (urgent)
- **The bug:** the appliance's auth middleware treated source IP
  `127.0.0.1` as fully trusted ("the request must have come through
  the authenticated cloud session"). But cloudflared on the
  appliance proxies tunnel traffic to localhost, so EVERY tunnel
  request appeared as loopback. Net effect: anyone with the
  `{slug}.appliances.wattpost.io` URL got full unauthenticated
  read + write access to the appliance — including settings, alert
  rules, and write-through endpoints. Reported by Ritual North after he
  shared the URL with a friend who could read his appliance from
  another house.
- **The fix:** `is_loopback_source()` now sniffs for Cloudflare's
  `CF-Ray` / `CF-Connecting-IP` / `CF-IPCountry` headers and returns
  False when present. Real loopback (curl from the Pi, SSH
  port-forward, the daemon talking to itself) has none of those, so
  legitimate local-trust paths still work. New helper
  `is_tunnel_origin()` is also used to disable the `READONLY_PUBLIC`
  GET bypass for tunnel requests — a leaked URL would otherwise
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

## [0.0.35] — 2026-05-16

### Added — Forecast-aware runtime prediction (#99)
- **New sub-line on the Hero's Remaining tile.** The existing
  "until empty" was always naive: current instant power × current
  SoC. A 2 kW kettle on for 30 seconds would drag it down to a
  scary number, then bounce back. Two replacements ride below it:
  - **Forecast-aware** (when an Open-Meteo or Solcast forecast is
    cached): walks hourly through the next 48h, subtracts forecast
    PV from a rolling 1-hour avg load, and reports either an
    absolute depletion time ("~14h until 10% — 02:30 Tue") or
    "holds for the 48h window" when PV input covers the draw.
  - **Naive rolling fallback** (no forecast configured): same
    1-hour avg load but no PV — "1h-avg: ~3.2 days to 10%".
- **Why the 10 % floor**: LFP wants headroom; predicting to 0 % is
  both alarming and academic since loads cut out before then.
- **Hidden gracefully** when there's no bank capacity to predict
  from (fresh install) or no historical load to average from.

### API
- New `GET /api/runtime-forecast` returning `now`, `naive`, and
  `forecast` blocks. The forecast walk is best-effort — failures
  return `forecast.available=false` and the UI falls back to the
  naive line.

### Storage
- New `Store.rolling_load_avg(window_seconds=3600)` returning mean
  bank power over the trailing window. Negative when discharging.
  Single-query AVG across the V×I join — cheap on the rollup
  tables.

## [0.0.34] — 2026-05-16

### Added — Battery health tile (#109)
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
  Works *without* a BMS — every shunt + battery setup gets this.
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

## [0.0.33] — 2026-05-16

### Fixed — demo.wattpost.io broken since 0.0.31
- **Synthetic poller crash loop.** `_compute_bank_aggregate` started
  emitting non-numeric fields (`source: "shunt"|"bms"` and the
  `source_disagreement` dict) in 0.0.31. record_poll's bank-persist
  loop assumed every value was numeric and crashed with
  `ValueError: could not convert string to float: 'shunt'` on every
  poll. The store stayed empty; the dashboard saw zero devices and
  fell through to "Setup needed" + the wizard redirect. Fix: route
  bank fields by type — floats to `samples`, strings to
  `samples_str`, dicts JSON-encoded into `samples_str`.
- **Demo dashboard yanking visitors into the setup wizard.** Even
  with the persist fix, the demo container has zero configured
  transports (it uses a synthetic poller), so the dashboard fired
  its first-boot redirect into `#/setup`. Now gated on the
  `is-demo` body class via a new `_maybeFirstBootRedirect` helper
  that awaits the `/api/system/info` promise before deciding.

### Added — Battery health plumbing (groundwork for #109)
- Bank aggregate now surfaces `cycle_count`, `lifetime_throughput_ah`,
  and `lifetime_throughput_kwh` when one or more BMSes report them
  (JK BMS, Lynx Smart BMS — anything with `cycle_count` +
  `total_charge_ah`). Cycle count is the max across packs (worst-
  pack-defines-bank); throughput is the sum. Empty when no BMS.
- New `Store.battery_health_aggregate(since, until)` returns a 10-
  bucket SoC residency histogram + window equivalent cycles +
  days-online. No tile yet — that lands in 0.0.34.

## [0.0.32] — 2026-05-16

### Added — First-class alert rules audit (#107)
- **One-tap alert templates.** Settings → Alerts now has a "Quick
  templates" pill row. Tap a chip → add-rule form opens with the
  metric path, comparison operator, threshold, severity, and
  cooldown all pre-filled with sensible defaults. Users don't
  have to learn the metric-path schema or invent thresholds.
  Shipped templates:
  - Low SoC (< 30%) — warn, 1h cooldown
  - Critical SoC (< 15%) — alarm, 15min cooldown
  - Low voltage (< 11.5V for 12V) — alarm
  - Bank over-temp (> 50°C) — alarm
  - Cell drift warning (> 100 mV) — warn
  - Cell drift alarm (> 200 mV) — alarm
  - Shunt-vs-BMS disagreement (> 10 percentage pts) — warn,
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

## [0.0.31] — 2026-05-16

### Added — Victron pairing in the setup wizard (#118 + #120 Phase 1B)
- **BLE scan now identifies Victron, Renogy, and JK devices** by
  manufacturer ID + name patterns. Each device row in the scan
  results gets a colour-coded vendor badge:
  - 🔵 Victron — additionally shows the decoded device class
    (SmartShunt, SolarCharger, DcDcConverter, etc.) when the
    advertisement payload makes that possible (no decryption
    needed — model ID is in the public header).
  - Renogy BT-2 / BT-1 — kept the existing badge.
  - JK BMS — surfaced as a recognised device with a "manual
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

## [0.0.30] — 2026-05-16

### Added — "No-BMS" dashboard mode (#115)
- **Shunt-only installs (Persona B — see `project_target_customer`
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
    `buildShuntDetail` renderer — verified it still works.
- After this lands, a customer with a Victron SmartShunt + a
  Renogy MPPT (no BMS) gets a complete coherent dashboard. The
  budget-upgrader segment we're targeting per
  `project_target_customer` finally has the full experience.

## [0.0.29] — 2026-05-16

### Added — BMS-vs-shunt reconciliation (#121)
- **Two-layer bank aggregator.** Cell-level metrics (per-cell V,
  worst-pack drift, cell min/max) always come from BMSes — shunts
  don't have per-cell data. System-level metrics (V, A, SoC,
  remaining Ah, time-to-go) prefer the shunt when present,
  fallback to BMS pack-sum otherwise. **Previously the shunt
  branch returned early, dropping all cell-level data — fixed.**
- **Source-disagreement hint.** When both shunt and BMS report SoC
  and they differ by more than 5 percentage points, the hero tile
  shows a quiet sub-line: *"BMS 72% · shunt 65% — showing shunt"*.
  Renogy DC Home makes users pick manually; we pick the right
  source automatically *and* tell them when we're unsure.
- **Time-to-go from shunt.** When the shunt reports a Coulomb-
  counted `time_to_go_minutes`, the Remaining tile uses that
  instead of the V·I extrapolation — much better accuracy on
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
  from the snapshot when both a shunt and BMSes were present —
  meaning customers with a hybrid install lost the cell-balance
  panel data. The aggregator now keeps both layers independent.

## [0.0.28] — 2026-05-16

### Fixed
- **`pyproject.toml` pinned `victron-ble>=0.10`, which PyPI doesn't
  have** (the latest published version is `0.9.3`). GitHub-hosted
  runners had been cache-hitting through this since v0.0.13, but
  the freshly-spun-up self-hosted runners on the VPS resolved deps
  from scratch and failed the appliance + demo Docker builds.
  Relaxed to `victron-ble>=0.9`.

### Appliance code unchanged from v0.0.27.

## [0.0.27] — 2026-05-16

### Changed
- **Every offgrid-monitor workflow now runs on the self-hosted VPS
  runners**, not just pi-gen. Previously the Docker, source-tarball,
  cloud, and demo workflows stayed on GitHub-hosted runners — looked
  cheap (~1-3 min each) but the appliance-image build fires twice
  per release (main push + tag push) so the real per-release cost
  was ~8.7 min, not the ~1.5 I'd estimated. At our shipping pace
  that would have burned the remaining GitHub Actions allowance in
  3-5 days.
- **Second runner container added** (`github-runner-wattpost-2`) so
  a long pi-gen build doesn't block the fast Docker / source-tarball
  builds that fire on the same tag push.
- Effective GitHub Actions minutes per release: **0**. (Plus
  redundancy on the VPS — either runner can pick up either kind of
  job.)

### Appliance code unchanged from v0.0.26.

## [0.0.26] — 2026-05-16

### Changed
- **Pi-gen SD-image build now runs on our self-hosted Contabo VPS
  runner**, not the GitHub-hosted shared pool. Eliminates the
  ~90-minute hit each release was taking on the ritualnorth
  account's 3000 GH Actions min/mo allowance — pi-gen is now
  effectively free.
- **Docker GHCR build + source-tarball publish** stay on GitHub-
  hosted runners. They're fast (~45 s + ~30 s) so the minutes
  cost is negligible, and keeping them on GitHub means Docker
  releases still ship even if the VPS is down.
- Restored the pi-gen trigger to all `v*` tags (we'd briefly
  restricted to `v<major>.<minor>.0` only as a minute-saver —
  no longer needed).

### Appliance code unchanged from v0.0.25.

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
