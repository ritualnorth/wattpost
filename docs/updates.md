# Updates

WattPost updates land on your Pi as **source tarballs** pulled from `releases.wattpost.io`. No package manager, no apt repo, no manual SSH steps. One click in the dashboard.

## How it works

1. The cloud manifest at [`releases.wattpost.io/img/manifest.json`](https://releases.wattpost.io/img/manifest.json) names the current published version
2. Your appliance polls that manifest every 24 hours
3. If a newer version is available, **Settings → About** shows "v0.0.X available"
4. Click **Update now** → the daemon downloads the source tarball from `releases.wattpost.io/source/latest.tar.gz`, verifies its SHA256, swaps it into `/opt/wattpost-src`, runs `install.sh`, and restarts itself

The dashboard polls the update log and reconnects to the new daemon when it comes back up. Total time: ~30 seconds.

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

For v0.0.x, the upgrade is **non-atomic** — if `install.sh` partially fails, the appliance can be in a half-upgraded state. SSH in and:

```
sudo /opt/wattpost-src.old/packaging/install.sh
```

… to roll back to the previous source tree (we keep `.old/` around for exactly this).

A fully atomic-swap updater (parallel venv, post-install health check, automatic rollback) is on the roadmap once we have customers who can't be expected to SSH-recover. For now, "SSH and re-run" is the escape hatch.

## Beta channel

Not yet shipped. Planned: an opt-in toggle in **Settings → System → Update channel** that switches the daemon to poll a different manifest (`latest-beta.json`) so volunteers see new builds days before they go to stable.

## Image upgrades

The **SD image** (the .img.xz you flashed) only really needs replacing if there's a major OS-level change — new Debian release, kernel update, new system service that can't be pulled in via source updates. Source updates handle 95% of releases. Flashing a fresh image is a once-every-six-months affair, not per-release.

To re-flash without losing data:

1. Back up `/etc/wattpost/config.yaml` and `/var/lib/wattpost/solar-monitor.db` to your laptop
2. Flash the new image
3. Boot, restore the two files
4. The appliance re-uses the existing bearer token and cloud pairing — no re-pair needed

## How we publish a release

Internal note for our future selves:

1. `git tag v0.0.X && git push --tags` triggers `build-image.yml`
2. ~90 minutes of pi-gen produces the new `.img.xz`
3. The workflow scp's the image + a fresh `manifest.json` to `releases.wattpost.io`
4. Within ~60s the cloud picks up the new manifest (in-process cache TTL)
5. Within ~24h every paired appliance discovers the new version and offers the Update button

[Source tarballs](https://releases.wattpost.io/source/latest.tar.gz) update independently — every push to `main` that touches the appliance refreshes them via the `publish-source.yml` workflow. So in-place upgrades flow continuously while image rebuilds are quarterly-ish.
