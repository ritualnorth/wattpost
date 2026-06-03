# Troubleshooting

Common things that go wrong, in roughly the order people hit them.

## "Can't open http://wattpost.local"

mDNS isn't reaching the Pi. Try:

1. `ping wattpost.local` from your computer. If no response, mDNS is broken on your network. Common causes: enterprise wifi, "guest network" isolation, an old router with mDNS off.
2. Look up the Pi's IP in your router's DHCP leases list. Open `http://<that-ip>` instead. Bookmark it.
3. As a last resort, plug the Pi into a monitor + keyboard and run `ip addr` to read the IP.

## "Bluetooth device not advertising within 20s"

Most of these clear on the next poll. Persistent failures mean:

- **Power / range**. BLE drops sharply past 5 m through walls. Try moving the dongle closer.
- **BlueZ holds the channel**. If you restarted the daemon less than 60s ago, BlueZ might still own the connection to the dongle. Wait another minute.
- **Pi's USB power**. Undervoltage messes with BLE. Use the official Pi power supply, not a phone charger.
- **Conflict with another app**. Your phone's Renogy app keeps the BT dongle's session. Quit the app on your phone.

## "Daemon won't start"

`sudo systemctl status wattpost` shows the most recent error.

Common ones:

- **"config.yaml not found"** · `/etc/wattpost/config.yaml` is missing or unreadable. Reinstall via `bash /opt/wattpost-src/packaging/install.sh`.
- **"port 80 already in use"**. Something else is on the port. Edit `/etc/wattpost/port.env` to set a different port, or `systemctl stop` the conflicting service.
- **"address already in use" inside the dashboard**. If you killed the daemon roughly, the previous process might still hold the port for ~60s. `sudo systemctl restart wattpost` after waiting.

## "Cloud says Offline but the appliance is fine"

The cloud's heartbeat watcher flips to Offline after 15 minutes of no heartbeat. Most common cause: the daemon was restarted but the **cloud heartbeat service didn't auto-start** because the pairing was set up on a daemon that's no longer running. Restart wattpost (`systemctl restart wattpost` or **Settings → System → Restart daemon**). The pair flow now hot-starts the cloud service in-process, so newer pairings don't have this issue.

## "Update Now does nothing / crashes"

Tail `/var/log/wattpost-update.log` on the Pi (`sudo tail -f`). Common causes:

- **No internet**. The helper has to reach GitHub. Verify with `curl -sIL https://github.com/ritualnorth/wattpost/releases/latest/download/wattpost-source.tar.gz`.
- **SHA256 mismatch**. The tarball was truncated mid-download. Run Update Now again; it's idempotent.
- **install.sh fails on apt**. Apt is busy (unattended-upgrades?) or out of disk. Free up space, wait for apt locks to clear, retry.

## "I forgot the local web password"

SSH in (the OS-level user · `wattpost@wattpost.local`, password `wattpost` by default unless you changed that too) and run `wattpost-config` → **Set / reset web password**. Generates a new random password and prints it.

Lost SSH too? Re-flash the SD image, copy your `config.yaml` and `solar-monitor.db` back. The cloud pairing is preserved.

## "Tunnel URL is dead / 404"

The Cloudflare tunnel needs the appliance to be online and `cloudflared` running. Check with:

```
sudo systemctl status wattpost
ps aux | grep cloudflared
```

If cloudflared is missing entirely, run install.sh again. It apt-installs the package. If it's running but the URL still 404s, the cloud's tunnel config got out of sync; unpair + re-pair to reissue.

## "Setting forecast / weather but no data"

- **Solcast forecast**. Needs a hobbyist API key + a registered rooftop site. Free tier is 10 polls/day; we poll every 3h (8/day). [Solcast hobbyist signup](https://solcast.com/free-rooftop-solar-forecasting).
- **Open-Meteo weather**. Needs **lat/lon** set in Settings → Integrations → Weather. No API key. If you don't see data, double-check the coords and the daemon's network access (`curl https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.1`).

## Other "ask the docs" places

- The dashboard's **Docs** tab (in-app) has the same content offline-cached.
- Email [support@wattpost.io](mailto:support@wattpost.io). Include the appliance version (Settings → About) and a copy of `journalctl -u wattpost --since '10 minutes ago'`.
