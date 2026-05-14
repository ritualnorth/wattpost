# Pair an appliance

Connect a WattPost Pi to your `app.wattpost.io` account so it appears in the multi-site dashboard, posts heartbeats, and gets a `<slug>.wattpost.io` remote-access URL.

## Steps

1. **Sign in** at [app.wattpost.io](https://app.wattpost.io). New users can sign up there too.
2. Click **+ Add appliance** on the dashboard. A short pairing code appears, valid for 10 minutes.
3. On your Pi's dashboard, go to **Settings → Integrations → WattPost cloud** and paste the code into the pairing field.
4. Hit **Pair**. Within a few seconds the appliance:
   - exchanges the code for a long-lived bearer token
   - gets assigned a unique slug like `<slug>.wattpost.io`
   - starts a [Cloudflare tunnel](#how-the-tunnel-works) for the remote-access URL
   - posts its first heartbeat
5. Refresh `app.wattpost.io` — your appliance is there, online.

## What pairing actually does

- The appliance saves the bearer token to `/etc/wattpost/config.yaml` under `cloud:`
- The cloud creates a Cloudflare Tunnel + DNS record for the slug
- The appliance launches a `cloudflared` daemon to maintain that tunnel
- Heartbeats post every ~5 minutes (configurable)

No port-forwarding, no inbound network changes. The tunnel is outbound-only from your network.

## How the tunnel works

Clicking **Open site →** on a card at `app.wattpost.io`:

1. Browser navigates to `https://<slug>.wattpost.io`
2. Cloudflare routes the request through the tunnel
3. `cloudflared` on the appliance proxies it to `localhost:80`
4. The appliance's daemon serves the same dashboard you'd see on the LAN

Auth's seamless: the cloud already authenticated you when you signed in, and the appliance trusts loopback traffic (the request reaches it from `127.0.0.1` via cloudflared) — see [Local web password](/docs/local-password) for the full trust model.

## Unpair

**Settings → Integrations → WattPost cloud → Edit → Unpair**, or use `wattpost-config → Unpair from cloud` from SSH. The appliance immediately stops heartbeats and removes the tunnel; on the cloud side, the site row stays until you delete it from the dashboard (so heartbeat history is preserved if you want to re-pair the same Pi).

## Re-pair to a different account

Unpair first, then run the pair flow with a fresh code from the new account. Each appliance can be paired to one cloud account at a time.

## Troubleshooting

- **"Pair failed: 410 Gone"** — the code expired. Generate a fresh one (codes are good for 10 minutes).
- **Paired but cloud shows Offline** — the heartbeat service initialises on daemon boot. Restart the wattpost service or click **Send heartbeat now** in the appliance UI.
- **No tunnel URL on the cloud card** — the appliance was paired before the cloud's tunnel provisioning was configured. Unpair + re-pair to get a tunnel issued.
