# GPS & location

A van, a boat, or a towed cabin moves — and a fixed lat/lon gives it the
wrong weather and the wrong solar forecast. WattPost can read a USB GPS
receiver and keep its location current as you travel.

## USB GPS

Plug in a USB-CDC NMEA receiver (a VK-162 "G-Mouse" is the reference
unit) and add a `gps:` block to `config.yaml`:

```yaml
gps:
  port: /dev/ttyACM0     # the receiver's serial device
  baudrate: 9600
  min_move_km: 5         # how far to move before re-applying (default 5 km)
  refresh_after_s: 1800  # force a refresh at least this often (default 30 min)
```

The daemon reads NMEA RMC sentences, tracks the latest fix, and on a
**significant move** (more than `min_move_km` from the last applied fix,
or after `refresh_after_s` idle) updates the weather and PV-forecast
services' coordinates so a moving rig always gets the forecast for where
it actually is.

The fix is held in memory and applied live — it is **not** written back
to `config.yaml` on every move (that would thrash the disk in a moving
van). On restart, the lat/lon on disk is the fallback until the first new
fix arrives.

### Reboot-proof port path

`/dev/ttyACM0` can renumber if you add another serial/CDC device. Point
`port` at the stable **by-id** symlink instead — it never changes across
reboots or replugs:

```yaml
gps:
  port: /dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00
```

Get the exact name from `ls /dev/serial/by-id/`.

### Docker / VM passthrough

On Docker, pass the device into the container — add to the `wattpost`
service in `docker-compose.yml`:

```yaml
    devices:
      - "/dev/ttyACM0:/dev/ttyACM0"
```

On a Proxmox / KVM VM, pass the USB device through to the guest **by
Vendor/Device ID**, not by port — port-based passthrough breaks on a host
reboot. On Docker Desktop for **Mac/Windows**, USB passthrough isn't
supported at all — use a static `forecast.lat`/`lon` instead.

### "GPS stops working after a reboot" — ModemManager

The number-one cause of a GPS that *opens* but returns no data (logs:
*"multiple access on port / returned no data"*, with `/dev/ttyACM0`
intermittently vanishing) is **ModemManager** grabbing the port to probe
it as a cellular modem. The SD image and `install.sh` ship a udev rule
(`/etc/udev/rules.d/99-wattpost-gps.rules`) that tells ModemManager to
ignore common GPS / serial chips, so it's handled out of the box.

**On Docker the rule lives on the host, not the container** — if your GPS
keeps dropping, add it on the host:

```bash
sudo tee /etc/udev/rules.d/99-wattpost-gps.rules >/dev/null <<'EOF'
ATTRS{idVendor}=="1546", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
docker restart wattpost
```

If the host has no cellular modem, you can instead just
`sudo systemctl mask --now ModemManager`.

## How location feeds the rest of the appliance

The current best location drives:

- **Weather** ([Open-Meteo](/docs/weather)) — current conditions for
  your position.
- **PV forecast** ([Solcast](/docs/forecast)) — sun and production
  estimates geolocated to where you are.
- The appliance's own **"where am I" map tile** on the dashboard.

This local use is always available and never gated — showing you your
own location is never restricted.

## Privacy: sharing with the cloud

Coordinates **only leave the device if you explicitly opt in.** A
separate `location:` block controls cloud transmission, and it defaults
to **off**:

```yaml
location:
  share_with_cloud: off    # "off" | "approx" | "precise"
  approx_grid_km: 10       # snap-to-grid size for approx mode
```

- **off** (default) — the cloud receives no location at all.
- **approx** — the appliance rounds to a ~10 km grid *before* sending,
  so the cloud sees "in the Lake District", not your driveway. Good for
  fleet visibility without pinpoint tracking.
- **precise** — real lat/lon. Unlocks the precise cloud features: fleet
  map, **geofences**, and **anchor watch** (alert if the boat drifts off
  its mooring).

The toggle lives in **Settings → Location** and is authoritative on the
appliance side — this box only contributes location when *you* set this.

## API

| Method & path | Purpose |
| --- | --- |
| `GET   /api/gps` | GPS service state: latest fix, fix age, last applied lat/lon. `{ "configured": false }` when no `gps:` block is present. |
| `GET   /api/location/status` | Current best location (local view, never gated) + the share mode. |
| `PATCH /api/location/share` | Set `share_with_cloud` to `off` / `approx` / `precise`; applies without a restart. |
