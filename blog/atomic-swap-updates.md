# Your WattPost can never brick itself on an update

Most off-grid solar monitors update like a 2010 Linux box: SSH in, run the install script, hope it doesn't die halfway through a `pip install`, hope the new code actually starts up. If it doesn't, you're either driving back to the cabin with a screwdriver, dragging an SD-card writer to wherever the Pi lives, or telling a customer "yeah, it'll be fixed when I'm next on-site."

WattPost v0.1.31 ships a different model. Updates can't brick the device. Power loss mid-install can't brick the device. A genuinely broken release can't brick the device. The worst case is "stuck on the version you had yesterday, dashboard healthy, no SSH required."

Here's what changed and why we're a bit proud of it.

## The old way (now legacy on the Pi side)

Old flow:

1. Daemon polls cloud once a day, sees "v0.1.30 available."
2. You click "Update now" on Settings → System.
3. `wattpost-update` runs as root: downloads tarball, verifies SHA256, unpacks into `/opt/wattpost-src/`, runs `install.sh`.
4. `install.sh` reinstalls the Python venv in-place at `/opt/wattpost/venv/`, then `systemctl restart wattpost`.

This is fine when it works. It's bad when it doesn't, because everything happens to the live install:

- pip install dies halfway → venv half-replaced, daemon won't start
- new daemon imports cleanly but crashes against your real BLE hardware → daemon refuses to boot
- the SD card gets pulled mid-update → corrupt venv, dead appliance

The fallback in every one of those cases used to be "SSH in and re-run install.sh." For a battery monitor sitting in a van four hours away, that's a five-hour round trip to fix a hiccup.

## The new way: A/B slots and a safety net

WattPost now keeps two copies of itself on disk:

```
/opt/wattpost          → symlink to one of the two below
/opt/wattpost-slots/
    a/                  one slot — venv + source + version
    b/                  the other slot
    previous            → symlink to whichever slot isn't active
```

`systemd` reads `/opt/wattpost/venv/bin/solar-monitor` and resolves the symlink. The daemon doesn't know or care which slot it's in.

### Step-by-step what an update does

1. **Pick the inactive slot.** If `a` is live, the update targets `b`. The live slot is never touched until the very end.
2. **Wipe + build.** The new tarball is downloaded, SHA256-verified, extracted into `b/src/`, and a fresh Python venv is built into `b/venv/`. Your data, your config, your live daemon — all untouched.
3. **Health probe before swap.** The updater boots the new daemon **in a sandbox**: on `127.0.0.1:18000`, with `--db :memory:` (no SQLite writes), in a `/tmp` working directory. It then curls `/api/health`, `/api/snapshot`, `/api/devices`, `/api/system/info`. If any of those return anything other than 200, the update **aborts** — the inactive slot is left on disk for inspection, and your live slot keeps running exactly as it was.

   This catches the kind of bug that bit us in v0.1.29 (a Python import error that only surfaced in demo mode). With the probe in place, that release would have aborted before it ever swapped in.

4. **Atomic flip.** If the probe passes, the symlink swap happens with a single Linux `rename(2)` syscall — `mv -T`. There is no moment in time where `/opt/wattpost` doesn't exist. Power loss between the probe and the flip leaves the old slot active; power loss after the flip leaves the new slot active. Either way, the system boots.

5. **Restart and re-probe.** `systemctl restart wattpost.service` picks up the new venv. The updater then polls `/api/health` against the live daemon for 20 seconds. If the new slot doesn't come up — boots fine in sandbox but fails against real hardware/config — the updater **automatically rolls back**: flips the symlink to the previous slot, restarts again. By the time the rollback completes you're back on the version you started the update with, dashboard healthy, total impact ≈30 seconds of downtime.

### And the watchdog catches what the updater can't

Some failures only show up minutes after a clean restart — say, a poll cycle that crashes against a specific BLE device, or a memory leak that only matters under real load. For those, the safety net is `systemd` itself:

```
StartLimitIntervalSec=60
StartLimitBurst=3
OnFailure=wattpost-rollback.service
```

If the daemon fails to start three times in sixty seconds, systemd gives up AND fires `wattpost-rollback.service` automatically. That oneshot unit flips the symlink back to the previous slot and starts the daemon there. From bad-release-installed to running-on-the-old-version-again is typically under a minute, with no human involvement.

## What an installer gets

The cloud dashboard has a new toggle on the fleet header: **Auto-apply updates fleet-wide**. Tick it on, and every Pi appliance in your fleet auto-updates the next time it sees a new release. The probe + watchdog + rollback machinery runs per-appliance. A bad release lands you back on the previous version on each site that auto-applied, automatically.

Together: zero-touch fleet updates with per-appliance rollback. The exact thing every installer wants but most off-grid kit forces them to handle by hand.

## What we tested

This isn't a "we read the systemd docs and shipped it" feature. Each piece was verified on a real Linux box in our Proxmox lab:

- **Atomic swap end-to-end.** Cloned a fresh Ubuntu cloud-init VM, installed v0.1.28, ran `wattpost-update`, watched it install v0.1.30 into the inactive slot, probe pass, atomic flip, daemon restart, healthy in under 90 seconds total.
- **Pre-swap probe abort.** Stubbed a broken `/api/snapshot` into the inactive slot, ran update — health probe failed, abort fired, live slot was untouched. Daemon's still running the previous version.
- **OnFailure watchdog.** Deliberately sabotaged the post-swap venv (deleted the daemon binary), restarted the service. systemd hit the StartLimit threshold in 19 seconds, fired `wattpost-rollback.service`, daemon was healthy again on the previous slot. Total recovery: ~30 seconds.

If you've ever bricked a Pi pushing an update through `apt`, you know how good "can't brick on update" feels.

## What about Docker users?

Docker installs already have atomic-swap-equivalent semantics built in — `docker compose pull && docker compose up -d` swaps the entire container atomically, and the previous image stays in the local cache so you can roll back manually with `docker run` against the old tag. The slot model adds nothing on Docker; the auto-apply toggle on the cloud dashboard correctly skips Docker appliances.

## Existing Pi customers

Anyone on a pre-v0.1.30 install has a latent bug in the *old* `wattpost-update` (it ran but didn't actually swap the venv contents — see v0.1.30 changelog for the gory pip `--force-reinstall` story). After the one-time recovery (`sudo bash /opt/wattpost-src/packaging/install.sh` once), the new atomic-swap machinery takes over and every subsequent update gets the full safety net.

Existing v0.1.30 installs upgrade cleanly via their next `Update now` click — the v0.1.30 install.sh that's already on those boxes runs the slot migration the first time it's called from `wattpost-update`.

## What's next

This is the foundation we needed before we could offer "auto-apply across your fleet" with a straight face. With it in place, the next thing on the roadmap is a **beta release channel** — opt-in tag for testers who want the bleeding edge while paying customers stay on the stable `:latest`. The atomic-swap model makes that safe: a beta tester running a flaky build doesn't risk an unrecoverable state; the watchdog brings them back to the last known-good slot.

If you have a fleet you'd like to enrol once the beta channel ships, hit reply. We're looking for three to five installers willing to break things on purpose so the rest of the customer base never does.

— Ritual North
