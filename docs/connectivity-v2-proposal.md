# Connectivity v2 — proposal

Status: **draft / not implemented.** This captures a redesign discussion,
not the current shipping architecture (see [architecture.md](architecture.md)
and [remote-access.md](remote-access.md) for what's live today). Written up
so the reasoning survives past the conversation that produced it.

## Why revisit this

The current connectivity story grew in layers: a home-grown remote-access
system, retired in v0.1.34 for a Cloudflare Tunnel + broker, with an SSO-token
front door left armed but unused alongside the broker that replaced it (see
the tunnel section of the last architecture review). Identity has picked up
six-plus coexisting mechanisms across that same evolution. None of it is
wrong so much as it's three generations of decisions still all present at
once. This proposal is what the design looks like with only one generation.

## The one rule everything else follows

**The app never asks the user to pick "local" or "remote."** One dashboard,
one connect flow. The app tries the LAN first (fast timeout) and falls back
to the cloud automatically; a small status indicator ("Live" vs "Synced 4m
ago") tells a technical user which channel it landed on, but nothing about
the UI changes based on it. Every decision below is in service of this.

## Local access

- **WiFi only. Never BLE for pairing/discovery.** The onboard BLE radio is
  already committed to reading battery/BMS hardware (some via held-open GATT
  connections — JBD, Daly, JKBMS — some via passive broadcast scanning —
  Victron, Ruuvi, Mopeka, Govee). Layering a peripheral/advertising role on
  the same radio for phone pairing contends with that traffic and is a
  plausible cause of BLE flakiness that's already a support burden today.
  mDNS + HTTP over WiFi has no such conflict and is already the mechanism
  used for first-boot onboarding (`WattPost-Setup` AP + captive portal) — so
  this isn't a new direction, just dropping a second, conflicting one that
  was never finished (`connection.ts`'s BLE rung throws `not-implemented`).
- **The Pi should be a plain WiFi client whenever any other network is
  available** (home WiFi, van park WiFi, and especially a dedicated van
  router — see below). Self-hosted AP mode is the first-boot / no-router-at-
  all fallback, not the primary design center. This sidesteps the
  single-radio AP+uplink contention (`hotspot/handoff.py`'s 30s poll /
  debounce / periodic-blip dance) entirely for anyone with a router, because
  the Pi never needs to run the AP and an uplink at once.
- **Local reachability must not depend on the network having internet.**
  mDNS/HTTP to a local IP needs no internet — but iOS and Android both run
  connectivity checks and will silently deprioritize or route around a WiFi
  network they've classified as internet-less, which produces exactly the
  "connected to the van's WiFi but the app can't reach the Pi" symptom.
  Fix: bind local probe/API traffic explicitly to the WiFi interface
  (`NWParameters.requiredInterfaceType = .wifi` on iOS,
  `ConnectivityManager.bindProcessToNetwork` on Android), overriding the
  OS's internet-availability judgment for that traffic. Standard practice
  for any local-IoT-control app; not exotic.

## Device ↔ cloud: add MQTT + device shadow as a fallback under the tunnel

Correction to an earlier draft of this doc: Home Assistant Cloud (Nabu
Casa) is **not** an example of this pattern — verified against Nabu Casa's
own docs, it's a live SNI-routed TCP tunnel (SniTun) straight to the local
instance, the same category as WattPost's current Cloudflare Tunnel, not a
synced-metrics cloud. That's actually *why* HA's cloud UI is trivially
identical to its local UI: one server, proxied, not two rendering paths
kept in sync. Tesla/Enphase/SolarEdge are the real examples of the
MQTT/shadow pattern below (also the standard shape behind AWS IoT Core and
Azure IoT Hub) — a device with an intermittent, sometimes-bad uplink that
needs to report state and receive occasional commands without a live
session:

- Device holds a persistent MQTT connection (reconnect-with-backoff is
  built into the protocol, not something to hand-roll). Publishes small
  telemetry messages on its own schedule; no polling required either
  direction.
- **Device shadow**: cloud holds "desired state," device holds "reported
  state," they reconcile whenever connected. This *is* the "push a setting
  change down from the cloud" mechanism asked for repeatedly in the original
  discussion — and WattPost already has this working end-to-end for one
  settings category: alert rules. `set_local_rule`/`delete_local_rule`
  commands (#261 slice 2, `solar_monitor/cloud/service.py`
  `_dispatch_command`) carry a rule spec in `payload_json`, are signed the
  same way as update/backup/rollback (`command_verify.py`), and are applied
  on the next heartbeat. This is the concrete template for "extend that to
  general settings" — proven infrastructure to reuse per settings category,
  not something to design from scratch.
- **Broker's "last will"** marks the device offline the instant its
  connection drops, so the app can honestly show "last synced Nm ago"
  instead of silently going stale.
  **Partially shipped without MQTT**: `/api/sites` already computes
  `online`/`age_seconds` from plain HTTP heartbeats (no persistent
  connection needed for this part), and the app now surfaces it — a
  "Live" / "Synced Nm ago" / "Never synced" badge per cloud-reachable
  site, refreshed on every Sites view-enter, not just at sign-in (see
  `wattpost-app/src/lib/cloud.ts`, `store.ts`, `pages/Sites.tsx`). MQTT
  would still be the better transport for this at fleet scale, but the
  "show freshness honestly" UX doesn't need to wait on that migration.
- **Degrades honestly on bad links.** A held-open interactive session (the
  current tunnel) breaks visibly mid-use on a flaky connection. MQTT
  reconnect + shadow degrades to *staler data and delayed commands* instead
  — representable in the UI as a "pending" state rather than a dropped
  session. This is the strongest argument for the change: it isn't just
  simpler, it fails better.
- Settings changes become **eventually consistent**: UI shows "Pending —
  will apply next check-in," flips to confirmed once the device's next
  publish echoes the change back. This is the one genuinely new UI pattern
  the redesign needs.

## What happens to the tunnel

Revised from an earlier draft: **the tunnel stays the primary path**, not a
demoted escape hatch. The HA comparison above is the reason why — a single
live server proxied through is what makes "cloud UI == local UI" trivially
true, which is a real, explicitly-stated product goal here, not something
to trade away for resilience alone. What actually needs fixing isn't the
tunnel concept, it's that WattPost has *two generations* of tunnel
front-door coexisting (see cleanup items below) where HA has one, clean,
audited mechanism.

The MQTT/shadow layer's job shrinks accordingly: it's the **fallback under
the tunnel**, not its replacement. When the tunnel can be held open, it's
the whole experience (live, identical locally and remotely). When the link
is too bad to hold a session (the van/cabin case this project actually
targets, more than HA's typical home-broadband user), the app falls back to
"last synced Nm ago" from the shadow channel instead of just breaking. Primary
experience from the tunnel, safety net from the shadow — not either/or.

## Resilience: SIM/cellular router as the recommended answer for remote access

If a phone is joined to the same network as the Pi, it should always reach
it — but "away from the van, want to check in" fundamentally requires *some*
internet source in the van; no WiFi-radio cleverness creates connectivity
that isn't there. Recommending a SIM/4G router (common van-life gear already,
not a purchase solely for this app) as the answer for real remote access:

- Removes the single-radio AP/uplink conflict (Pi is just a client on it).
- Removes the "OS avoids no-internet networks" failure mode (a router with
  real internet looks like a normal trusted network to the phone).
- Collapses "near the van" and "hiking away" into one consistent network
  identity instead of two different code paths.
- Self-hosted AP mode remains supported for the no-router-at-all case, but
  explicitly as the fallback tier, documented as such.

## Phone-as-relay for the fully isolated Pi

For the Pi with no router and no independent uplink at all: the phone can
opportunistically carry data in both directions without needing any special
trust, by piggybacking on signing the device already does:

- The Pi signs its heartbeat payload locally regardless of connectivity
  (signing needs no internet, only *sending* does). If it detects no
  route to the cloud, it holds the already-signed blob. A phone on LAN
  fetches it (`GET /api/pending-sync` or similar) and forwards the opaque
  envelope to the cloud whenever *it* next has any internet. The cloud
  verifies the signature exactly as if the Pi sent it directly — the phone
  never handles the device's key.
- Same idea in reverse for queued desired-state commands: phone fetches
  them from the cloud when it has signal, delivers them to the Pi over LAN
  next time they're together.
- Honest scope: this catches things up on next visit, it isn't continuous
  remote monitoring. It's a resilience layer under the SIM-router
  recommendation, not a replacement for it.

## Mobile app: native shell, not a webview wrapper

- **React Native or Flutter**, not Capacitor. The "flash SD card, open app,
  it just finds your box" pitch needs real native mDNS + BLE (BLE for
  battery-pairing *help* flows in-app, not device pairing — see above),
  which a webview can't do without fighting the framework.
- Local device dashboard can stay a served web UI *inside* the app once a
  connection is established (no native rewrite needed there) — the app
  shell needs to be native specifically for discovery and for feeling like
  one consistent product whether the data's live or synced.
- Local and cloud dashboards should render from the *same* UI rather than
  the cloud having a separate, simpler fleet-view (`wpc.js`) distinct from
  the device's own SPA (`app.js`) — most of this falls out for free once
  both paths feed the same shadow-backed view model instead of one being
  "the real dashboard" and the other "the cloud's copy of it."

## Relationship to existing cleanup items

Independent of whether this whole proposal is adopted, these apply either
way. Status:

- **Done.** Removed the superseded `.io` + SSO-redirect-token tunnel front
  door: `api/sites.py`'s `mint_sso_token` + its route, the appliance's
  `/sso` route (`api/app.py`), `consume_sso_token` + the SSO nonce cache +
  the `origin=sso` session concept (`web_auth.py`). `sso_secret` and
  `verify_broker_auth`/`broker_auth_scope` were left untouched — that's the
  live broker HMAC mechanism, just confusingly named after the flow that
  used to share it. Renaming `sso_secret` (e.g. to `broker_secret`) is a
  separate migration: it's read from already-paired appliances' persisted
  `config.yaml` and pushed every heartbeat, so it needs a back-compat read
  of the old key, not just a find-replace.
- **Done.** Removed `wattpost-app/src/lib/connection.ts`'s unused
  `connect()` ladder, `ApplianceConnection`, and the now-dead
  `Appliance.password` field + `setAppliancePassword()` (only ever written
  by the removed native LAN-login path, never read elsewhere — the webview
  handles its own login). Kept `resolveBaseUrl()`/`probeLan()`, which are
  live.
- **Done.** Fixed `pairing.md` and `cloud-architecture.md`, which described
  the pre-broker direct-to-`.io` flow as current; both now describe the
  Caddy/broker/`X-WP-Broker-Auth` path that's actually live, and are
  explicit that `.io` is an internal tunnel hostname, never a user-facing
  URL.
- **Not done — needs a small backend feature, not a cleanup.** The "same
  box shows twice" (LAN entry + cloud entry) issue in `store.ts` can't be
  fixed client-side: `/api/health` doesn't expose any pairing identity
  today, so there's no reliable way to correlate a hand-added LAN entry
  with a cloud-synced one. Matching by label would be fragile and could
  wrongly merge two different appliances that happen to share a name.
  Real fix: have `/api/health` (or a similar local endpoint) report the
  appliance's cloud slug when paired, then merge client-side on that.

## What this proposal doesn't decide

- Whether to build the MQTT/shadow layer as self-hosted (EMQX, Mosquitto)
  or on a managed IoT platform (AWS IoT Core) — infra choice, not
  architecture.
- Exact scope of what settings move into the desired-state schema first.
- Whether the tunnel is removed outright or kept as a narrow, explicitly-
  invoked feature.
- Timeline — this is sized in months, not a weekend refactor, and should be
  sequenced deliberately rather than attempted alongside normal feature
  work.
