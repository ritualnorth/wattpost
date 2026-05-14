# Release pipeline

How a code change reaches a customer's Raspberry Pi.

```
git push main               git tag v0.0.5
       │                          │
       ▼                          ▼
publish-source.yml          build-image.yml
       │                          │
   tar.gz + sha               .img.xz + sha (pi-gen, ~90 min)
       │                          │
       ▼                          ▼
scp → VPS                   scp → VPS
       │                          │
       ▼                          ▼
/srv/wattpost-releases/      /srv/wattpost-releases/
  source/latest.tar.gz         img/latest.img.xz
       │                          │
       ▼                          ▼
       └────────┬─────────────────┘
                ▼
       shared-caddy (Docker, vps-infra)
       releases.wattpost.io (CF proxied)
                │
        ┌───────┴────────┐
        ▼                ▼
  Appliance         /download page on
  Update-now        wattpost.io (anonymous
  (auth-free,       binary fetch)
  sha-verified)
```

## What gets released

Two artefacts, two channels, two cadences:

| Artefact                 | Source pipeline               | URL                                              | Trigger                                          |
| ------------------------ | ----------------------------- | ------------------------------------------------ | ------------------------------------------------ |
| `wattpost-source-*.tar.gz` | `.github/workflows/publish-source.yml` | `https://releases.wattpost.io/source/latest.tar.gz` | Every push to `main` touching `solar_monitor/`, `packaging/`, or `pyproject.toml` |
| `wattpost-*.img.xz`        | `.github/workflows/build-image.yml`     | `https://releases.wattpost.io/img/latest.img.xz`    | Git tag `v*` (or manual `workflow_dispatch`)     |

The source tarball is what the appliance's **Update now** button pulls down for in-place upgrades. The `.img.xz` is what a new user flashes onto a fresh SD card via Raspberry Pi Imager.

Both are anonymously fetchable. There's no auth on `releases.wattpost.io` because the contents are by definition shippable to anyone — the SD image and the source tarball are what a customer already has on their flashed Pi.

## Serving infrastructure

- **Container**: `shared-caddy` in `vps-infra/docker-compose.yml`. Same Alpine Caddy that serves `wattpost.io` + `app.wattpost.io`.
- **Host directory**: `/srv/wattpost-releases/{img,source}/` — bind-mounted read-only into the container. Created by `vps-infra/scripts/bootstrap.sh`.
- **Caddy block**: `vps-infra/caddy/Caddyfile` → `releases.wattpost.io`. `file_server browse` so the directory listing is human-readable. 24h cache header — fine because filenames are versioned.
- **DNS**: `releases.wattpost.io` A → `REDACTED-ORIGIN-IP` (Proxied through CF). Set in CF dashboard for the `wattpost.io` zone.
- **TLS**: same `cloudflare_tls` snippet (`tls internal`) used by every other proxied domain. CF zone SSL must stay on **Full** (not Strict) for this to work; documented in `vps-infra/caddy/Caddyfile` warning banner.

## Cutting a release

A normal main push automatically:

- Rebuilds + deploys the cloud (`build-cloud-image.yml`)
- Bundles a new source tarball if the push touches the appliance (`publish-source.yml` → `releases.wattpost.io/source/latest.tar.gz`)

That's enough to flow features to **already-paired appliances** via the Update-now button. Cutting a fresh SD image is a deliberate step:

1. Bump `__version__` in `solar_monitor/__init__.py`.
2. Update `LATEST["version"]` and `LATEST["released_at"]` in `cloud/wattpost_cloud/releases.py` so paired appliances see "new version available" in Settings → About.
3. Commit, push to main.
4. Tag: `git tag v0.0.X && git push --tags`.
5. `build-image.yml` runs (pi-gen, ~90 min) → attaches `.img.xz` to a GH Release in the private repo (internal archive) AND scp's to `/srv/wattpost-releases/img/`. The `latest.img.xz` symlink is repointed at the new filename automatically.
6. Verify: `curl -sI https://releases.wattpost.io/img/latest.img.xz` should 200; the `/download` page on `wattpost.io` should now link to the new build.

The pi-gen workflow takes the longest, so the rule of thumb is: tag → take a 90 min break → check it landed. The CI emails on failure.

## How an appliance updates

The flow is implemented across:

- `solar_monitor/update/checker.py` — daily poll of `/api/releases/latest` from the cloud.
- `solar_monitor/api/system.py` — endpoints: `GET /api/system/update`, `POST /api/system/update/check`, `POST /api/system/update/apply`, `GET /api/system/update/log`.
- `solar_monitor/web/app.js` — the **Update now** button in Settings → About, with live log polling.
- `packaging/cli/wattpost-update` — root-owned helper that does the actual fetch+verify+swap+install.
- `/etc/sudoers.d/wattpost` — NOPASSWD grant for `/usr/local/bin/wattpost-update` (no args; the daemon can't pass arbitrary URLs).

Step by step:

1. User clicks **Update now** in the appliance dashboard.
2. Browser POSTs `/api/system/update/apply`.
3. Daemon sudo-execs `/usr/local/bin/wattpost-update` with `setsid + start_new_session` so the child survives the daemon's eventual restart.
4. Helper:
   - `flock` a lock at `/run/wattpost-update.lock` to prevent concurrent updates.
   - `curl` the tarball from `https://releases.wattpost.io/source/latest.tar.gz` + `.sha256`.
   - Verify SHA256.
   - Extract to `/opt/wattpost-src.new`, atomic-rename to `/opt/wattpost-src`.
   - Run `packaging/install.sh` from the new tree. That reinstalls the venv, refreshes the systemd unit, and calls `systemctl restart wattpost` at the end.
5. While that's happening, the UI polls `/api/system/update/log` every 2 s and renders `/var/log/wattpost-update.log`. The log file is group-readable by the `wattpost` user so the daemon can serve it.
6. The dashboard reconnects on the new version. The "Latest available" row in Settings flips back to matching `Current`.

If the daemon was started before the helper was installed (legacy paired appliances from before this change), running install.sh once via SSH or `wattpost-config` → Restart wattpost picks everything up.

## Operator runbook

**Pi-gen build fails.** Most common reason historically has been qemu emulation bugs (see `.github/workflows/build-image.yml` — we're pinned to `tonistiigi/binfmt:latest` for qemu 9.x because Ubuntu's apt qemu segfaults Python 3.13 at random). Pull the failed log: `gh run view <run-id> --log-failed | tail -100`. Look at what stage died; if it's deep in pi-gen's own apt-install loop and we haven't changed pi-gen, retry — there's still some qemu flakiness even with the newer build.

**scp step fails.** The build runs as a GH Actions hosted runner, talking to the VPS over SSH with the `VPS_SSH_KEY` secret. Same key as `build-cloud-image.yml`'s deploy step. If scp 502s or auth-fails, regenerate the key on the VPS, update GH secrets.

**releases.wattpost.io returns 521.** CF can't reach origin. Check (in this order):

1. `dig +short releases.wattpost.io @1.1.1.1` — should return CF IPs (104.x or 172.x).
2. CF dashboard → DNS — record should be `A` / `releases` / `REDACTED-ORIGIN-IP` / Proxied.
3. `curl -sI --resolve releases.wattpost.io:443:REDACTED-ORIGIN-IP https://releases.wattpost.io/ -k` — should 200 from inside the VPS. If yes, the break is CF→origin; if no, Caddy isn't serving it.
4. Caddy block syntax: `docker exec shared-caddy caddy validate --config /etc/caddy/Caddyfile`.

**`latest.img.xz` points to wrong file / nothing.** The `build-image.yml`'s "Update `latest` symlink" step picks the newest `wattpost-*.img.xz` by mtime. SSH in, `cd /srv/wattpost-releases/img && ls -lt`, and `ln -sfn <correct-file> latest.img.xz` manually.

**Update-now on the appliance silently fails.** Check `/var/log/wattpost-update.log` on the appliance (also tail-able via `wattpost-config` → Recent logs). Common causes: tarball 404 (releases.wattpost.io down, see above); SHA mismatch (corrupt download, retry); install.sh apt-install fails (no network, log says so).

## Related docs

- [`docs/architecture.md`](architecture.md) — overall appliance design
- [`docs/cloud-architecture.md`](cloud-architecture.md) — cloud + tunnel pattern
- `packaging/README.md` — install.sh / systemd unit specifics
