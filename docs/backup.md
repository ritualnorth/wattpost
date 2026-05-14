# Backup & disaster recovery

SD cards die. WattPost is built so the **data and configuration**
survive even when the hardware doesn't.

## What's on the appliance

| Path | What | Replaceable? |
|---|---|---|
| `/opt/wattpost/venv` | Python venv | Yes — re-install fetches it. |
| `/etc/wattpost/config.yaml` | Your devices + alerts + transports | **Back this up.** |
| `/var/lib/wattpost/solar-monitor.db` | All historical telemetry | Back this up if it matters. |

## Manual backup (today)

SSH into the Pi:

```bash
sudo tar czf wattpost-backup-$(date +%F).tar.gz \
    /etc/wattpost/config.yaml \
    /var/lib/wattpost/solar-monitor.db*
```

Copy that file off the Pi. Restore is the reverse — `tar xzf` into
the new SD card after installing WattPost, then restart the daemon.

## Cloud backup (coming with the cloud tier)

Once we ship the cloud tier, every poll's data syncs upward as it
happens, and your config gets pushed whenever you change it. After a
hardware failure:

1. Flash a fresh SD card.
2. Log into `app.wattpost.io`.
3. Pick the site, hit **Restore**.
4. ~10 minutes later you're back: history, paired BLE addresses,
   alert rules, exporter config — everything.

No more re-pairing four batteries by hand.

## Config edits via the UI are safe

Every mutation through Settings (devices, alerts, transports) takes
a `.bak` copy of `config.yaml` before writing. If something goes
wrong you'll find `/etc/wattpost/config.yaml.bak` next to the live
config — copy back to restore.
