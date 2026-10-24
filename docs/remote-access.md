# Remote access via wattpost.cloud

By default WattPost is **LAN-only**. To check on your system from your
phone on the road, pair the appliance with **wattpost.cloud**. The
cloud shows you a **copy of the data your appliance pushes to it**,
over a normal HTTPS connection, with no port-forwarding, no public IP
and no certs to manage on your side.

## How it works

1. The appliance makes **outbound** HTTPS requests to wattpost.cloud.
   It sends a heartbeat and 1-minute telemetry rollups (battery state
   of charge, power flows, temperatures, daily energy totals) plus a
   few summary views.
2. wattpost.cloud stores that data against your account and renders the
   same dashboard from it at `https://wattpost.cloud/`. Real HTTPS cert,
   valid everywhere, maintained by us.
3. Access is gated by your wattpost.cloud account.

**The appliance never accepts inbound connections from the cloud.**
There is no tunnel, no reverse proxy and no remote login into the box.
That also means the cloud view is **read-only and a little delayed**:
it shows what the appliance last uploaded, and settings, setup and
device control stay on the appliance's own (local) dashboard.

## Pairing

1. Sign in at **[wattpost.cloud](https://wattpost.cloud)**.
2. Open the appliance dashboard (LAN), go to **Settings → Integrations
   → WattPost cloud → Pair**.
3. Paste the pairing code from the cloud dashboard.
4. Within a minute or so your site appears on the cloud dashboard.

See **[Pair with the cloud](pairing.md)** for the step-by-step.

## What you get

- **HTTPS for free.** Real cert, no "Not Secure" warning, works on
  every browser and the iOS Add-to-Home-Screen PWA.
- **Multi-site dashboard.** One login, all your appliances in one
  view.
- **Kiosk shares.** Share a read-only view with a wall-mounted tablet
  or a customer from the cloud dashboard. These are rendered from the
  pushed data, so they can never write config or trigger restarts.
- **Heartbeat-stale alerts, off-site backups, REST API.** Cloud
  features layered on top of the pushed data, see
  [wattpost.cloud/pricing](https://wattpost.cloud/pricing).

## Turning it off

You stay in control of what is sent:

- **Settings → Integrations → WattPost cloud**: switch telemetry
  sync off to keep the heartbeat but stop uploading rollups, or
  **Unpair** to stop sending anything.
- Never pair at all and nothing is ever sent. The appliance is fully
  functional with no account and no internet.

See [Privacy & telemetry](privacy-and-telemetry.md) for exactly what is
sent.

## Pricing

Local monitoring is **free forever**. **WattPost Cloud** is £6/mo and
adds the cloud dashboard, multi-site, push, off-site backups, and the
REST API. See the [pricing page](https://wattpost.cloud/pricing) for the
full comparison.

## If you'd rather skip the cloud

If you want live access to the full (read-write) appliance dashboard
from outside your home, use a self-managed route:

- **Tailscale.** Install it directly per [tailscale.com/install](https://tailscale.com/install)
  on the appliance host (`curl -fsSL https://tailscale.com/install.sh | sh`,
  then `sudo tailscale up`). The WattPost daemon doesn't configure or
  surface Tailscale state, but it doesn't conflict with it either, once
  your host is on a tailnet you can reach `http://<host>.<tailnet>.ts.net/`
  from any logged-in device (append `:<port>` if you changed the web port).
- **A VPN / WireGuard tunnel of your own.**
- **A reverse-proxy with your own cert** (Caddy, Traefik, nginx +
  Let's Encrypt). The appliance binds `0.0.0.0:<port>`; point your
  proxy at it. Requests relayed by a proxy on the same host are never
  treated as local, so they still need the dashboard password.

These paths are out of scope for our support.
