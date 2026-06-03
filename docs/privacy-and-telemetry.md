# Privacy & telemetry

WattPost is local-first by design. Everything you see on your dashboard comes from your hardware over a wire on your network, nothing has to leave the box for the appliance to work. This page lists every connection it _does_ make to the outside world, what's in it, and how to switch each one off.

## What leaves the appliance

There are five outbound flows. The first three are mandatory (the dashboard can't function without them); the last two are optional and have UI toggles.

### 1. Update check, every 24 hours

The appliance fetches `https://wattpost.cloud/api/releases/latest` once a day to find out what the current shipping version is, so the dashboard can show an **Update available** badge. The request carries the appliance's User-Agent (`wattpost-appliance/<version>`); the response is identical for every appliance and is cached by our CDN.

Cannot be disabled. Disabling it would mean the appliance never knows when there's a security fix waiting.

### 2. Weather + solar forecast, every 15 minutes

The appliance queries [Open-Meteo](https://open-meteo.com/) directly with your configured latitude and longitude. Open-Meteo is a non-profit weather API; the request does not pass through WattPost servers. If you don't want to share your coordinates with them, leave the `weather:` block out of `config.yaml` and the appliance will skip the call (the dashboard's weather tile + PV forecast curve simply won't render).

### 3. CHANGELOG fetch, daily, alongside the update check

Same 24-hour cadence as #1, hitting `https://raw.githubusercontent.com/ritualnorth/wattpost/main/CHANGELOG.md`. Anonymous; same as fetching any static file. Used so the dashboard can preview what's in a not-yet-installed version's release notes.

### 4. Local-install beacon, *daily, anonymous, opt-in, default OFF*

When you fire the daily update check (#1), **and only if you have opted in**, the appliance also POSTs three things to `https://wattpost.cloud/api/local_installs/beacon`:

| Field | Example | Why we want it |
| --- | --- | --- |
| `install_id` | `5f9c…3a` (random UUID v4, generated on first boot, persisted to `/var/lib/wattpost/install-id`) | So we can count distinct installs instead of counting raw HTTP requests. |
| `version` | `0.1.29` | So we can see how fast a release lands across the fleet. |
| `install_method` | `pi` or `docker` | So we know which install paths are in real use. |

Cloudflare adds an `X-IP-Country` header on the way in (the two-letter ISO code, e.g. `GB`); the cloud reads it server-side, stores _only_ the two letters, and never persists the IP itself.

**We do not send and never have sent:** your email, your name, your MAC, your battery voltage, your SoC, your location coordinates, your hostname, your Wi-Fi SSID, your nearby Bluetooth devices, or any heartbeat data. The beacon body is exactly the three fields above.

**Off by default.** Nothing is sent unless you turn it on. If you'd like to help us see release adoption, enable it from **Settings → Privacy → Anonymous install ping**, or in `/etc/wattpost/config.yaml`:

```yaml
local_telemetry:
  enabled: true
```

The 24-hour update check fires either way (we need it to show the "Update available" badge); the `install_id` POST only happens when you've opted in.

### 5. Location sharing, *opt-in, default OFF, three modes*

If, and only if, you flip `location.share_with_cloud` to `approx` or `precise` (from Settings → Location or directly in `config.yaml`), the appliance ships coordinates in its heartbeat. **Default is `off`**, even a paired appliance with a working GPS will not transmit location until you flip this.

Three modes:

- **off** (default), cloud receives no location data at all. Your local dashboard still knows where you are; this gate is purely about transmission.
- **approx**, coordinates snapped to a ~10&nbsp;km grid on the appliance *before* transmission. The cloud literally never sees the precise number. Good for "show roughly where my fleet is" without precise tracking.
- **precise**, real lat/lon. Required for geofences, anchor-watch, and the moving-van trail.

The toggle is customer-side and authoritative: nothing on the cloud, admin, installer, or builder, can override it remotely.

### 6. Discovery telemetry, *opt-in, default OFF*

If, and only if, you flip `discovery.enabled: true` in `config.yaml`, the appliance forwards anonymised fingerprints of Bluetooth devices it scans but does not yet recognise. This feeds our driver-add pipeline. Even when on, we strip serial numbers, truncate the MAC to its vendor prefix (first 3 octets / OUI), and never associate the fingerprint with your account or install_id. Off by default precisely because we'd rather you opt in.

## What stays local, always

Just to be explicit about what we _don't_ do, because the privacy-conscious crowd asks: WattPost never sends any of the following anywhere unless you've paired the appliance to wattpost.cloud (which is a deliberate, billed action with a card-on-file gate):

- Battery voltage, current, SoC, temperature, cell voltages
- Solar PV power, MPPT load, charger state, schedule entries
- Configured device names, MAC addresses (full), passwords, API keys
- Local user accounts, sessions, login attempts
- Your network's other devices

If you pair (the SaaS tier), all of the above flow to the cloud as part of the heartbeat, that's the whole point of pairing, and you can see exactly what's in each heartbeat at any time via the appliance's diagnostics endpoint or `journalctl -u wattpost`.

## How to verify yourself

- `journalctl -u wattpost | grep -E "manifest|beacon|open-meteo"`, every outbound call we log shows up here.
- `tcpdump -i <iface> host wattpost.cloud or host github.com or host raw.githubusercontent.com or host objects.githubusercontent.com or host api.open-meteo.com`, captures the production hostnames the appliance talks to (version-check + heartbeat → wattpost.cloud; source + changelog → GitHub; weather → Open-Meteo).
- The source of every outbound call is open: `solar_monitor/update/checker.py` (update + beacon), `solar_monitor/weather/service.py` (Open-Meteo), `solar_monitor/cloud/service.py` (heartbeat, paired only), `solar_monitor/discovery/service.py` (BLE fingerprint, opt-in only).

## Changelog

- **2026-05-20**, Anonymous local-install beacon added (#217), default ON, opt-out via `local_telemetry.enabled: false`. Three-field payload + Cloudflare country header; no IP, no PII.
