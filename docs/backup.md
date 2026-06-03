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

> **Caveat:** unlike the in-app backup below, a raw `tar` copy of
> `config.yaml` **contains your plaintext third-party secrets** (SMTP
> password, MQTT credentials, Solcast / weather API keys, the hotspot
> WiFi password). Store it somewhere private and don't paste it into a
> ticket or share it.

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

> **Caveat:** as with the Pi manual path, this raw copy of `config.yaml`
> **contains plaintext third-party secrets** (SMTP / MQTT credentials,
> API keys, hotspot password) — unlike the in-app backup, which redacts
> them. Keep it private.

## Built-in local backups

**Settings → System → Backups** runs a Home-Assistant-style snapshot
flow: rotating weekly archives of `config.yaml` + the SQLite DB,
stored on the appliance itself (or any USB stick you mount). Restore
is one click from the same panel.

### What's in a backup, what's redacted

The in-app backup (and any cloud-uploaded copy) is built to be safe to
move around. Before the archive leaves the box it **redacts your
third-party secrets** — SMTP password, MQTT credentials, Solcast /
weather API keys, the hotspot WiFi password — and it **never includes
the plaintext dashboard password** (only the argon2 hash). The
appliance's own **cloud pairing tokens are kept**, so restoring onto a
fresh Pi recovers its cloud identity and history without re-pairing.

After a restore, any redacted secret comes back blank — re-enter it from
the matching Settings page, and the restore summary lists exactly which
fields to re-set.

### Selective restore

You don't have to restore everything. The restore picker lets you pull
back any combination of three independent components:

- **Data** — the SQLite DB (all history / samples).
- **Config** — `config.yaml` (devices, alerts, transports).
- **Password** — the dashboard password hash.

The common case is **data-only**: keep a clean fresh config but bring
your history back. On a true fresh install the password is never taken
from the backup — the first-boot generator mints a new one regardless,
which you read from `wattpost-config` / the SSH MOTD.

## Cloud backups (WattPost Cloud)

If the appliance is [paired to wattpost.cloud](/docs/pairing) with
WattPost Cloud, every snapshot is pushed off-site as it's taken. The
cloud keeps the last 8 weekly archives encrypted at rest.

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
See [wattpost.cloud](https://wattpost.cloud) for the full feature list.

## Config edits via the UI are safe

Every mutation through Settings (devices, alerts, transports) takes
a `.bak` copy of `config.yaml` before writing. If something goes
wrong you'll find `config.yaml.bak` next to the live config. Copy
back to restore.
