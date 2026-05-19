# Backup & disaster recovery

SD cards die. WattPost is built so the **data and configuration**
survive even when the hardware doesn't.

## What's on the appliance

| Path | What | Replaceable? |
|---|---|---|
| `/opt/wattpost/venv` (Pi) / image (Docker) | Python runtime | Yes. Re-install / re-pull fetches it. |
| `/etc/wattpost/config.yaml` | Your devices + alerts + transports | **Back this up.** |
| `/var/lib/wattpost/solar-monitor.db` | All historical telemetry | Back this up if it matters. |

## Manual backup. SD-card install

SSH into the Pi:

```bash
sudo tar czf wattpost-backup-$(date +%F).tar.gz \
    /etc/wattpost/config.yaml \
    /var/lib/wattpost/solar-monitor.db*
```

Copy that file off the Pi. Restore is the reverse · `tar xzf` into
the new SD card after installing WattPost, then restart the daemon.

## Manual backup. Docker install

Both the config and database live in the volumes you bind-mounted
into the container (`./wattpost-config/` and `./wattpost-data/` if
you used the example compose). To back up:

```bash
cd ~/wattpost
tar czf wattpost-backup-$(date +%F).tar.gz wattpost-config/ wattpost-data/
```

Restore: copy that file to the new host, untar in place, `docker
compose up -d`.

## Built-in local backups

**Settings → System → Backups** runs a Home-Assistant-style snapshot
flow: rotating weekly archives of `config.yaml` + the SQLite DB,
stored on the appliance itself (or any USB stick you mount). Restore
is one click from the same panel.

## Cloud backups (Pro tier)

If the appliance is [paired to wattpost.cloud](/docs/pairing) on the
Pro tier, every snapshot is pushed off-site as it's taken. The cloud
keeps the last 8 weekly archives encrypted at rest.

After a hardware failure:

1. Flash a fresh SD card and finish first-boot.
2. Log into `wattpost.cloud`, open the dead site, click
   **Restore from cloud → pick a snapshot**.
3. The cloud sends a one-time pairing code + restore URL to the new
   Pi; the daemon downloads the snapshot, swaps `config.yaml` + the
   DB into place, restarts.
4. ~10 minutes later you're back: history, paired BLE addresses,
   alert rules, exporter config. Everything.

No more re-pairing four batteries by hand. See
[Cloud overview](/docs/cloud-overview) for the full feature list.

## Config edits via the UI are safe

Every mutation through Settings (devices, alerts, transports) takes
a `.bak` copy of `config.yaml` before writing. If something goes
wrong you'll find `config.yaml.bak` next to the live config. Copy
back to restore.
