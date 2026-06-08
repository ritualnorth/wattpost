# SSH access & tinkering

The appliance is a real Raspberry Pi running standard Raspberry Pi OS. If you
want to get a shell and set things up yourself — extra fans, GPIO, a HAT, your
own packages — you can. This page covers how to get in safely and what's safe
to change.

## Getting SSH access

There's **no default login** on the image (no baked-in username or password —
required by the UK PSTI Act and EU Cyber Resilience Act). You set your own
credentials, so only you can get in.

1. **At flash time**, in Raspberry Pi Imager, open the OS-customisation
   settings (the gear icon) and set:
   - a **username**, and
   - an **SSH public key** (recommended) — paste the contents of your
     `~/.ssh/id_ed25519.pub`. You can set a password instead, but key-based
     auth is stronger and is what we'd suggest.
2. **Enable SSH** in the dashboard under **Settings → Network security**. That
   starts the SSH server and opens port 22 in the firewall. (SSH ships off, so
   the port stays closed until you turn it on.)
3. Connect:
   ```
   ssh <your-user>@wattpost.local      # or the appliance's IP
   ```

Key-based ("cert-based") SSH is therefore available out of the box — you don't
need to log in first to set it up; the key goes in at flash time. To add more
keys later, append them to `~/.ssh/authorized_keys` once you're in.

> Turning SSH **off** again in Settings stops the server and re-closes port 22.
> If you ever lock yourself out, attach a keyboard + monitor to the Pi.

## What's safe to change

WattPost itself lives under **`/opt/wattpost`** and is swapped wholesale on
every update — **don't edit anything in there**, your changes would be lost on
the next update.

Everything else is normal Raspberry Pi OS and **persists across WattPost
updates**: `apt` packages you install, files in `/etc`, your own systemd
services, `/boot/firmware/config.txt` tweaks, GPIO setups, and so on. Tinker
freely there.

### Example: a custom fan

Pi 5 boards with the official active cooler are handled automatically — the
image sets a temperature-based fan curve in `config.txt`, so the fan ramps with
SoC temperature without any setup.

If you've wired your own fan (e.g. to the GPIO header), you can drive it however
you like over SSH — a `gpio-fan` overlay in `config.txt`, a small systemd
service, etc. Those changes live outside `/opt/wattpost`, so they survive
updates.

## A note on security

- Leave SSH **off** unless you actually need it. The dashboard, updates, and
  cloud features all work without it.
- Prefer **key-based** auth over a password.
- The host firewall only exposes port 22 while SSH is enabled; everything else
  inbound stays closed. See [security.md](security.md) for the full model.
