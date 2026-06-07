# Security

How the WattPost appliance is secured on your network, and what you can do
to harden it further.

## The "Not Secure" browser warning

You'll see **"Not Secure"** in your browser's URL bar when you open
WattPost via `http://<pi-ip>/` on the LAN. **This is normal and
expected** for a local-only appliance.

## Why it's there

Modern browsers label any page served over plain HTTP (not HTTPS) as
"Not Secure" because the connection isn't encrypted. On the public
internet that warning is a real risk. On your home / cabin / boat
LAN it's a label, not a danger. There's nobody on the wire between
your phone and the Pi.

The same applies to **Pi-hole**, **OctoPrint**, **Plex**, **Home
Assistant** out-of-the-box, every Synology NAS, and almost every
other self-hosted Pi appliance. The fix is the same: HTTPS via a
real cert.

## Removing the warning

Two ways, in order of difficulty:

1. **Pair with wattpost.cloud** (recommended).
   The cloud broker gives you `https://<slug>.wattpost.cloud/` with
   a real Let's Encrypt cert maintained by us. See [Remote
   access](/docs/remote-access) for pairing.

2. **Browser exceptions**.
   If you'd rather keep using `http://<pi-ip>/`, every browser
   lets you dismiss / hide the warning permanently for a known
   address. iOS Safari hides it from the URL bar by default once
   you've visited the site a few times.

## What is NOT a safer option

- **Self-signed certificates**. Browsers will show a much *louder*
  warning than "Not Secure", trains users to dismiss security
  warnings, and breaks the PWA install path. Don't do this.
- **A real cert for an internal IP / `.local` hostname**. Doesn't
  exist. Let's Encrypt and the public CAs only issue for public
  DNS names.

## How the cloud broker authenticates requests

Once you pair, requests to `https://<slug>.wattpost.cloud/` are
proxied through our broker before they hit your appliance's tunnel.
The broker enforces:

- **HMAC-signed request headers.** Every browser request carries a
  short-lived `X-WP-Broker-Auth` header signed against a per-site
  secret. Replay window is tight (seconds, not minutes), and the
  scope tag (`user` vs `kiosk`) is part of what's signed, so an
  attacker who scrapes a kiosk header can't strip the tag and
  promote it to full access.
- **`owner_id` check.** The broker verifies the logged-in cloud
  user owns this appliance before forwarding any request. A leaked
  pairing code or stolen tunnel URL doesn't grant access on its own.
- **Kiosk shares are read-only.** A `wattpost.cloud/k/<token>` URL
  is scoped to a fixed allow-list of GET endpoints, they can never
  POST to `/api/system/restart`, `/api/devices`, or anything that
  writes config. The token exchanges for a short cookie on first
  load and never appears in subsequent URLs (so it doesn't end up
  in your browser history / server logs).

If you'd rather skip the broker entirely, the appliance still binds
`0.0.0.0:<port>` on your LAN, put it behind your own VPN / reverse
proxy. See [Remote access](/docs/remote-access) for the unsupported
options.

## Host firewall & SSH (Pi appliance)

The SD-card (Pi) appliance ships with two host-level hardening switches,
both controlled from **Settings** (or `web.firewall_enabled` /
`web.ssh_enabled` in the config file). They're **Pi-image only** — Docker
installs skip them, because there Docker and the host already own the
firewall.

### Inbound firewall — on by default

An nftables firewall guards the appliance with a **default-deny** policy on
incoming connections. Only what the appliance actually needs is allowed in:

- the dashboard (port 80),
- mDNS, so `wattpost.local` keeps resolving,
- DHCP, plus the hotspot's DHCP/DNS (harmless when the hotspot is off),
- SSH (port 22) **only while SSH is enabled** (see below).

Everything else arriving from the network is dropped. Outbound traffic is
left open — that's how the appliance reaches the cloud, MQTT, ntfy and your
other integrations. The toggle is also the master escape hatch: if a rule
ever misbehaves, turn the firewall off in Settings and the appliance is
back to wide-open on the LAN.

### SSH — off by default

The Pi image ships with **sshd disabled and no built-in login account** —
there's no default password to guess. Turning **SSH on** in Settings both
starts sshd and opens port 22 in the firewall; turning it off stops sshd
and recloses the port. You still need your own user + SSH key (set in
Raspberry Pi Imager when you flash the card) to actually log in.

> **Heads-up if you install or update over SSH:** because SSH is off by
> default, the appliance closes port 22 the next time the service starts,
> which drops your session. If you rely on SSH, enable it first — set
> `web.ssh_enabled: true` before installing, or use the Settings toggle
> once you're in the dashboard. Locked out? Re-enable SSH from the
> dashboard, or attach a keyboard + screen to the Pi.

### How it stays safe

The dashboard never touches the firewall or sshd directly. It calls a
small, **root-owned helper that understands only two fixed commands**
(`status`, and `apply <firewall on/off> <ssh on/off>`) through a
locked-down sudo rule. The helper owns the ruleset; the app can only flip
the predefined switches — so even a compromised dashboard can't author its
own firewall rules or run arbitrary commands as root.

> **Locked yourself out?** If a firewall rule ever blocks the dashboard,
> get to the Pi's console (keyboard + screen, or SSH if it's on) and run
> `sudo wattpost-config --firewall-off` — that disables the firewall and
> restarts the daemon so the LAN can reach it again.

## What's actually exposed on the LAN

On your network the appliance listens on **one port (80)** for the
dashboard and local API, plus mDNS so `wattpost.local` resolves. With login
required by default, another device on the LAN can't read your data or
change settings without the password (or a scoped, revocable kiosk token).
SSH is closed unless you turn it on. Everything else inbound is dropped by
the firewall.

The one thing that doesn't change: **LAN traffic is plain HTTP**. On a
network you don't fully trust, reach the appliance over the **cloud tunnel**
(HTTPS) rather than its LAN IP, so your login isn't sent in the clear.

## The real lateral-movement control: network segmentation

The host firewall guards the *appliance's* own front door. It does **not**:

- stop a compromised appliance from reaching the rest of your network, or
- protect your other devices from each other.

For that you want **network segmentation** — the single most effective
thing you can do for any IoT device, WattPost included. Put the appliance
(and your cameras, smart plugs, TVs and other IoT) on a **separate network
from your trusted machines** (laptops, phones, NAS, work devices):

- **Easy:** most home routers have a **"Guest" Wi-Fi network** — switch it
  on and join the appliance to it. Guest networks are isolated from the
  main LAN by default.
- **Advanced:** a dedicated **IoT VLAN**, with rules that let the segment
  reach the internet but **block it from initiating connections to your
  trusted LAN**.

Either way, if any device on that segment is ever compromised, it can't
pivot to your laptop or NAS. A host firewall can't substitute for this —
they solve different problems, and segmentation is the one that *contains*
a breach. (Trade-off: with the appliance on an isolated segment, browse it
via the cloud tunnel, or hop onto the same segment when you want local
access — cross-segment LAN browsing is what the isolation deliberately
blocks.)
