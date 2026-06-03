# Local web password

The WattPost local dashboard (the page at `http://wattpost.local`) is **unauthenticated by default**. Anyone on your LAN can view + change config. Same trust model as Pi-hole, Home Assistant Yellow, Solar Assistant. Most off-grid users have a single trusted network, and the cloud tunnel is the strong-auth surface for remote access.

## When to set a local password

You want one if:

- Your LAN has guests you don't fully trust
- You're on a shared / corporate / multi-tenant network
- A nervous family member keeps hitting "Restart daemon" by accident

You probably don't need one if:

- It's your own home LAN
- You access the dashboard primarily through the cloud tunnel (`<slug>.wattpost.io`), which has its own auth

## Setting one

On the Pi's console — or over SSH, if you enabled it — run `wattpost-config` → **Set / reset web password**. A random `wattpost-<5-hex>` password is generated, hashed (argon2), stored at `/etc/wattpost/web-password.hash`. The plaintext is also written to `/etc/wattpost/web-password` so the MOTD shows it on next login.

```
$ wattpost-config
[ select option 6: Set / reset web password ]
# Confirm
# New password: wattpost-a3f9c1
```

Copy the new password into your password manager. The plaintext file gets shown on every SSH login until you delete it.

## Resetting

Same menu, same option. Generates a fresh random password and signs all existing browser sessions out.

## Removing (back to no password)

`sudo rm /etc/wattpost/web-password.hash /etc/wattpost/web-password` and restart the wattpost service. The middleware auto-detects the missing hash and stops enforcing. We'll add this to the menu in a future release.

## What's gated, what's open

| Surface | Behaviour when password is set |
| - | - |
| `/kiosk` | Always anonymous (wall-display URL never asks for login) |
| `GET /api/devices/*`, `GET /api/today` etc | Read-only-public mode (default): anonymous OK. Strict mode: requires login. |
| `POST /api/setup/*`, `POST /api/system/restart` etc | Login required |
| Cloud tunnel access | Bypasses local auth. The cloud already authed you |
| `/api/heartbeat` | Bearer token (the appliance → cloud flow, unchanged) |

## The cloud-tunnel bypass

Visits arriving through `<slug>.wattpost.io` come from `cloudflared` on the local appliance, which proxies to `localhost:80`. That means the source IP is `127.0.0.1`. Kernel-decided, **can't be spoofed** by a LAN client. The middleware trusts loopback. So clicking **Open Site** in `wattpost.cloud` works without prompting for the local password.

## Lost the password?

### Pi (SD image)

On the Pi's console, or over SSH if you enabled it, log in with the username/password you set in Raspberry Pi Imager (the OS login is separate from the web password, and WattPost ships no default for it). Run `wattpost-config` → Set / reset web password → it generates a new one.

Worst case (SSH locked out + dashboard locked out): re-flash the SD image, restore your `config.yaml` backup, you're back. The cloud-side data is preserved because the appliance is identified by its bearer token in `config.yaml`.

### Docker

You usually don't need to reset it — the plaintext is kept on the box for exactly this. The config volume is `./wattpost-config` on the host ↔ `/etc/wattpost` in the container, and the password lives at `web-password` there. **Read it back** with any of:

```bash
docker exec wattpost cat /etc/wattpost/web-password   # from the container
cat ./wattpost-config/web-password                    # the same file on the host
docker compose logs wattpost | grep -i password       # it's logged when first generated
```

To force a **fresh** password (e.g. the plaintext file is gone), delete the hash + plaintext and restart — the daemon regenerates a random one on boot, writes both files, and logs it:

```bash
rm -f ./wattpost-config/web-password ./wattpost-config/web-password.hash
docker compose restart wattpost
docker exec wattpost cat /etc/wattpost/web-password   # the new password
```

Restarting also signs out any existing browser sessions. (Container/service name is `wattpost` in the example compose file — adjust if you renamed it.)
