# First boot + dashboard

Power on a freshly-flashed WattPost Pi and a lot happens in the first 30 seconds.

## The MOTD

SSH in (`ssh wattpost@wattpost.local`, default password `wattpost`. Change it!) and you'll see:

```
  WattPost v0.1.34    uptime: 1m

  Dashboard:  http://wattpost.local
  Daemon:     ● running
  Cloud:      not paired (pair at https://wattpost.cloud/app/pair)
  Tunnel:     ─

  Type wattpost-config for the setup menu.
```

Pair with **[wattpost.cloud](https://wattpost.cloud/app/pair)** if you
want to reach the dashboard from outside your LAN, see
[Remote access](remote-access.md) for the full pairing walk-through.

Everything you need to find the dashboard. Type `wattpost-config` for the interactive setup menu. Change web port, reset web password, check daemon status, view live logs, pair / unpair from cloud, check for updates.

## Opening the dashboard

`http://wattpost.local` resolves on any LAN with mDNS. If your router or network doesn't support mDNS, look up the Pi's IP from your router admin panel and use that instead (e.g. `http://192.168.1.42`).

The dashboard opens at the **Dashboard** tab. A large state-of-charge donut, real-time power flow, and a Today summary. Empty until you pair gear.

## The four tabs

- **Dashboard**. Live view: SoC, flow, today's energy in / out, weather, forecast
- **History**. Charts of any metric over any range, CSV export
- **Devices**. List of paired hardware, last poll, slave IDs
- **Settings**. Sub-tabs for devices, integrations (Solcast, weather, MQTT, cloud), alerts, system, and **Docs**. These pages, served locally so you can read them without internet

## Kiosk mode

Drop the chrome and run the dashboard fullscreen on a wall-mounted tablet or fridge browser via the **Kiosk** view. See [Kiosk view](/docs/kiosk) for the URL pattern + auto-launch options.

## What runs in the background

A single `wattpost.service` Python daemon. It:

- Polls configured devices every 60 seconds
- Computes a bank aggregate (voltage / current / SoC across all your batteries)
- Stores everything in a local SQLite database
- Evaluates alert rules and fires to local transports
- Sends a heartbeat to the cloud (if paired)
- Polls weather + Solcast forecast (if configured)

Tail it: `journalctl -u wattpost -f` from SSH, or **Settings → Diagnostics → Recent logs** in the dashboard.

## What if Bluetooth doesn't connect?

Most "BT dongle not advertising" warnings clear on the next poll cycle (~60s). Persistent failures usually mean:

- The dongle is on a flaky USB power source. Try a different port or a Pi-official power supply
- The Pi's BlueZ daemon needs reset · `sudo systemctl restart bluetooth` (or use the **Restart wattpost service** menu)
- The device is out of range. BLE drops off sharply past ~10 m through walls

See [Troubleshooting](/docs/troubleshooting) for more.
