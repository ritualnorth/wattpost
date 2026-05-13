# Getting started

WattPost is a small Raspberry-Pi-style appliance that polls your solar
+ battery hardware over Bluetooth and shows the state of your system
at a glance.

## What's running

The daemon (`wattpost.service`) wakes up every 60 seconds, talks to
each configured device, and stores a sample in a local SQLite
database. The dashboard you're reading right now is served by the
same daemon — open `http://<your-pi>:8000/` from any device on the
same network.

## What's on the dashboard

- **Donut**: state of charge of the whole battery bank, with a
  rotating pulse when the bank is charging or discharging.
- **Power flow**: shows which sources are putting energy in, the
  bank's net flow, and the loads pulling energy out. Connectors
  glow when there's active flow.
- **Today**: cumulative energy in / out / charged for today.
- **Cell balance**: per-cell voltages for each smart battery, with a
  24-hour drift sparkline.
- **History tab**: charts of any metric over 1h / 6h / 24h / 7d / 30d
  or a custom range. CSV export available.
- **Devices tab**: drill-down per device.
- **Settings tab**: alerts, transports, network (Tailscale), about,
  diagnostics, daemon restart.

## What to do first

1. **Add your devices**: Settings → Devices & setup → Run device
   setup. Scan an open BLE transport (the BT-2 dongle) for new slave
   IDs and add them in one click.
2. **Set up alerts**: Settings → Alerts → Add rule. Add a "Battery
   low" rule firing at SoC < 30 % to your phone via ntfy, Discord,
   or email.
3. **Decide on remote access**: Settings → Network. Either skip
   (LAN-only is fine), or join your Tailscale account for free
   secure remote access.
