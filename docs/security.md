# About the "Not Secure" warning

You'll see **"Not Secure"** in your browser's URL bar when you open
WattPost via `http://<pi-ip>:8000/` on the LAN. **This is normal and
expected** for a local-only appliance.

## Why it's there

Modern browsers label any page served over plain HTTP (not HTTPS) as
"Not Secure" because the connection isn't encrypted. On the public
internet that warning is a real risk. On your home / cabin / boat
LAN it's a label, not a danger — there's nobody on the wire between
your phone and the Pi.

The same applies to **Pi-hole**, **OctoPrint**, **Plex**, **Home
Assistant** out-of-the-box, every Synology NAS, and almost every
other self-hosted Pi appliance. The fix is the same: HTTPS via a
real cert.

## Removing the warning

Three ways, in order of difficulty:

1. **Tailscale Serve** (recommended).
   Once your tailnet is up, Settings → Network → **Enable HTTPS via
   Tailscale Serve**. Tailscale auto-provisions a Let's Encrypt cert
   for `*.<your-tailnet>.ts.net`. From there, open
   `https://wattpost.<your-tailnet>.ts.net/` — green padlock, no
   warning, no install. Works both at home and remotely.

2. **WattPost cloud**.
   Managed remote access at `https://<slug>.wattpost.io/` via
   Cloudflare Tunnel. Real cert maintained by us. From £3/mo —
   see [Remote access](/docs/remote-access).

3. **Browser exceptions**.
   If you'd rather keep using `http://<pi-ip>:8000`, every browser
   lets you dismiss / hide the warning permanently for a known
   address. iOS Safari hides it from the URL bar by default once
   you've visited the site a few times.

## What is NOT a safer option

- **Self-signed certificates**. Browsers will show a much *louder*
  warning than "Not Secure", trains users to dismiss security
  warnings, and breaks the PWA install path. Don't do this.
- **A real cert for an internal IP / `.local` hostname**. Doesn't
  exist — Let's Encrypt and the public CAs only issue for public
  DNS names.
