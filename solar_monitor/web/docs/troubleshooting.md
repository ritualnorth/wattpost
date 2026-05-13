# Troubleshooting

## Status pill says "X errors"

Last poll didn't reach one or more devices. Tap the pill for the
full state legend, then **Settings → Diagnostics** for the live log
tail. Common causes:

- **Just restarted the daemon?** BlueZ holds the BT-2 connection
  for up to a minute. The next poll clears it.
- **BT-2 dongle moved out of range** — typical Renogy BT-2 reach is
  ~10 m unobstructed. Check the Pi is close enough to the hub.
- **Battery off** — a sleeping smart battery doesn't answer Modbus.
  Once it wakes (e.g. starts charging) it'll come back on the next
  poll.

## Status pill says "Offline"

Scheduler isn't running. Try **Settings → System → Restart daemon**.
If that doesn't fix it, SSH in and `sudo systemctl status wattpost`.

## Daemon won't start

Common after a config error from a hand-edit:

```bash
sudo systemctl status wattpost     # short summary + last few logs
sudo journalctl -u wattpost -n 50  # last 50 lines
```

If a Settings UI write corrupted `config.yaml`, the backup is at
`/etc/wattpost/config.yaml.bak`:

```bash
sudo cp /etc/wattpost/config.yaml.bak /etc/wattpost/config.yaml
sudo systemctl restart wattpost
```

## ntfy notifications not arriving

In order of likelihood:

1. **Subscribed to the wrong topic.** The Settings → Alerts panel
   shows the topic next to each ntfy transport. Subscribe to *that*
   exact string in the ntfy app, not the transport ID.
2. **iOS notifications disabled for ntfy.** Settings → Notifications
   → ntfy → "Allow Notifications" must be on.
3. **Topic muted in the ntfy app.** Tap the topic → top-right gear →
   "Notifications" toggle.

Browser-side check: open `https://ntfy.sh/<your-topic>` and use the
"Send notification" field at the bottom. If that pops up on your
phone, our server-side is fine and it's an iOS settings issue. If
that *also* fails, fix iOS first.

## BLE scan in the setup wizard finds nothing

The wizard scans slave IDs on an already-open transport. If the
transport itself isn't connected (e.g. you just powered on the BT-2),
give it ~30 s and reload. Diagnostics will show "[hub_bt] connected"
when ready.

## Need to upgrade?

```bash
cd /opt/wattpost && sudo bash packaging/install.sh
```

Or, if you installed from git, `cd` to your checkout and re-run.
The install script is idempotent — it upgrades the venv + service
without touching your config or database.
