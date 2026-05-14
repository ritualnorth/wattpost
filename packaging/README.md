# Packaging

Two install paths:

## 1. Hobbyist install on existing Pi OS Lite

For users who already have Raspberry Pi OS Lite (or any Debian-derived
system with systemd + BlueZ + Python 3.11+) running:

```bash
sudo ./packaging/install.sh
```

What it does:
- Creates a `wattpost` system user (in the `bluetooth` group for BlueZ access).
- Drops the daemon into `/opt/wattpost/venv` via `pip install`.
- Seeds `/etc/wattpost/config.yaml` from `config.example.yaml` (only if
  not already present — re-runs upgrade in place without clobbering
  user edits).
- Installs `/etc/systemd/system/wattpost.service` with `Restart=on-failure`
  and a hardened sandbox (`NoNewPrivileges`, `ProtectSystem=strict`,
  read-write only to `/etc/wattpost` and `/var/lib/wattpost`).
- Enables + starts the service.

After it finishes:
- Dashboard: `http://<pi>:8000/`
- Live logs: `journalctl -u wattpost -f` (or via the dashboard's
  Settings → Diagnostics block).
- Apply config changes: tap **Restart daemon** in Settings → System
  (does `os.execv` after closing BLE cleanly), or `sudo systemctl
  restart wattpost`.

Idempotent — re-run to upgrade. Doesn't touch the config or the
SQLite database.

## 2. Pre-baked SD image (pi-gen)

For the non-technical buyer: a `.img.xz` file you flash with
Raspberry Pi Imager and boot. First boot starts WattPost
automatically.

```bash
./packaging/build-image.sh
# ~1–2 hours; result at build/pi-gen/deploy/wattpost-*-lite.img.xz
```

What it does:
1. Clones [`pi-gen`](https://github.com/RPi-Distro/pi-gen) on the arm64
   branch into `build/pi-gen/` (if not already there).
2. Symlinks `packaging/pi-gen/stage-wattpost` into pi-gen's stage list.
3. Writes a `config` file selecting stages 0–2 (lite) + our stage.
4. Runs `sudo ./build.sh` inside the pi-gen checkout. The stage:
   - rsyncs the repo into `/tmp/wattpost-src` in the image chroot
   - apt-installs `python3`, `python3-venv`, `python3-pip`, `bluez`
   - runs the same `install.sh` the hobbyist path uses
   - enables `wattpost.service` for first boot

Build host: needs `git quilt parted qemu-user-static debootstrap
zerofree zip dosfstools libcap2-bin rsync xz-utils kmod pigz` and the
ability to `sudo`. Works on x86_64 Ubuntu/Debian; arm64 native is
faster (no qemu emulation).

Default credentials in the image:
- SSH user: `wattpost` / `wattpost` — **change on first boot**.
- Hostname: `wattpost.local` (mDNS).

## What's in the SD image (and the bare-metal install)

Both install paths converge on `install.sh` as the single source of
truth, so what shows up in the chroot during pi-gen build also shows
up on a manually-installed Pi.

| Component               | Source                      | Why it's there |
| ----------------------- | --------------------------- | -------------- |
| `python3`, `python3-venv`, `python3-pip` | apt: pi-gen's `00-packages` | Daemon runtime |
| `bluez`, `bluez-tools`, `bluetoothctl`   | apt: pi-gen's `00-packages` | BLE comms with Renogy / Victron / JK-BMS dongles + the BlueZ-disconnect fallback in `transport/ble_modbus.py` |
| `ca-certificates`       | apt: pi-gen's `00-packages` | TLS verification on every outbound HTTPS call (Solcast, Open-Meteo, cloud) |
| `curl`, `gnupg`, `lsb-release` | apt: pi-gen's `00-packages` | Required by `install.sh` to add Cloudflare's signed apt repo |
| `cloudflared`           | apt: `install.sh` adds Cloudflare's signed repo, then `apt install cloudflared` | Cloud tunnel that exposes the local dashboard at `<slug>.wattpost.io` once the appliance is paired and the cloud has provisioned a tunnel |
| `wattpost` daemon       | pip: `install.sh` does `pip install -e ${WATTPOST_SOURCE}` into `/opt/wattpost/venv` | The appliance itself |
| `wattpost.service`      | `install.sh` drops a unit at `/etc/systemd/system/wattpost.service` | systemd lifecycle |
| `/etc/wattpost/config.yaml` | Seeded from `config.example.yaml` (only on fresh install — re-runs preserve) | User config |
| `/etc/sudoers.d/wattpost-tailscale` | `install.sh` | Lets the daemon call `tailscale up/logout/serve` without a password — only those three subcommands |
| `wattpost` system user (in the `bluetooth` group) | `install.sh` | Hardened systemd unit runs as this user, not root |

### Deferred — *not* yet in the image (manual install per-customer):

- **Tailscale** — required for Settings → Network → Connect to do
  anything. Most users won't use it (cloud tunnel via wattpost.io
  is the canonical remote-access path). When a user does want it,
  `curl -fsSL https://tailscale.com/install.sh | sh` from a serial
  console is the documented step.

### Re-deriving this list

The full requirements set is the union of:
- `packaging/pi-gen/stage-wattpost/01-install/00-packages` (apt)
- `packaging/install.sh` (Cloudflare apt repo + cloudflared)
- `pyproject.toml`'s `[project] dependencies` block (pip)

If any new daemon-side capability needs a system package or a CLI
tool, it goes in those three places.

## Files

```
packaging/
├── install.sh                  # bare-metal installer (idempotent)
├── build-image.sh              # pi-gen driver — produces .img.xz
├── systemd/
│   └── wattpost.service        # the unit (sandboxed)
└── pi-gen/
    └── stage-wattpost/
        ├── prerun.sh
        ├── 00-copy-source/00-run.sh     # rsync repo → chroot /tmp
        └── 01-install/
            ├── 00-packages              # apt-deps
            └── 00-run-chroot.sh         # runs install.sh inside chroot
```
