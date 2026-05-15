# Orientation for AI agents

This is **WattPost** — an off-grid solar monitoring product. Two halves:

- `solar_monitor/` — the appliance daemon. Runs on a Pi, polls Renogy/Victron/JK BMS over Bluetooth, serves a local dashboard.
- `cloud/` — the SaaS at `app.wattpost.io`. Multi-site dashboard, heartbeat ingest, Cloudflare tunnel provisioning, paired appliance management.

Read in this order if you're new:

1. **[README.md](README.md)** — what the product is, top-level layout, how to install
2. **[docs/architecture.md](docs/architecture.md)** — appliance internals (transports, scheduler, storage, vendor drivers)
3. **[docs/cloud-architecture.md](docs/cloud-architecture.md)** — cloud / appliance split, tunnel pattern
4. **[docs/release-pipeline.md](docs/release-pipeline.md)** — how features reach customers (SD images + tarballs + Update-now)
5. **[docs/adding-a-vendor.md](docs/adding-a-vendor.md)** — add a new battery/charger driver

## Repo conventions

- **Private repo.** The appliance binary distribution is via `releases.wattpost.io` (self-hosted on the Contabo VPS, see `docs/release-pipeline.md`).
- **Cloud auto-deploys.** Every push to `main` that touches `cloud/` triggers `build-cloud-image.yml` → GHCR → SSH-deploy to the VPS. Don't commit broken cloud code to main.
- **Appliance + cloud share this repo.** Code under `solar_monitor/` ships to customer Pis; code under `cloud/` stays on our infrastructure. Don't mix imports across that boundary.
- **Sister repo: `vps-infra`** at `/home/user/code/vps-infra` on the dev box. Holds `docker-compose.yml`, Caddyfile, bootstrap scripts for the VPS. Has its own CLAUDE.md.

## Common ops

| Task                                  | Where                                                                 |
| ------------------------------------- | --------------------------------------------------------------------- |
| **Cut a real release** (see below)    | Bump `__version__` + CHANGELOG, commit, `git tag vX.Y.Z`, push tag.   |
| Ship a feature to bleeding-edge testers | Just push to `main`. Builds `:edge` Docker tag, no manifest bump.    |
| Debug a failed pi-gen run             | `gh run view <id> --log-failed`. Common failure modes documented in release-pipeline.md operator runbook |
| Edit production Caddy                 | Edit `vps-infra/caddy/Caddyfile`, push to vps-infra main, SSH in + `docker compose up -d shared-caddy` |
| Smoke-test the cloud locally          | `cd cloud && docker compose up -d`; visit `http://localhost:8080`     |

## Cutting a release (the ritual)

Ritual North (the user) doesn't cut releases — I do, when he asks. The
shape:

```bash
# 1. Bump version. Two characters change.
sed -i 's/__version__ = "0.0.3"/__version__ = "0.0.4"/' solar_monitor/__init__.py

# 2. Move the [Unreleased] block in CHANGELOG.md to a new
#    [0.0.4] — YYYY-MM-DD section. Leave [Unreleased] empty
#    above it for the next batch. Today's date is in
#    `currentDate` context.

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
- If Ritual North says "cut a release" or "ship this" — do it.

### Things that often go wrong

- **Forgetting to bump `__version__`**: tag fires, but
  `__version__` inside the shipped code is wrong. Always bump
  in the same commit as the tag intent. If you forget: rebase,
  fix, re-tag.
- **CHANGELOG entry missing**: less catastrophic, but customers
  won't know what changed. Always populate it from the commit
  log since the previous tag (`git log v0.0.3..HEAD --oneline`
  is your friend).
- **Tag already exists**: `git tag -d v0.0.4 && git push origin :v0.0.4`
  to delete locally and remotely, then re-tag. Be careful — if
  CI already started building, you may have a stuck partial
  release. Better to bump to the next patch.

## Gotchas

- **Pi-gen + qemu**: must use `tonistiigi/binfmt:latest` (qemu 9.x) for Python 3.13 to not segfault inside the chroot. Ubuntu's apt qemu is too old. See `.github/workflows/build-image.yml`.
- **Caddy + Cloudflare**: zone SSL must be **Full** (not Strict). Caddy uses `tls internal` (self-signed) on every proxied site. Strict would 525-error everything. Banner in `vps-infra/caddy/Caddyfile` explains.
- **Heartbeat hot-start**: pairing now starts the heartbeat service in-process via the API endpoint (`solar_monitor/api/cloud_admin.py`). Don't reintroduce "restart required for pairing" — that bit users earlier in development.
- **Source tarball channel is anonymous.** Don't add auth to `releases.wattpost.io` — the appliance Update-now flow relies on it being un-gated, and the contents are already shipped by definition.
