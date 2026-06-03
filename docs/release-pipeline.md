# Release pipeline

How a code change reaches a customer's Raspberry Pi.

```
git push main               git tag v0.0.5
       │                          │
       ▼                          ▼
build-appliance-image.yml   publish-source.yml + build-image.yml
  (:edge Docker tag)          tar.gz+sha      .img.xz+sha (pi-gen, ~90 min)
                                  │                  │
                                  ▼                  ▼
                         attached as assets to the GitHub Release
                                  │
                          ┌───────┴────────┐
                          ▼                ▼
                    Appliance          /download page on
                    Update-now         wattpost.io → links to
                    (auth-free,        the GitHub Release asset
                     sha-verified)
```

## What gets released

Three artefacts, all triggered by **git tags** for release builds. Only the
Docker `:edge` channel publishes on every commit. This keeps version numbers
honest and stops the "Update available" badge from nagging users on every
fix-commit.

| Artefact | Source pipeline | Where it lands | Trigger |
| --- | --- | --- | --- |
| `wattpost-source.tar.gz` | `publish-source.yml` | GitHub Release asset (stable URL `releases/latest/download/wattpost-source.tar.gz`) | Git tag `v*` (or `workflow_dispatch`) |
| `image_*.img.xz` | `build-image.yml` | GitHub Release asset | Git tag `v*` (or `workflow_dispatch`) |
| `ghcr.io/.../wattpost-appliance:latest` | `build-appliance-image.yml` | GHCR. Gets `:vX.Y.Z`, `:X.Y`, `:latest` | Git tag `v*` |
| `ghcr.io/.../wattpost-appliance:edge` | `build-appliance-image.yml` | GHCR. Gets `:edge`, `:sha-<short>` | Every push to `main` |

The source tarball is what the appliance's **Update now** button pulls down for
in-place upgrades. The `.img.xz` is what a new user flashes onto a fresh SD card
via Raspberry Pi Imager.

Everything is anonymously fetchable — public-repo GitHub Release assets download
without auth, which is fine because the contents are by definition shippable to
anyone.

## Serving infrastructure

Release artefacts live as **GitHub Release assets** on the public
`ritualnorth/wattpost` repo. There is no self-hosted release host — the build
workflows attach the `.img.xz` + source tarball to the Release for the tag, and:

- The appliance update helper pulls source from the stable URL
  `https://github.com/ritualnorth/wattpost/releases/latest/download/wattpost-source.tar.gz`
  (a permanent redirect to the newest release's stable-named asset).
- The cloud's `/api/releases/latest` reads the **GitHub Releases API** for the
  current version + asset URLs; `/download` on `wattpost.io` links to the GitHub
  asset.
- Release notes are read raw from the repo:
  `https://raw.githubusercontent.com/ritualnorth/wattpost/main/CHANGELOG.md`.

> History: artefacts used to be `scp`'d to a self-hosted `releases.wattpost.io`
> static host (Caddy on a VPS). That host was retired on 2026-06-03 when the repo
> went public and GitHub Releases became the anonymous distribution channel.

## Cutting a release

A normal main push automatically:

- Rebuilds + deploys the cloud (`build-cloud-image.yml`)
- Builds + pushes the appliance Docker image as `:edge` and `:sha-<short>`
  (`build-appliance-image.yml`)

That's the bleeding-edge channel — fine for dev + tester opt-in
(`image: ghcr.io/ritualnorth/wattpost-appliance:edge`), NOT what customers track.
It deliberately does NOT cut a release, so paired Pi appliances aren't nagged on
every fix commit.

**To cut a real release that flows to customers**:

1. Bump `__version__` in `solar_monitor/__init__.py` (e.g. `0.0.3` → `0.0.4`).
   Do this in the **same commit** as the tag — the artefacts are named/stamped
   from `__version__`, so a tag ahead of the code version ships a mislabelled
   build (and a never-clearing "Update available" badge).
2. Move the `[Unreleased]` block in `CHANGELOG.md` to a new
   `[0.0.4] - YYYY-MM-DD` section. Leave `[Unreleased]` empty above it.
3. Commit: `git commit -am "Release v0.0.4"`.
4. Tag: `git tag v0.0.4 && git push origin main v0.0.4`.
5. CI then automatically:
   - `build-appliance-image.yml` builds + pushes `:v0.0.4`, `:0.0`, `:latest` to
     GHCR. Docker users on `:latest` pull this on their next
     `docker compose pull && up -d`.
   - `publish-source.yml` builds the source tarball + attaches it (plus a
     stable-named `wattpost-source.tar.gz`) to the GitHub Release. Pi users'
     "Update available" badge lights up within their next 24h poll (or via
     Check now) — the cloud picks the new tag up from the GitHub Releases API.
   - `build-image.yml` runs (pi-gen, ~90 min) → attaches the `.img.xz` + sha to
     the GitHub Release. New customers downloading via `/download` get it.
6. Verify (after ~90 min): the `v0.0.4` GitHub Release carries the `.img.xz` +
   `wattpost-source.tar.gz` assets; `/download` on `wattpost.io` links to the new
   image; a paired Pi shows "Update available v0.0.4" within an hour.

The pi-gen workflow takes the longest: tag → 90 min break → check it landed. CI
emails on failure.

## Release channels (#11)

Appliances pick a channel in **Settings → About**. Each channel is a separate
stream of the artefacts above:

| Channel | Who | Docker tag | Pi image (GitHub Release) | Trigger |
| --- | --- | --- | --- | --- |
| stable | customers | `:latest` | newest **non-prerelease** release | **final** tag `vX.Y.Z` |
| beta | testers | `:beta` | newest **prerelease** release | **pre-release** tag `vX.Y.Z-rcN` |
| edge | dev | `:edge` | — (Docker-only) | every push to `main` |

The appliance's daily poll hits `…/api/releases/latest?channel=<ch>`; the cloud
maps **stable → newest non-prerelease** release, **beta → newest prerelease**,
and degrades edge (and a not-yet-published channel) to stable so no appliance
ever sees the 0.0.1 fallback.

**Beta sits between edge and stable.** edge is every commit; beta is a tagged
release candidate that hasn't soaked; stable is a final release. A **final** tag
is published as a normal (latest) Release; a release candidate is published as a
**prerelease**, so it's the newest beta without becoming "latest" — beta testers
move forward but stable users are untouched.

**Edge is Docker-only** — we don't pi-gen every commit. On a Pi the edge channel
still version-checks but an in-place apply uses the latest beta build; the
Settings selector says as much.

**To cut a beta / release candidate**:

1. Bump `__version__` to a semver pre-release, e.g. `0.2.0-rc1` (the hyphen is
   what routes the build to the beta channel everywhere — Docker `latest=auto`
   skips `:latest`, both Pi workflows detect the `*-*` version, and the
   GitHub Release is marked **prerelease** so `releases/latest` keeps pointing at
   the last stable build).
2. Tag `v0.2.0-rc1` and push. Only `:beta` + the prerelease Release move;
   `:latest` and the stable "latest" Release are untouched.
3. When the RC has soaked, cut the final `v0.2.0` (drop the `-rcN`). That
   publishes a normal (latest) Release — the newest beta too.

### Recovering when pi-gen's publish step fails

Pi-gen BUILDS the image then the "Attach to release" step uploads it to the
GitHub Release. If that step fails (rare — it's a GitHub-to-GitHub upload now),
just **re-run the workflow on the tag** — `action-gh-release` appends to the
existing Release without losing its body. Or grab the artefact from the run and
upload it by hand — no host access needed:

```bash
# 1. Find the failing run id (most recent pi-gen)
gh -R ritualnorth/wattpost run list --workflow=build-image.yml --limit 3

# 2. Download the image from the run, verify, attach to the Release
cd /tmp && rm -rf wp-img-tmp && mkdir wp-img-tmp && cd wp-img-tmp
gh -R ritualnorth/wattpost run download <RUN_ID>
sha256sum -c image_*.img.xz.sha256
gh -R ritualnorth/wattpost release upload vX.Y.Z image_*.img.xz image_*.img.xz.sha256
```

The cloud picks the asset up from the GitHub Releases API within ~60 s, so
`/download` reflects it shortly after.

### Customer-side update paths

| Install type | Channel | Command / action | Notes |
| --- | --- | --- | --- |
| Pi (SD card) | Stable | "Update now" in Settings → About | Fetches the source tarball from GitHub → `wattpost-update` swaps + restarts the daemon |
| Docker, stable | `image: ghcr.io/ritualnorth/wattpost-appliance:latest` | `docker compose pull && docker compose up -d` | Pulls the most recent tagged release |
| Docker, edge | `image: ghcr.io/ritualnorth/wattpost-appliance:edge` | Same | Every main commit. Opt-in for testers. |
| Docker, pinned | `image: ghcr.io/.../wattpost-appliance:0.0.4` | Pull when ready | Pinned forever. Use when you want reproducibility above all. |

### What if I forgot to bump __version__?

The tag still triggers everything, but `__version__` inside the shipped code is
wrong vs. the tag — the source tarball is named from `__version__`, and a paired
appliance self-reports the code version, so the "Update available" badge never
clears. Fix: bump `__version__`, re-commit, and move the tag onto the corrected
commit. CI doesn't validate tag-vs-version yet (backlog item). For now: bump in
the same commit as the tag.

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
   - `curl` the tarball from `https://github.com/ritualnorth/wattpost/releases/latest/download/wattpost-source.tar.gz` + `.sha256` (override with `WATTPOST_SOURCE_URL` for a mirror or testing).
   - Verify SHA256.
   - Extract + build a fresh venv in the inactive slot, health-probe it, then atomic-flip the `/opt/wattpost` symlink and `systemctl restart wattpost`. Auto-rolls-back if the new slot doesn't come up.
5. While that's happening, the UI polls `/api/system/update/log` every 2 s and renders `/var/log/wattpost-update.log`.
6. The dashboard reconnects on the new version. The "Latest available" row in Settings flips back to matching `Current`.

> Docker installs don't use this helper — they update with `docker compose pull && docker compose up -d` (the helper is Pi/source-install only).

## Operator runbook

**Pi-gen build fails.** Most common reason historically has been qemu emulation
bugs (see `.github/workflows/build-image.yml`; we're pinned to
`tonistiigi/binfmt:latest` for qemu 9.x because Ubuntu's apt qemu segfaults
Python 3.13 at random). Pull the failed log: `gh run view <run-id> --log-failed | tail -100`.
If it's deep in pi-gen's own apt-install loop and we haven't changed pi-gen,
retry.

**The "Attach to release" step fails.** It's a GitHub-to-GitHub upload now, so
this is rare. Re-run the workflow on the tag (it appends to the existing
Release), or upload the asset by hand (see "Recovering when pi-gen's publish step
fails" above).

**A tag's GitHub Release is missing an asset.** Check the run for the relevant
workflow (`build-image.yml` for the image, `publish-source.yml` for the source).
If the run succeeded but the asset is missing, re-run it or `gh release upload`
the artefact manually. The cloud refreshes from the GitHub API every ~60 s.

**Update-now on the appliance silently fails.** Check `/var/log/wattpost-update.log`
on the appliance (also tail-able via `wattpost-config` → Recent logs). Common
causes: tarball 404 (the GitHub Release is missing the `wattpost-source.tar.gz`
asset — see above); SHA mismatch (corrupt download, retry); install fails (no
network, log says so).

## Related docs

- [`docs/architecture.md`](architecture.md) — overall appliance design
- `packaging/README.md` — install.sh / systemd unit specifics
