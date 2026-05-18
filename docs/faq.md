# FAQ

## Does WattPost work without internet?

Yes. The appliance is local-first. Bluetooth polling, dashboard, history, local alerts (ntfy/Discord/Pushover via your LAN, MQTT to a local broker, email via your relay) — all work without an internet connection.

The cloud tier is strictly additive: lose internet and you lose multi-site / heartbeat alerts / remote tunnel, but the local appliance keeps running.

## Do I have to subscribe to anything?

No. The SD image and local appliance are free forever. The [Cloud tier](/docs/cloud-overview) is optional, aimed at people running multiple sites or who want SMS escalation / remote access. You'll never be required to buy us.

## What happens if my Pi dies?

Flash a fresh SD image, restore your `/etc/wattpost/config.yaml` and `/var/lib/wattpost/solar-monitor.db` backups, plug in. The cloud pairing is preserved (it's tied to the bearer token in config.yaml). Total recovery: 10 minutes.

We recommend backing up those two files periodically; the dashboard's Settings → Diagnostics shows their paths.

## Can I add hardware that's not on the supported list?

Email [support@wattpost.io](mailto:support@wattpost.io) with the gear name + protocol details. If the BLE / Modbus / VE.Direct protocol is publicly documented (or we can borrow a sniffer), we can usually add support in a release or two. Vendor-locked devices that require their cloud auth — those we won't add.

## How do I get updates?

Depends on your install path. SD-card install: **Settings → About → Update now** pulls the latest source tarball, SHA256-verifies, swaps into place, restarts. Docker install: `docker compose pull && docker compose up -d` from the host. See [Updates](/docs/updates).

## Can I run WattPost in Docker instead of on a Pi?

Yes. On a Linux host with BLE (laptop, mini PC, Synology, Unraid, etc), `docker compose up -d` is the install — full instructions at [Run in Docker](/docs/docker-install). Same daemon, same dashboard, same scanner; only differences are how you install and how you take updates. macOS / Windows / WSL2 can't pass host BLE through Docker so they're SD-card only.

## Is the code open source?

**Source-available**, not full open source. The SD image ships with the full appliance source under `/opt/wattpost-src`, so you can audit + modify what runs on your Pi. We retain commercial rights via the license so we can build a sustainable business around WattPost.

The cloud-tier code (multi-site dashboard, heartbeat ingest, tunnel provisioning) is private.

## Is the cloud data private?

The cloud receives **heartbeats only** — SoC + net power + a tiny extras blob (alert count, today's energy), every ~5 minutes. Per-poll detail, individual cell voltages, full history — all stay on your Pi. We never see your raw telemetry stream.

Unpairing from `wattpost.cloud` deletes the appliance row + all stored heartbeats from our DB.

## Why local-first instead of cloud-only?

Off-grid solar exists *because* people don't trust the grid + commercial systems. A monitor that breaks when the internet goes down isn't acceptable in that world. WattPost stays useful in a blackout, in the woods, on a boat — anywhere your inverter is.

The cloud tier is for the things only a remote watcher can do: tell you the appliance itself is dead, give you a remote-access URL, escalate via SMS.

## What's the difference between WattPost and \<some vendor app\>?

Vendor apps (Renogy BT, VictronConnect, JK BMS app) are read-only, vendor-specific, often need their cloud + your account. They're great for "tap once, see numbers". WattPost is for "I have batteries + an MPPT + a shunt + maybe a different vendor's BMS, and I want one unified always-on dashboard with alerts and history".

The free local-only WattPost replaces ~3 vendor apps. The cloud tier replaces a hosted commercial dashboard like Victron VRM.

## Can I use WattPost for grid-tied PV?

Yes — many "off-grid" pieces of kit (Renogy Rover, Victron MPPT) are equally happy on grid-tied or hybrid setups. Hybrid inverters (EG4, Sol-Ark) are on the supported-hardware roadmap.

If you're 100% grid-tied with no batteries, dedicated solar-PV monitoring tools (PVOutput, Solar Analytics) cover that use case better. WattPost shines when there's a battery and load to track.

## How much does the cloud tier cost?

- **Hobby** — £3/mo, 1 site, multi-site dashboard + tunnel + REST API
- **Pro** — £6/mo, 3 sites, push notifications, rules engine, cloud-stored backups
- **Installer** — £39/mo + £3/site, white-label kiosk for paid installs

14-day free trial. Cancel anytime. Full breakdown at [wattpost.cloud/pricing](https://wattpost.cloud/pricing).

## What hardware do you recommend buying?

- **Raspberry Pi 4** (1 GB is enough — about £60 / $80 with PSU)
- **8 GB+ microSD card**
- A Bluetooth source for each vendor you own:
  - Renogy → **BT-2 dongle** (~£8) plugs into the comms port
  - Victron → no dongle, devices advertise directly (you'll need the Instant Readout encryption key from VictronConnect)
  - JK BMS → no dongle, it advertises directly

Total kit for one vendor: **~£70**. About 1/4 the cost of a comparable commercial monitor.
