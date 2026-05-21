# Remote access via wattpost.cloud

By default WattPost is **LAN-only**. To reach the dashboard from your
phone on the road, pair the appliance with **wattpost.cloud** — our
managed broker that gives you a stable HTTPS URL with no
port-forwarding, no public IP, no certs to manage.

## How it works

1. The appliance opens an **outbound** Cloudflare Tunnel to our
   infrastructure. Nothing inbound on your network — no holes in your
   router, no public IP needed.
2. The cloud assigns your appliance a stable subdomain like
   `https://yourname.wattpost.cloud/`. Real HTTPS cert, valid
   everywhere, auto-renewed.
3. Authentication is gated by your wattpost.cloud account. Every
   request is signed with a short-lived HMAC and verified against the
   appliance's `owner_id` before the cloud forwards it to the tunnel.

## Pairing

1. Sign in at **[wattpost.cloud](https://wattpost.cloud)** (free Hobby
   tier covers one site).
2. Open the appliance dashboard (LAN), go to **Settings → Cloud → Pair
   with wattpost.cloud**.
3. Paste the pairing code from the cloud dashboard. The appliance
   provisions its tunnel in the background — usually under 30 seconds.
4. Open the cloud dashboard. Your site appears with its broker URL.

See **[Pair with the cloud](pairing.md)** for the step-by-step with
screenshots.

## What you get

- **HTTPS for free.** Real cert, no "Not Secure" warning, works on
  every browser and the iOS Add-to-Home-Screen PWA.
- **Multi-site dashboard.** One login, all your appliances in one
  view. Free tier covers one site; Pro / Fleet add more.
- **Kiosk shares.** Generate a read-only `wattpost.cloud/k/<token>`
  URL for a wall-mounted tablet or to send a customer. Scoped to a
  fixed allow-list of read-only endpoints — they can never write
  config or trigger restarts.
- **Heartbeat-stale alerts, off-site backups, REST API.** Cloud
  features layered on top of the broker — see
  [wattpost.cloud/pricing](https://wattpost.cloud/pricing).

## Pricing

Hobby (1 site) is **free forever**. Pro starts at £3/mo. See the
pricing page for the full comparison — includes alerts, off-site
backups, and the REST API.

## If you'd rather skip the cloud

The appliance no longer manages remote-access tooling itself —
that wiring was retired in **v0.1.34** in favour of the cloud
broker. If you want a self-managed alternative:

- **Tailscale.** Install it directly per [tailscale.com/install](https://tailscale.com/install)
  on the appliance host (`curl -fsSL https://tailscale.com/install.sh | sh`,
  then `sudo tailscale up`). The WattPost daemon no longer
  configures or surfaces Tailscale state, but it doesn't conflict
  with it either — once your host is on a tailnet you can reach
  `http://<host>.<tailnet>.ts.net:8000/` from any logged-in device.
- **A VPN / WireGuard tunnel of your own.**
- **A reverse-proxy with your own cert** (Caddy, Traefik, nginx +
  Let's Encrypt). The appliance binds `0.0.0.0:<port>`; point your
  proxy at it.

These paths are out of scope for our support — wattpost.cloud is the
one we maintain end-to-end and the one most customers use.
