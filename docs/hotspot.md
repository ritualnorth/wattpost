# WiFi hotspot (appliance-as-AP)

WattPost can turn the appliance's own WiFi radio into an **access
point**, so a phone or laptop can reach the dashboard when there's no
other network around. This is the field-setup and off-grid story: plug
the box in, join the `WattPost-Setup` network, open the page.

> **Status:** manual control, auto-handoff **and the captive portal** are
> all shipped. Drive the hotspot by hand, let it raise itself whenever
> there's no other network (*Auto-handoff*), and have a joining device
> pop the dashboard automatically (*Captive portal*) — all below.

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
  auto_handoff: false      # auto-enable when offline (see below)
  captive_portal: false    # auto-pop the dashboard on join (see below)
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

## Auto-handoff

With **Auto-enable when offline** (`auto_handoff: true`, or the checkbox
in Settings → WiFi hotspot), the appliance manages the AP for you: it
brings the hotspot up whenever it has no other network, and drops it
again when a real LAN returns. This is the off-grid / vanlife path —
park anywhere, and the dashboard is always reachable at
`http://10.42.0.1` without you touching anything.

**It needs no cloud account.** The `auto_handoff` flag lives in the
appliance's own config and works on a fully offline, unpaired box —
which is the whole point, since the off-grid user who needs this most is
the least likely to be paying for the cloud.

How it decides:

- Every ~30s it checks for a non-AP network. A **wired** connection (or
  a second WiFi adapter acting as a client) is detected immediately and
  cleanly — the AP drops the moment real connectivity returns.
- A short grace window debounces transient blips (e.g. a WiFi roam)
  before the AP is raised, so it doesn't flap.

**Single-radio caveat.** Most Pis have one WiFi radio, so while the AP
is up that radio *can't also* be scanning for known networks. To recover,
the appliance periodically (~every 5 min) drops the AP for a few seconds
to let NetworkManager try to rejoin a known network; if none is in range
the AP comes straight back. So on a one-radio box, expect a brief
hotspot blip every few minutes while you're off-grid. Use **Ethernet for
the uplink** (or add a second USB WiFi adapter) for seamless, blip-free
handoff. `auto_handoff` is ignored when `enabled: true`, since the AP is
already always on.

## Captive portal

With **Captive portal** on (`captive_portal: true`, or the checkbox in
Settings → WiFi hotspot), joining the hotspot **pops the dashboard
automatically** on the phone or laptop — the "Sign in to network" sheet
every OS shows for hotel/airport WiFi — so nobody has to know or type
`http://10.42.0.1`.

How it works: while a captive AP is up, the appliance adds a NetworkManager
dnsmasq drop-in that resolves *every* hostname to itself. A joining
device's OS fires its usual connectivity check (Apple hits
`captive.apple.com`, Android `generate_204`, Windows `connecttest.txt`);
those land on the appliance, which answers with a redirect to the
dashboard instead of the "you're online" reply — and the OS opens its
captive sheet.

Notes:

- **Needs DNS write access.** The appliance must be able to manage NM's
  `dnsmasq-shared.d` drop-in. The packaged Pi image grants the `wattpost`
  user exactly this (and nothing more). On a host where it can't write
  there (e.g. a hand-rolled Docker setup), the portal simply doesn't arm
  — the AP still works and clients reach the dashboard at the gateway IP
  by hand. The hotspot is never affected either way.
- The drop-in is present **only while a captive AP is up**; it's removed
  the moment the AP comes down, so it never interferes with normal
  networking.
- Pairs naturally with **auto-handoff**: off-grid, the AP raises itself
  and a joining phone is taken straight to the dashboard.
