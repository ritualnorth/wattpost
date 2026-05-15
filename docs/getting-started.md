# Quick start

WattPost polls off-grid solar gear over Bluetooth and serves a local
dashboard. Two install paths — pick whichever fits.

| | SD-card image (Pi) | Docker container (Linux box) |
|---|---|---|
| Hardware | Raspberry Pi 4 / 5 | Any Linux host with BLE |
| Install | Flash + boot | `docker compose up -d` |
| Updates | "Update now" in Settings | `docker compose pull` |
| Best for | dedicated appliance | homelab / existing Linux box |

The dashboard, scanner, vendor drivers, alerts, cloud-pairing — all
identical between the two. The only differences are how you install
and how you update.

## Path A — SD card on a Raspberry Pi

What you need:

- A Raspberry Pi 4 or 5 (any RAM size — the 1 GB Pi 4 works fine).
- An 8 GB+ microSD card.
- A [Renogy BT-1 or BT-2 dongle](/docs/supported-hardware) plugged
  into your charge controller / battery.
- A USB-C power supply.

Then:

1. Install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
2. [Download the WattPost image](/download) — about 600 MB.
3. In Imager: **Choose OS → Use custom** → select the `.img.xz`.
4. **Choose Storage** → pick the SD card.
5. Hit **Write**. ~3–5 minutes.
6. Slot the SD card into the Pi, plug in your BT dongle, power on.
7. After ~60 seconds, open `http://wattpost.local` from any browser
   on the same network. If `.local` doesn't resolve, look up the
   Pi's IP and use that.

## Path B — Docker on an existing Linux host

What you need:

- A Linux box with Docker + `docker compose` and a working Bluetooth
  adapter or USB BLE dongle (the host's `bluetoothctl list` should
  show at least one controller).
- A [Renogy BT-1 or BT-2 dongle](/docs/supported-hardware) plugged
  into your charge controller / battery.

Then follow [Run in Docker](/docs/docker-install) — one compose file,
two volumes, one `docker compose up -d`. Open
`http://<this-host-ip>:8000` once it's up.

Both paths land in the same place: the WattPost dashboard, ready to
pair gear.

## Pair your first device

In the dashboard:

1. **Settings → Setup**
2. Confirm the **Bluetooth ready** banner at the top is green —
   that's the daemon seeing your BLE adapter.
3. Click **Find my dongle** in step 1. The wizard scans for
   advertising BLE devices for ~8 seconds and lists them. Your
   Renogy BT-2 advertises as `BT-TH-XXXXXXXX`.
4. Click **Use this** on your dongle's row. The wizard writes the
   transport entry and asks you to restart the daemon (or container).
5. After restart, step 2 (**Scan for devices**) is unlocked. Hit
   **Scan**; the wizard probes the standard Renogy slave IDs over
   the BT-2 link. Identified devices appear with **Add** buttons.
6. Add each one. Restart the daemon once more — live data starts
   flowing on the dashboard within ~10 s of the first poll.

No config files to hand-edit.

## What's next?

- [Pair an account](/docs/pairing) for the multi-site cloud dashboard
- [Set up alerts](/docs/alerts) — ntfy, Discord, Pushover, email, MQTT
- [Browse supported hardware](/docs/supported-hardware)
- [How updates work](/docs/updates)
