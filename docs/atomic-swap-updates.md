# Atomic-swap updates

WattPost's update flow on Pi installs uses an **A/B slot** model. Updates install into the inactive slot, get health-probed before they go live, swap into place with a single atomic operation, and auto-rollback if the daemon boot-loops afterwards.

You don't need to know any of this to use the appliance, clicking "Update now" on the dashboard, or letting the cloud auto-apply updates for an installer fleet, Just Works. This page is here for the operators who want to understand the moving parts.

## The slot layout

```
/opt/wattpost            → /opt/wattpost-slots/<active> (symlink)
/opt/wattpost-slots/
    a/                   one slot (venv + source + version)
    b/                   the other slot
    previous             → /opt/wattpost-slots/<the-other-one>
```

`/opt/wattpost` is always a symlink. systemd `wattpost.service` reads its `ExecStart` and `WorkingDirectory` from that path, so flipping the symlink and restarting the service is the entirety of an update from systemd's point of view.

## What happens when you click Update now

`/usr/local/bin/wattpost-update` does the following:

1. **Resolves the active and inactive slot** by reading `/opt/wattpost` and basename'ing the target. If active is `a`, inactive is `b`, and vice versa.
2. **Downloads the source tarball** from `https://github.com/ritualnorth/wattpost/releases/latest/download/wattpost-source.tar.gz` (a permanent redirect to the newest release's asset) and verifies its SHA256 against the matching `.sha256` file. A cache-busting query string defeats any CDN staleness.
3. **Wipes + populates the inactive slot**: fresh venv, extracts the tarball into `<inactive>/src/`, pip-installs the package into `<inactive>/venv/`. Forced reinstall, no-deps, the running active slot's venv is untouched.
4. **Health probe**: forks `solar-monitor serve` from the **inactive** venv on `127.0.0.1:18000`, with `--db :memory:` so the probe doesn't touch the real SQLite database. The probe gets a `/tmp` scratch dir for CWD. Curls `/api/health`, `/api/snapshot`, `/api/devices`, `/api/system/info`. Any non-200 → **ABORT** and leave the active slot in place. The inactive slot is preserved on disk so the operator can inspect it.
5. **Atomic symlink flip** via `mv -T` (single `rename(2)` syscall, there is no window where `/opt/wattpost` doesn't exist).
6. **Restart `wattpost.service`**. systemd picks up the new symlink target on next start.
7. **Post-swap health check**: poll the live `/api/health` for 20s. If it doesn't return 200, **auto-rollback**, flip the symlink back to the previous slot and restart again.

## The OnFailure watchdog

Even after a clean swap, the daemon can still crash later, say, against real BLE hardware that the sandbox probe didn't exercise, or against migrated config the inactive slot's venv hadn't seen. That's what the systemd `OnFailure=` watchdog catches:

```
StartLimitIntervalSec=60
StartLimitBurst=3
OnFailure=wattpost-rollback.service
```

If `wattpost.service` fails to start 3 times in 60 seconds, systemd gives up on it AND fires `wattpost-rollback.service`. That oneshot unit runs `wattpost-rollback --auto`, which flips the symlink back to `previous` and starts the daemon again.

Recovery time from a botched update is typically under 60 seconds end-to-end: the bad daemon takes ~10s to crash + retry + crash + retry + crash, hits the burst limit, OnFailure fires, rollback runs (~1s), daemon starts on the previous slot, healthy.

## Manual rollback

From the dashboard or `wattpost-config`:

- `wattpost-config` → menu option **12 (Roll back to previous slot)**
- Or call `POST /api/system/slots/rollback` against the appliance
- Or, if you enabled SSH, run `sudo /usr/local/bin/wattpost-rollback` over SSH (or from the Pi's console)

All three call the same script, so behaviour is identical.

After a rollback, `previous` points at the slot you *just left*, so a second rollback takes you back to where you were before, effectively a "redo update" gesture.

## Cloud auto-apply

If you've ticked **"Auto-apply updates fleet-wide"** in WattPost Cloud, the cloud watches each appliance's heartbeat-reported version. When it sees a Pi appliance running an older version than the latest GitHub release, it auto-queues an `update` command for that appliance, the same command the dashboard's manual "Update now" button issues. The appliance picks it up on its next heartbeat (5min default) and runs the full atomic-swap flow described above. If the new release misbehaves, the OnFailure watchdog rolls it back without your involvement.

Docker installs update the same one-click way — the **Update** button (in the appliance UI or the cloud per-site page) fires the bundled `wattpost-updater` sidecar, which pulls the new image and recreates the container via the local Docker socket. No `docker compose pull` needed (that still works as a manual fallback). The sidecar must be configured (`WATCHTOWER_URL`/`WATCHTOWER_TOKEN` + the `wattpost-updater` service — see the Docker install guide); without it, the UI falls back to telling you to pull on the host.

## What this gives you

- **Power loss mid-update is safe.** Either the symlink flipped or it didn't; both states are bootable.
- **A bad release can't brick the device.** Worst case is "stuck on the last working version."
- **No SSH required to recover.** The watchdog runs automatically; the manual rollback is a CLI option.
- **You can canary-deploy across a fleet.** Per-site `auto_apply_updates` lets you turn on auto-apply for two test sites first, then flip the rest once you're happy.

## File locations

| Path | Purpose |
| --- | --- |
| `/opt/wattpost` | Symlink to active slot |
| `/opt/wattpost-slots/a/`, `/opt/wattpost-slots/b/` | Slot directories |
| `/opt/wattpost-slots/previous` | Symlink to most-recently-demoted slot |
| `/usr/local/bin/wattpost-update` | Atomic-swap update helper |
| `/usr/local/bin/wattpost-rollback` | Rollback helper |
| `/etc/systemd/system/wattpost.service` | Main daemon unit (has `OnFailure=`) |
| `/etc/systemd/system/wattpost-rollback.service` | Oneshot fired by `OnFailure=` |
| `/var/log/wattpost-update.log` | Update history |
| `/var/log/wattpost-rollback.log` | Rollback history (manual + auto) |
