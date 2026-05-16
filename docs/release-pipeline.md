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

Three artefacts, three pipelines, all triggered by **git tags** for
release builds — only the Docker `:edge` channel publishes on every
commit. This keeps version numbers honest and stops the "Update
available" badge from nagging users on every fix-commit.

| Artefact                 | Source pipeline               | URL                                              | Trigger                                          |
| ------------------------ | ----------------------------- | ------------------------------------------------ | ------------------------------------------------ |
| `wattpost-source-*.tar.gz` | `.github/workflows/publish-source.yml` | `https://releases.wattpost.io/source/latest.tar.gz` | Git tag `v*` (or manual `workflow_dispatch`)     |
| `wattpost-*.img.xz`        | `.github/workflows/build-image.yml`     | `https://releases.wattpost.io/img/latest.img.xz`    | Git tag `v*` (or manual `workflow_dispatch`)     |
| `ghcr.io/.../wattpost-appliance:latest` | `.github/workflows/build-appliance-image.yml` | (Docker registry)                                | Git tag `v*` — gets `:vX.Y.Z`, `:X.Y`, `:latest` |
| `ghcr.io/.../wattpost-appliance:edge`   | `.github/workflows/build-appliance-image.yml` | (Docker registry)                                | Every push to `main` — gets `:edge`, `:sha-<short>` |

The source tarball is what the appliance's **Update now** button pulls down for in-place upgrades. The `.img.xz` is what a new user flashes onto a fresh SD card via Raspberry Pi Imager.

Both are anonymously fetchable. There's no auth on `releases.wattpost.io` because the contents are by definition shippable to anyone — the SD image and the source tarball are what a customer already has on their flashed Pi.

## Serving infrastructure

- **Container**: `shared-caddy` in `vps-infra/docker-compose.yml`. Same Alpine Caddy that serves `wattpost.io` + `wattpost.cloud`.
- **Host directory**: `/srv/wattpost-releases/{img,source}/` — bind-mounted read-only into the container. Created by `vps-infra/scripts/bootstrap.sh`.
- **Caddy block**: `vps-infra/caddy/Caddyfile` → `releases.wattpost.io`. `file_server browse` so the directory listing is human-readable. 24h cache header — fine because filenames are versioned.
- **DNS**: `releases.wattpost.io` A → `REDACTED-ORIGIN-IP` (Proxied through CF). Set in CF dashboard for the `wattpost.io` zone.
- **TLS**: same `cloudflare_tls` snippet (`tls internal`) used by every other proxied domain. CF zone SSL must stay on **Full** (not Strict) for this to work; documented in `vps-infra/caddy/Caddyfile` warning banner.

## Cutting a release

A normal main push automatically:

- Rebuilds + deploys the cloud (`build-cloud-image.yml`)
- Builds + pushes the appliance Docker image as `:edge` and
  `:sha-<short>` (`build-appliance-image.yml`)

That's the bleeding-edge channel — fine for our dev environment
and tester opt-in (`image: ghcr.io/ritualnorth/wattpost-appliance:edge`),
NOT what customers should track. It deliberately does NOT bump the
manifest version or build a source tarball, so paired Pi
appliances don't get nagged on every fix commit.

**To cut a real release that flows to customers**:

1. Bump `__version__` in `solar_monitor/__init__.py` (e.g. `0.0.3` → `0.0.4`).
2. Move the `[Unreleased]` block in `CHANGELOG.md` to a new
   `[0.0.4] — YYYY-MM-DD` section. Leave `[Unreleased]` empty
   above it for the next batch.
3. Commit: `git commit -am "Release v0.0.4"`.
4. Tag: `git tag v0.0.4 && git push origin main v0.0.4`.
5. CI then automatically:
   - `build-appliance-image.yml` builds + pushes `:v0.0.4`, `:0.0`,
     `:latest` Docker tags to GHCR. Docker users on `:latest` pull
     this on their next `docker compose pull && up -d`.
   - `publish-source.yml` builds the source tarball + bumps
     `releases.wattpost.io/img/manifest.json` to `0.0.4`. Pi users'
     "Update available" badge lights up within their next 24h poll
     (or via Check now).
   - `build-image.yml` runs (pi-gen, ~90 min) → attaches `.img.xz`
     to a GH Release AND scp's to `/srv/wattpost-releases/img/`.
     `latest.img.xz` symlink repointed automatically. New customers
     downloading via `/download` get the fresh image.
6. Verify (after ~90 min): `curl -sI https://releases.wattpost.io/img/latest.img.xz`
   should 200; `/download` page on `wattpost.io` should link to the
   new build; a paired Pi appliance should show "Update available
   v0.0.4" within an hour.

The pi-gen workflow takes the longest, so the rule of thumb is:
tag → take a 90 min break → check it landed. The CI emails on
failure.

### Recovering when pi-gen's publish step fails

Pi-gen successfully BUILDS the image then SCPs it to
`releases.wattpost.io` as the final step. The Azure→Contabo SSH
path occasionally route-flaps for several minutes; the workflow
retries with exponential backoff (~25 min total), but a long
outage can still bail. When that happens the image lives **only**
on the GH run as an artefact + attached to a GH Release —
customers still see the OLD image on the /download page.

Recovery from a developer laptop with VPS SSH access:

```bash
# 1. Find the failing run id (most recent pi-gen)
gh -R ritualnorth/offgrid-monitor run list --workflow=build-image.yml --limit 3

# 2. Download the artefact + verify
cd /tmp && rm -rf wp-img-tmp && mkdir wp-img-tmp && cd wp-img-tmp
gh -R ritualnorth/offgrid-monitor run download <RUN_ID>
cd wattpost-image
sha256sum -c image_*.img.xz.sha256

# 3. scp it to the VPS + repoint the symlink + rewrite manifest
IMG="image_YYYY-MM-DD-wattpost-lite.img.xz"
SHA=$(awk '{print $1}' "${IMG}.sha256")
SIZE=$(stat -c %s "${IMG}")
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
scp "${IMG}" "${IMG}.sha256" root@<vps>:/srv/wattpost-releases/img/
ssh root@<vps> "cd /srv/wattpost-releases/img; \
  ln -sfn '${IMG}' latest.img.xz; \
  ln -sfn '${IMG}.sha256' latest.img.xz.sha256; \
  jq -n --arg v 'X.Y.Z' --arg fn '${IMG}' --arg sha '${SHA}' \
    --arg ts '${NOW}' --argjson sz ${SIZE} \
    '{version:\$v, released_at:\$ts, image_filename:\$fn, \
      image_url:\"https://releases.wattpost.io/img/latest.img.xz\", \
      sha256:\$sha, sha256_url:\"https://releases.wattpost.io/img/latest.img.xz.sha256\", \
      size_bytes:\$sz, release_url:\"/docs/release-notes\", \
      source_url:\"https://releases.wattpost.io/source/latest.tar.gz\", \
      source_sha256_url:\"https://releases.wattpost.io/source/latest.tar.gz.sha256\"}' \
    > manifest.json"
```

After this, `curl -sI https://releases.wattpost.io/img/latest.img.xz`
should 200 and the /download page reflects the new build within a
minute or two.

### Customer-side update paths

| Install type | Channel | Command / action | Notes |
| --- | --- | --- | --- |
| Pi (SD card) | Stable | "Update now" button in Settings → About | Reads manifest → fetches new source tarball → wattpost-update swaps + restarts daemon |
| Docker, stable | `image: ghcr.io/ritualnorth/wattpost-appliance:latest` | `docker compose pull && docker compose up -d` | Pulls the most recent tagged release |
| Docker, edge | `image: ghcr.io/ritualnorth/wattpost-appliance:edge` | Same | Every main commit. Opt-in for testers. |
| Docker, pinned | `image: ghcr.io/.../wattpost-appliance:0.0.4` | Pull when ready | Pinned forever. Use when you want reproducibility above all. |

### What if I forgot to bump __version__?

Tag still triggers everything, but `__version__` inside the
shipped code will be wrong vs. the tag. CI doesn't validate this
yet — worth adding eventually (see backlog: validate
`__version__` matches tag in CI). For now, just be disciplined:
bump in the same commit as the tag, easy to remember.

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
