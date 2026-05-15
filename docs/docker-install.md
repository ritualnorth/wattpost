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
    # Host networking is the simplest BLE-passthrough path on Linux.
    # Trade-off: the container is on the host network, so :8000 binds
    # the host directly.
    network_mode: host
    # Bluetooth via the host's bluetoothd over DBus. Least-privilege
    # path; fall back to `privileged: true` if your distro's bluez
    # is patchy with the cap_add combo below.
    volumes:
      - /var/run/dbus:/var/run/dbus
      - ./wattpost-config:/etc/wattpost
      - ./wattpost-data:/var/lib/wattpost
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    restart: unless-stopped
```

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

## Differences from the SD-card install

The Docker image:

- **Doesn't** carry `cloudflared` — the cloud-tunnel feature is
  Pi-only (the binary doesn't have a clean Linux multi-distro story
  yet). If you want remote access on Docker, use Tailscale on the
  host directly.
- **Doesn't** ship the `wattpost-config` whiptail TUI. Configure via
  the dashboard's Settings UI or by editing the volume's
  `config.yaml`.
- **Doesn't** auto-update via the daemon's "Update now" button —
  that's image-replacement (`docker compose pull`) instead.
