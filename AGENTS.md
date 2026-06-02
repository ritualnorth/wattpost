# Repository orientation

This is **WattPost** — an off-grid solar monitoring appliance.
`solar_monitor/` is the daemon that runs on a Pi (or any Linux
Docker host), polls Renogy/Victron/JK BMS/EG4/Deye/Voltronic/etc.
over Bluetooth or wired serial, and serves a local dashboard.

The optional cloud companion (multi-site fleet, push, encrypted
backups) lives in a separate private repo. Code under this repo
is local-first by definition.

Read in this order if you're new:

1. **[README.md](README.md)** — what the product is, top-level layout, how to install
2. **[docs/architecture.md](docs/architecture.md)** — appliance internals (transports, scheduler, storage, vendor drivers)
3. **[docs/release-pipeline.md](docs/release-pipeline.md)** — how features reach customers (SD images + tarballs + Update-now)
4. **[docs/adding-a-vendor.md](docs/adding-a-vendor.md)** — add a new battery/charger driver

## Repo conventions

- **Public, Apache 2.0.** Don't commit anything cloud-side, SaaS-side, or commercially sensitive here.
- **Binary distribution via `releases.wattpost.io`** (self-hosted, see `docs/release-pipeline.md`).
- **Commit style.** Plain technical voice, imperative mood; no personal names or session-diary narratives.

## Common ops

| Task                                  | Where                                                                 |
| ------------------------------------- | --------------------------------------------------------------------- |
| **Cut a real release** (see below)    | Bump `__version__` + CHANGELOG, commit, `git tag vX.Y.Z`, push tag.   |
| Ship a feature to bleeding-edge testers | Just push to `main`. Builds `:edge` Docker tag, no manifest bump.    |
| Debug a failed pi-gen run             | `gh run view <id> --log-failed`. Common failure modes in release-pipeline.md operator runbook. |

## Cutting a release (the ritual)

```bash
# 1. Bump version. Two characters change.
sed -i 's/__version__ = "0.0.3"/__version__ = "0.0.4"/' solar_monitor/__init__.py

# 2. Move the [Unreleased] block in CHANGELOG.md to a new
#    [0.0.4] — YYYY-MM-DD section. Leave [Unreleased] empty
#    above it for the next batch.

# 3. Commit (push to main fires :edge build only — harmless).
git add solar_monitor/__init__.py CHANGELOG.md
git commit -m "Release v0.0.4"
git push origin main

# 4. Tag + push tag. THIS is what fires the real release.
git tag v0.0.4
git push origin v0.0.4
```

The tag push triggers, in parallel:

- **`build-appliance-image.yml`** → pushes Docker tags `:v0.0.4`,
  `:0.0`, `:latest` to GHCR. Docker users on `:latest` get it on
  next `docker compose pull`.
- **`publish-source.yml`** → uploads source tarball to
  `releases.wattpost.io/source/` + bumps `manifest.json` version
  to 0.0.4. Pi users see "Update available v0.0.4" badge within
  their next poll cycle (or via Check now).
- **`build-image.yml`** → pi-gen builds the SD `.img.xz` (~90 min),
  scp's to `releases.wattpost.io/img/`. `/download` page on
  wattpost.io serves the new image.

### When to cut a release

- After landing a coherent batch of fixes/features, especially
  anything user-visible.
- NOT after every commit — that's what `:edge` is for. Customers
  on `:latest` should see batched, version-numbered releases.

### Things that often go wrong

- **Forgetting to bump `__version__`**: tag fires, but
  `__version__` inside the shipped code is wrong. Always bump
  in the same commit as the tag intent. If you forget: rebase,
  fix, re-tag.
- **CHANGELOG entry missing**: less catastrophic, but customers
  won't know what changed. Populate from the commit log since
  the previous tag (`git log v0.0.3..HEAD --oneline`).
- **Tag already exists**: `git tag -d v0.0.4 && git push origin :v0.0.4`
  to delete locally and remotely, then re-tag. Be careful — if
  CI already started building, you may have a stuck partial
  release. Better to bump to the next patch.

## Gotchas

- **Pi-gen + qemu**: must use `tonistiigi/binfmt:latest` (qemu 9.x) for Python 3.13 to not segfault inside the chroot. Ubuntu's apt qemu is too old. See `.github/workflows/build-image.yml`.
- **Heartbeat hot-start**: pairing now starts the heartbeat service in-process via the API endpoint (`solar_monitor/api/cloud_admin.py`). Don't reintroduce "restart required for pairing" — that bit users earlier in development.
- **Source tarball channel is anonymous.** Don't add auth to `releases.wattpost.io` — the appliance Update-now flow relies on it being un-gated.
