# Docker install

A second way to run the WattPost appliance, alongside the SD-card
image. Same daemon, same dashboard, different distribution path.

| | SD image | Docker |
|---|---|---|
| Hardware | Raspberry Pi 4 / 5 | any Linux host with BLE |
| Install | Flash + boot | `docker compose up -d` |
| Updates | "Update now" button | `docker compose pull && up -d` |
| Bluetooth | built-in / BT-2 dongle | host BLE (Linux only) |
| Best for | dedicated appliance | homelab / existing Linux box |

macOS, Windows, and WSL2 hosts **cannot** pass host Bluetooth into a
container. For those, use the SD-card path.

## Requirements

- A Linux host with Docker + `docker compose`
- Working Bluetooth on the host (`bluetoothctl list` shows at least
  one adapter)
- A Renogy BT-1 or BT-2 dongle on your charge controller / battery
- ~200 MB disk for the image + a few MB/day for history

## Install

```bash
mkdir -p ~/wattpost && cd ~/wattpost
```

Save the following as `~/wattpost/docker-compose.yml`:

```yaml
services:
  wattpost:
    image: ghcr.io/ritualnorth/wattpost-appliance:latest
    container_name: wattpost
    network_mode: host
    volumes:
      - /run/dbus:/var/run/dbus
      - ./wattpost-config:/etc/wattpost
      - ./wattpost-data:/var/lib/wattpost
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined
    restart: unless-stopped
```

That combo is the least-privilege path that reliably reaches the host's BlueZ on Ubuntu / Debian. If your distro is unusual and BLE still doesn't show up under **Settings → Setup**, replace `cap_add`/`security_opt` with `privileged: true` — same result, bigger hammer.

Then:

```bash
docker compose up -d
```

Open `http://<this-host-ip>:8000` in a browser on the same network.
First-boot drops a minimal `config.yaml` in `./wattpost-config/` —
edit via **Settings → Devices & setup** in the dashboard, no SSH or
file-editing needed.

## Bluetooth passthrough

The example compose uses `network_mode: host` and bind-mounts
`/var/run/dbus`. That's the most reliable combo across distros —
the container shares the host's network namespace and talks to the
host's `bluetoothd` over the DBus socket. Trade-off: the container
is on the host network, so `:8000` binds the host directly.

If your distro's BlueZ is patchy with that combo, fall back to
`privileged: true` on the service. It's a bigger hammer; we'd rather
ship the least-privilege path.

## Updates

```bash
cd ~/wattpost
docker compose pull   # fetch newest image
docker compose up -d  # roll the container
```

The `latest` tag follows `main`. For traceability, pin to a
`sha-<short>` tag in your compose file:

```yaml
image: ghcr.io/ritualnorth/wattpost-appliance:sha-abc1234
```

## What's mounted

- `./wattpost-config:/etc/wattpost` — `config.yaml`, sudoers etc.
  Survives `docker compose pull && up -d`.
- `./wattpost-data:/var/lib/wattpost` — SQLite database (history,
  rollups). Back this up.

> ⚠️ **You MUST mount both directories**, either as bind mounts (the
> example uses `./wattpost-config` + `./wattpost-data` in your
> compose project folder) or as named Docker volumes. If you skip
> them, Docker gives the container an **anonymous volume** that is
> created fresh every time the container is recreated — your history
> and config will silently reset to zero on the next `docker compose
> pull && up -d`. The `docker-compose.example.yml` we publish always
> mounts them; don't strip those lines out.

## Updates retain your data

When a new release ships:

```bash
docker compose pull         # fetches the new image
docker compose up -d        # recreates the container with the new image
```

The container is replaced, but `./wattpost-config` and
`./wattpost-data` are host directories — they're untouched.

- **Config (`config.yaml`)** stays exactly as you left it. Devices,
  transports, alerts, integrations — all preserved.
- **History (SQLite DB)** is preserved. Raw samples, 1-minute /
  1-hour / 1-day rollups, the cached PV forecast — all there.
- **Schema migrations**, when we ship them, run automatically on
  the first boot of the new image (`PRAGMA user_version` tracks
  what's been applied — the daemon logs every migration at INFO so
  you can audit it). Migrations are forward-only and have to be
  additive or in-place; nothing destructive happens to your data.

## Backups

For belt-and-braces, a periodic copy of `./wattpost-data` is the
whole backup — it's a single SQLite file. Restore is "stop the
container, replace the file, start the container."

```bash
# nightly cron, on the host
sqlite3 ./wattpost-data/solar-monitor.db ".backup '/path/to/backup-$(date +%F).db'"
```

SQLite's online backup API is hot — no need to stop the daemon
while it runs.

## Differences from the SD-card install

The Docker image:

- **Carries `cloudflared`** in the image, so the paired-cloud
  feature's "Open site" tunnel works identically to the Pi
  install — your appliance's local dashboard is reachable at
  `<your-slug>.wattpost.io` over the Cloudflare Tunnel once
  pairing is complete.
- **Doesn't** ship the `wattpost-config` whiptail TUI. Configure via
  the dashboard's Settings UI or by editing the volume's
  `config.yaml`.
- **Doesn't** auto-update via the daemon's "Update now" button —
  that's image-replacement (`docker compose pull`) instead.
- **Doesn't** include the in-app Tailscale toggle. Tailscale in
  a container is fiddly (needs `/dev/net/tun`, custom caps, a
  sidecar pattern) — we don't recommend it. Two paths for
  remote access:
  1. **Pair the appliance to [WattPost Cloud](pairing.md)** —
     handles tunnels, multi-site dashboards, and remote
     management. The path we maintain end-to-end.
  2. **Install Tailscale on the host** directly (not in the
     container). Once it's on your tailnet, you reach the
     appliance dashboard at `http://<host-name>:8000/` from
     anywhere on your tailnet. No app changes needed; the
     daemon still binds `0.0.0.0:8000` like normal.

  Pi installs keep the in-app Tailscale UI — sudoers + `tailscale
  serve` are pre-wired on the SD image because that environment
  is consistent across customers. Containers vary too much for
  us to support automating it.
