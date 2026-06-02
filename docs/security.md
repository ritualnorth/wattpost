# About the "Not Secure" warning

You'll see **"Not Secure"** in your browser's URL bar when you open
WattPost via `http://<pi-ip>:8000/` on the LAN. **This is normal and
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
   The cloud broker gives you `https://yourname.wattpost.cloud/` with
   a real Let's Encrypt cert maintained by us. See [Remote
   access](/docs/remote-access) for pairing.

2. **Browser exceptions**.
   If you'd rather keep using `http://<pi-ip>:8000`, every browser
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

Once you pair, requests to `https://yourname.wattpost.cloud/` are
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
