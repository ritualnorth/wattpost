# Updates

WattPost ships fast, updates come weekly. You have three ways to take them.
The cloud-driven path is the safe one: snapshot before, auto-rollback after,
restore from cloud if anything goes really wrong.

## 1. Cloud "Update now" button (recommended)

The per-site dashboard at `/app/site/{id}` has an **Update** button. One click
fires the full safety chain, same flow on Pi and on Docker:

1. **Local snapshot.** The appliance writes a fresh tarball of config + DB.
2. **24h cloud-backup check.** If your most recent cloud-stored backup is
   older than 24 hours (or doesn't exist), the cloud queues a `backup_now`
   first. The appliance uploads the archive before any update touches the box.
3. **Update.** Pi runs `wattpost-update` (atomic slot swap); Docker hits the
   `wattpost-updater` sidecar (pulls + restart via the local docker socket).
4. **Watch for the new-version heartbeat.** The cloud reconciles
   automatically once the upgraded daemon checks in with a bumped version.
5. **Auto-rollback if it wedges.** If no new-version heartbeat arrives within
   10 minutes, the cloud marks the update failed AND queues a rollback to
   the previous version automatically. Pi uses `wattpost-rollback` (slot
   symlink swap); Docker pins the previous image tag.
6. **Cloud-restore as the last-resort safety net.** If autorollback doesn't
   recover the box, your snapshot from step 1 is restorable in one click
   from **/app/site/{id} → Cloud backups**.

You also get a permanent **Update history** card on the per-site page
showing every update, every rollback, and a one-click "Roll back to v0.X.Y"
button for any past version.

**WattPost Cloud** can run the same chain across sites: **Update all
out-of-date sites** queues the full snapshot → check → update → watchdog
→ auto-rollback sequence per site independently. Per-site progress
surfaces in the response.

## 2. Watchtower / auto-poll (Docker)

The `wattpost-updater` sidecar polls GHCR daily on its own. If you toggle
`auto_apply_updates` ON for the appliance (cloud dashboard), this becomes
fully hands-off: new image → snapshot → restart → cloud watches → auto-
rollback if it goes wrong. No clicks.

## 3. Manual

### On the SD-card install (Pi)

1. The cloud's [`/api/releases/latest`](https://wattpost.cloud/api/releases/latest) — which reads the GitHub Releases API — names the current published version.
2. Your appliance polls that manifest every 24 hours.
3. If a newer version is available, **Settings → About** shows "v0.0.X available".
4. Click **Update now** in the appliance's local dashboard → `wattpost-update`
   downloads the tarball, verifies SHA256, builds into the inactive slot,
   health-probes, swaps the symlink, and restarts.

### On the Docker install

```
cd ~/wattpost
docker compose pull   # fetch newest image
docker compose up -d  # roll the container
```

That's the update. The `latest` tag follows main; for traceability, pin to a
`sha-<short>` tag in your compose file. Your config + history persist via the
bind-mounted volumes.

## What changes between updates

- Dashboard JS / CSS / HTML
- Daemon Python code
- New vendor drivers
- Bug fixes
- Sometimes new packaging files (systemd unit, sudoers rules, motd, wattpost-config menu)

`install.sh` re-runs end-to-end, so it correctly applies any of those changes.

## What doesn't change

- Your `config.yaml` (devices, transports, alert rules)
- Your SQLite database (history, samples)
- Your local web password
- Your cloud pairing token

`install.sh` checks for existing config and keeps it untouched.

## What if an update breaks something?

The cloud-driven path (option 1 above) is layered defence in depth, multiple
things have to fail before you lose data or end up with a broken appliance.

| Layer | What protects you | Where it lives |
|---|---|---|
| 1. Local snapshot before update | Tarball of config + DB sits next to the appliance on its local disk | Pi: `wattpost-update`; Docker: cloud dispatcher |
| 2. 24h cloud-backup check | Cloud guarantees a fresh off-box copy exists before letting an update touch the appliance | Cloud-side, automatic |
| 3. Atomic update | Pi: slot-swap with health probe; Docker: image pull + restart through compose | `wattpost-update` / `wattpost-updater` |
| 4. Auto-rollback on failure | If no new-version heartbeat in 10 min, cloud queues a rollback automatically | Cloud-side watchdog |
| 5. Manual 1-click rollback | "Roll back to v0.X.Y" button on **/app/site/{id}** for any successful past update | Cloud UI |
| 6. Restore from cloud backup | Pull the snapshot from step 1 + step 2 back onto the appliance from **/app/site/{id} → Cloud backups** | Cloud UI |

Pi also keeps the previous slot on disk after every update; `wattpost-rollback`
(or the cloud's auto-rollback) swings the symlink back in seconds.

If you ran the manual path (option 3) and it broke, SSH in and re-run
`wattpost-update` to get the previous slot back, or run `wattpost-rollback`.
On Docker, pin the previous image tag in your `docker-compose.yml` and
`docker compose up -d`.

## Release channels

Pick a channel in **Settings → About**. Each one follows a different release stream:

- **Stable** (default) — tagged releases that have soaked. What customers run.
- **Beta** — release candidates the moment they're cut, before the soak. Pre-release, may be unstable; opt in to see new builds days before they reach stable.
- **Edge** — every commit to `main`. Bleeding edge, expect breakage. Docker-only (there's no per-commit Pi image), so on a Pi the edge channel version-checks but an in-place apply uses the latest beta build.

Your appliance's daily poll carries the chosen channel, and the cloud serves the matching build. See [Release pipeline](/docs/release-pipeline#release-channels-11) for how the streams map to GitHub Releases and Docker tags.

## Image upgrades

The **SD image** (the .img.xz you flashed) only really needs replacing if there's a major OS-level change. New Debian release, kernel update, new system service that can't be pulled in via source updates. Source updates handle 95% of releases. Flashing a fresh image is a once-every-six-months affair, not per-release.

To re-flash without losing data:

1. Back up `/etc/wattpost/config.yaml` and `/var/lib/wattpost/solar-monitor.db` to your laptop
2. Flash the new image
3. Boot, restore the two files
4. The appliance re-uses the existing bearer token and cloud pairing. No re-pair needed

## How we publish a release

Internal note for our future selves:

1. `git tag v0.0.X && git push --tags` triggers `build-image.yml`
2. ~90 minutes of pi-gen produces the new `.img.xz`
3. The workflow attaches the image to the tag's GitHub Release
4. Within ~60s the cloud picks up the new release from the GitHub API (in-process cache TTL)
5. Within ~24h every paired appliance discovers the new version and offers the Update button

[Source tarballs](https://github.com/ritualnorth/wattpost/releases/latest/download/wattpost-source.tar.gz) update independently. Every push to `main` that touches the appliance refreshes them via the `publish-source.yml` workflow. So in-place upgrades flow continuously while image rebuilds are quarterly-ish.
