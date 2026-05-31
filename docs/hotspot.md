# WiFi hotspot (appliance-as-AP)

WattPost can turn the appliance's own WiFi radio into an **access
point**, so a phone or laptop can reach the dashboard when there's no
other network around. This is the field-setup and off-grid story: plug
the box in, join the `WattPost-Setup` network, open the page.

> **Status:** scaffold + manual control. Auto-handoff (automatically
> falling back to the hotspot when no known WiFi is in range) and a
> captive portal that pops the dashboard automatically are **Phase 3b**,
> not yet shipped. Today you turn the hotspot on/off yourself.

## What it does

When enabled, the appliance runs a 2.4 GHz access point named after
your chosen SSID. Clients that join get an IP from the appliance's
built-in DHCP server and can reach the dashboard at:

```
http://10.42.0.1
```

The hotspot is driven through **NetworkManager** (`nmcli`), the default
network stack on Raspberry Pi OS Bookworm. There's nothing to install
or hand-configure — no `hostapd`, no `dnsmasq`. NetworkManager provides
the AP, the DHCP server and the NAT.

It is **off by default** and strictly opt-in.

## Requirements

- **NetworkManager on the host** (`nmcli` on `PATH`). This is the
  default on Pi OS Bookworm. On older images that still use
  `dhcpcd`/`wpa_supplicant`, switch to NetworkManager first
  (`sudo raspi-config` → Advanced → Network Config → NetworkManager).
- **A WiFi radio that can run in AP mode.** The Pi's built-in adapter
  can. If the same radio is also your only uplink to the internet,
  bringing the AP up will take that uplink down — use Ethernet for the
  uplink, or a second USB WiFi adapter, if you need both at once.
- For Docker installs, the container needs host networking and access
  to D-Bus/NetworkManager. The packaged appliance image already has
  this; a hand-rolled `docker run` on a generic host generally will
  not.

## Turning it on

**From the dashboard:** Settings → **WiFi hotspot**. Set the network
name and (optionally) a password, then **Save**. Use **Turn on now** /
**Turn off** for manual control, and tick **Start on boot** to bring it
up automatically every time the appliance starts.

- **Password** must be 8–63 characters (WPA2). Leave it blank for an
  **open** network. When editing later, a blank password field means
  "keep the current one" — it is never echoed back to the browser.

**From config.yaml:**

```yaml
hotspot:
  enabled: true            # bring the AP up on boot (default false)
  ssid: WattPost-Setup
  password: "changeme123"  # 8..63 chars, or "" for an open network
  band: bg                 # "bg" = 2.4 GHz (default), "a" = 5 GHz
  channel: 6
  interface: wlan0
```

Changes made in the UI are written back to `config.yaml` and applied
live — no restart needed.

## API

All endpoints sit behind the same local auth as the rest of the
dashboard.

| Method & path             | Purpose                                  |
| ------------------------- | ---------------------------------------- |
| `GET  /api/hotspot/status`| Live AP state: active, SSID, gateway, client count, last error |
| `PUT  /api/hotspot/config`| Set/update the `hotspot:` block (partial updates OK; omit `password` to keep it) |
| `POST /api/hotspot/on`    | Bring the AP up now (ignores the `enabled` flag) |
| `POST /api/hotspot/off`   | Bring the AP down now                    |

`POST /on` / `/off` return `409` until a `hotspot:` block exists — save
the config first (the UI does this for you).

## Behaviour notes

- **The AP survives a daemon restart.** It lives in NetworkManager, not
  inside the WattPost process, so restarting or upgrading the daemon
  won't drop a client who is connected over the hotspot. Turning it off
  is always an explicit action.
- **Failures are non-fatal.** If `nmcli` is missing or the radio can't
  run an AP, the hotspot simply reports unavailable in Settings; the
  local dashboard and polling keep working normally.

## Troubleshooting

- **Settings shows "Unavailable — NetworkManager not found":** the host
  isn't running NetworkManager. See *Requirements* above.
- **AP turns on but you lose internet:** the appliance only has one
  WiFi radio and it's now serving the AP instead of connecting out. Use
  Ethernet for the uplink, or add a second WiFi adapter.
- **Can't reach `http://10.42.0.1`:** confirm you joined the right SSID
  and your phone didn't silently drop back to mobile data because the
  hotspot has no internet.

## Roadmap (Phase 3b)

- **Auto-handoff** — bring the AP up automatically when no known WiFi
  is in range, and drop it again once a known network reappears.
- **Captive portal** — DNS redirect so joining the network pops the
  dashboard automatically, like a hotel WiFi splash page.
