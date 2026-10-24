# Pair an appliance

Connect a WattPost Pi to your `wattpost.cloud` account so it appears in the multi-site dashboard, posts heartbeats, and pushes telemetry for the cloud dashboard.

## Steps

1. **Sign in** at [wattpost.cloud](https://wattpost.cloud). New users can sign up there too.
2. Click **+ Add appliance** on the dashboard. A short pairing code appears, valid for 10 minutes.
3. On your Pi's dashboard, go to **Settings → Integrations → WattPost cloud** and paste the code into the pairing field.
4. Hit **Pair**. Within a few seconds the appliance:
   - exchanges the code for a long-lived bearer token
   - posts its first heartbeat
   - starts pushing 1-minute telemetry rollups (a recent backfill first)
5. Refresh `wattpost.cloud`. Your appliance is there, online.

## What pairing actually does

- The appliance saves the bearer token to `/etc/wattpost/config.yaml` under `cloud:`
- Heartbeats post every ~5 minutes (configurable)
- Closed 1-minute telemetry rollups and a few summary views are pushed to your account every few minutes. The cloud dashboard is rendered from this data.
- You can stop the telemetry upload in **Settings → Integrations → WattPost cloud** (the heartbeat continues), or unpair to stop everything.

Everything is **outbound HTTPS from the appliance**. No port-forwarding, no inbound network changes, and the appliance never accepts inbound connections from the cloud. The cloud view is read-only; settings and device control stay on the appliance.

## Unpair

**Settings → Integrations → WattPost cloud → Edit → Unpair**, or use `wattpost-config → Unpair from cloud` from SSH. The appliance immediately stops heartbeats and telemetry; on the cloud side, the site row stays until you delete it from the dashboard (so heartbeat history is preserved if you want to re-pair the same Pi).

## Re-pair to a different account

Unpair first, then run the pair flow with a fresh code from the new account. Each appliance can be paired to one cloud account at a time.

## Troubleshooting

- **"Pair failed: 410 Gone"**. The code expired. Generate a fresh one (codes are good for 10 minutes).
- **Paired but cloud shows Offline**. The heartbeat service initialises on daemon boot. Restart the wattpost service or click **Send heartbeat now** in the appliance UI.
- **Paired but the cloud dashboard has no history**. Telemetry is pushed every few minutes after pairing; check that `telemetry_sync` is not turned off in Settings → Integrations → WattPost cloud.
