# Remote access via wattpost.cloud

By default WattPost is **LAN-only**. To reach the dashboard from your
phone on the road, pair the appliance with **wattpost.cloud**, our
managed broker that gives you a stable HTTPS URL with no
port-forwarding, no public IP, no certs to manage.

## How it works

1. The appliance opens an **outbound** Cloudflare Tunnel to our
   infrastructure. Nothing inbound on your network, no holes in your
   router, no public IP needed.
2. The cloud assigns your appliance a stable subdomain like
   `https://<slug>.wattpost.cloud/`. Real HTTPS cert, valid
   everywhere, auto-renewed.
3. Authentication is gated by your wattpost.cloud account. Every
   request is signed with a short-lived HMAC and verified against the
   appliance's `owner_id` before the cloud forwards it to the tunnel.

## Getting the appliance online in the first place

Remote access needs *some* internet connection reaching the appliance —
no tunnel or broker can create connectivity that isn't there. For a van,
cabin, or anywhere off a home network, the recommended setup is a
**cheap SIM/4G router** (many vanlifers already run one for their own
internet anyway):

- **Point the appliance at it like any other WiFi network.** The
  appliance should just be a WiFi *client* on the router — it doesn't
  need to run its own access point at all once there's a real router
  present. Your phone joins the same router; local access (`wattpost.local`)
  and remote access both work over it, with no config needed to switch
  between them.
- **This sidesteps the single-radio hotspot limitations entirely.** The
  appliance's own hotspot mode (see [WiFi hotspot](hotspot.md)) is a
  genuinely good fallback for a box with no router around at all, but a
  single WiFi radio can't run an access point *and* stay connected to
  the internet at the same time — see that doc's *Single-radio caveat*
  and *Auto-handoff* sections for what that trade-off looks like in
  practice. A dedicated router sidesteps the trade-off completely: the
  appliance is never doing double duty on one radio.
- **This is what actually makes "check in while you're out hiking"
  possible.** If the van has no internet source of its own, there's no
  path from your phone back to it regardless of appliance settings —
  a SIM router (or a cellular hotspot you leave switched on) is the
  piece that makes that scenario work at all, not a WattPost setting.

If you don't have or want a router, the appliance's own hotspot mode
(off by default, opt-in) still works — you'll just join `WattPost-Setup`
directly when you're at the van, and remote access while away won't be
available unless something else at that location has internet.

## Pairing

1. Sign in at **[wattpost.cloud](https://wattpost.cloud)**.
2. Open the appliance dashboard (LAN), go to **Settings → Cloud → Pair
   with wattpost.cloud**.
3. Paste the pairing code from the cloud dashboard. The appliance
   provisions its tunnel in the background, usually under 30 seconds.
4. Open the cloud dashboard. Your site appears with its broker URL.

See **[Pair with the cloud](pairing.md)** for the step-by-step with
screenshots.

## What you get

- **HTTPS for free.** Real cert, no "Not Secure" warning, works on
  every browser and the iOS Add-to-Home-Screen PWA.
- **Multi-site dashboard.** One login, all your appliances in one
  view.
- **Kiosk shares.** Generate a read-only `wattpost.cloud/k/<token>`
  URL for a wall-mounted tablet or to send a customer. Scoped to a
  fixed allow-list of read-only endpoints, they can never write
  config or trigger restarts.
- **Heartbeat-stale alerts, off-site backups, REST API.** Cloud
  features layered on top of the broker, see
  [wattpost.cloud/pricing](https://wattpost.cloud/pricing).

## Pricing

Local monitoring is **free forever**. **WattPost Cloud** is £6/mo and
adds remote access, multi-site, push, off-site backups, and the REST
API. See the [pricing page](https://wattpost.cloud/pricing) for the
full comparison.

## If you'd rather skip the cloud

The appliance no longer manages remote-access tooling itself,
that wiring was retired in **v0.1.34** in favour of the cloud
broker. If you want a self-managed alternative:

- **Tailscale.** Install it directly per [tailscale.com/install](https://tailscale.com/install)
  on the appliance host (`curl -fsSL https://tailscale.com/install.sh | sh`,
  then `sudo tailscale up`). The WattPost daemon no longer
  configures or surfaces Tailscale state, but it doesn't conflict
  with it either, once your host is on a tailnet you can reach
  `http://<host>.<tailnet>.ts.net/` from any logged-in device (append `:<port>` if you changed the web port).
- **A VPN / WireGuard tunnel of your own.**
- **A reverse-proxy with your own cert** (Caddy, Traefik, nginx +
  Let's Encrypt). The appliance binds `0.0.0.0:<port>`; point your
  proxy at it.

These paths are out of scope for our support, wattpost.cloud is the
one we maintain end-to-end and the one most customers use.
