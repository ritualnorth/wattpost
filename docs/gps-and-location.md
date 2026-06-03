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
