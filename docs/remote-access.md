# Remote access (Tailscale)

By default WattPost is **LAN-only**. To reach the dashboard from
your phone on the road, you have two options:

## Tailscale (DIY, free)

Tailscale builds a private mesh between your devices. You don't open
ports, you don't have a public IP, you don't manage certs.

1. Sign up at [tailscale.com](https://tailscale.com) — free plan
   covers up to 100 devices.
2. **Settings → Network → Connect to my tailnet**. The button runs
   `tailscale up`, surfaces an auth URL inline. Click it on the
   appliance device, sign in.
3. Page flips to "**Connected · wattpost**" with your tailnet IP and
   a `wattpost.<your-tailnet>.ts.net` domain.
4. Install the **Tailscale** app on your phone, sign into the same
   account, and you can reach `http://wattpost.<your-tailnet>.ts.net:8000/`
   from anywhere with internet.

### Enabling HTTPS (no cert warnings)

Tap **Enable HTTPS via Tailscale Serve** once you're connected.
Tailscale provisions a real Let's Encrypt cert for
`*.<your-tailnet>.ts.net` and serves the dashboard at port 443
(`https://wattpost.<your-tailnet>.ts.net/`). Auto-renewed forever, no
config files, no monitoring required.

### Security note on the auth URL

The auth URL is a **one-time, ~10-minute token** that adds the
appliance to whichever Tailscale account first opens it. Treat it
like a password — only open it on your device.

## WattPost cloud

A managed alternative: we open an outbound Cloudflare Tunnel from
your appliance to `wattpost.io` and give you a stable subdomain like
`https://<slug>.wattpost.io/`. No Tailscale account, no auth URL, no
install. Browser-side broker dashboard at
`app.wattpost.io/site/{id}/` opens the appliance UI inside your
cloud session.

Pricing starts at £3/mo (Hobby, 1 site) with a 14-day trial — see
[wattpost.cloud/pricing](https://wattpost.cloud/pricing) for the full
comparison. Includes heartbeat-stale alerts, off-site backups, and
the REST API.

Choose whichever fits. Tailscale is free + self-managed; the cloud
tier is one-click and adds multi-site overview, push notifications,
and remote management.
