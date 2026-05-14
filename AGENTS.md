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
| Cut a new SD-image release            | Bump `__version__` + `releases.LATEST`, `git tag v0.0.X`, push. ~90 min |
| Ship a feature to paired appliances   | Just push to `main`. `publish-source.yml` does the rest.              |
| Debug a failed pi-gen run             | `gh run view <id> --log-failed`. Common failure modes documented in release-pipeline.md operator runbook |
| Edit production Caddy                 | Edit `vps-infra/caddy/Caddyfile`, push to vps-infra main, SSH in + `docker compose up -d shared-caddy` |
| Smoke-test the cloud locally          | `cd cloud && docker compose up -d`; visit `http://localhost:8080`     |

## Gotchas

- **Pi-gen + qemu**: must use `tonistiigi/binfmt:latest` (qemu 9.x) for Python 3.13 to not segfault inside the chroot. Ubuntu's apt qemu is too old. See `.github/workflows/build-image.yml`.
- **Caddy + Cloudflare**: zone SSL must be **Full** (not Strict). Caddy uses `tls internal` (self-signed) on every proxied site. Strict would 525-error everything. Banner in `vps-infra/caddy/Caddyfile` explains.
- **Heartbeat hot-start**: pairing now starts the heartbeat service in-process via the API endpoint (`solar_monitor/api/cloud_admin.py`). Don't reintroduce "restart required for pairing" — that bit users earlier in development.
- **Source tarball channel is anonymous.** Don't add auth to `releases.wattpost.io` — the appliance Update-now flow relies on it being un-gated, and the contents are already shipped by definition.
