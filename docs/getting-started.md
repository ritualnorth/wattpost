# Quick start

WattPost polls off-grid solar gear over Bluetooth and serves a local
dashboard. Two install paths. Pick whichever fits.

| | SD-card image (Pi) | Docker container (Linux box) |
|---|---|---|
| Hardware | Raspberry Pi 4 / 5 | Any Linux host with BLE |
| Install | Flash + boot | `docker compose up -d` |
| Updates | "Update now" in Settings | `docker compose pull` |
| Best for | dedicated appliance | homelab / existing Linux box |

The dashboard, scanner, vendor drivers, alerts, cloud-pairing. All
identical between the two. The only differences are how you install
and how you update.

## Path A. SD card on a Raspberry Pi

What you need:

- A Raspberry Pi 4 or 5 (any RAM size. The 1 GB Pi 4 works fine).
- An 8 GB+ microSD card.
- A [Renogy BT-1 or BT-2 dongle](/docs/supported-hardware) plugged
  into your charge controller / battery.
- A USB-C power supply.

Then:

1. Install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
2. [Download the WattPost image](/download). About 600 MB.
3. In Imager: **Choose OS → Use custom** → select the `.img.xz`.
4. **Choose Storage** → pick the SD card.
5. Click **Next**, then **Edit Settings** (the OS-customisation gear) and set:
   - a **username + password** — this is your SSH / console login. WattPost
     ships *no default login or password*, so set your own here.
   - **Enable SSH** only if you actually want shell access (most people never
     do — the dashboard does everything). Key-based auth is the safest choice.
   - your **WiFi**, if the Pi isn't on Ethernet.
   Save, then hit **Write**. ~3–5 minutes.
6. Slot the SD card into the Pi, plug in your BT dongle, power on.
7. After ~60 seconds, open `http://wattpost.local` from any browser
   on the same network. If `.local` doesn't resolve, look up the
   Pi's IP and use that. Viewing the dashboard needs no login; the
   first time you open **Settings**, the appliance asks you to **create
   a dashboard password** (right there in the browser — no SSH needed).
   That password gates Settings from then on. The SSH/console
   username + password you set in Imager is separate.

**No router, no screen, no WiFi set?** A headless Pi on a boat or in a
van isn't stranded. If the Pi boots with **no network** — you didn't set
WiFi in Imager and there's no Ethernet — it raises its own WiFi access
point named **`WattPost-Setup`** within about a minute. Join it from your
phone and the setup page pops up automatically (a captive portal, like
hotel WiFi); if it doesn't, browse to `http://10.42.0.1`. From there you
set up your gear, or just keep using the AP if there's no other network
around. See [First boot](/docs/first-boot) and [WiFi hotspot](/docs/hotspot)
for the full behaviour.

## Path B. Docker on an existing Linux host

What you need:

- A Linux box with Docker + `docker compose` and a working Bluetooth
  adapter or USB BLE dongle (the host's `bluetoothctl list` should
  show at least one controller).
- A [Renogy BT-1 or BT-2 dongle](/docs/supported-hardware) plugged
  into your charge controller / battery.

Then follow [Run in Docker](/docs/docker-install). One compose file,
two volumes, one `docker compose up -d`. Open
`http://<this-host-ip>` once it's up.

Both paths land in the same place: the WattPost dashboard, ready to
pair gear.

## Path C. USB-RS485 (wired, no Bluetooth)

Skip the BT-2 dongle entirely and run a wire from the Pi to your
Renogy gear's RJ45 comms port. Bullet-proof on long runs, immune
to BLE interference, no pairing state to clear.

What you need:

- A USB-RS485 adapter. Recommended: the **Renogy "RS485-to-USB" cable** (pre-wired RJ45 → USB, ~£15-20) for zero-fuss setup, OR a **DSD TECH SH-U10** (FTDI FT232 chip) + Cat5e patch cable if you want a longer / cleaner run. Full shopping list at [Wired setup](/docs/wired-setup#cable-shopping-list-read-this-first).
- The Renogy comms port is RJ45 but **carries RS-485, not Ethernet**. It does not plug into the Pi's network jack.

Then:

1. Plug the adapter's USB end into the Pi. It enumerates as
   `/dev/ttyUSB0` (or `ttyUSB1` if you already have another USB-serial
   adapter attached, e.g. GPS).
2. Plug the RJ45 end into your Renogy device's comms port.
3. In the dashboard: **Settings → Setup → Add another connection →
   Wired (USB-RS485 adapter)**. The wizard scans `/dev/ttyUSB*` and
   shows what's attached + the chip type. Pick yours, save.
4. Hit **Scan for devices** on that connection. Same Renogy slave-ID
   sweep as the BT-2 path, just over the wire.

USB-RS485 and a BT-2 can run side by side on the same Pi. Useful if
you have multiple Renogy units in different cabinets / van bays.
See [Wired setup](/docs/wired-setup) for the full reference.

Note: this path is for **Renogy** today (the only vendor where USB-RS485 is
a real-world install). Victron and JK BMS broadcast over Bluetooth
directly. No dongle, no wire. So they don't need this option.

## Pair your first device

In the dashboard:

1. **Settings → Setup**
2. Confirm the **Bluetooth ready** banner at the top is green ·
   that's the daemon seeing your BLE adapter.
3. Click **Find my dongle** in step 1. The wizard scans for
   advertising BLE devices for ~8 seconds and lists them. Your
   Renogy BT-2 advertises as `BT-TH-XXXXXXXX`.
4. Click **Use this** on your dongle's row. The wizard writes the
   transport entry and asks you to restart the daemon (or container).
5. After restart, step 2 (**Scan for devices**) is unlocked. Hit
   **Scan**; the wizard probes the standard Renogy slave IDs over
   the BT-2 link. Identified devices appear with **Add** buttons.
6. Add each one. Restart the daemon once more. Live data starts
   flowing on the dashboard within ~10 s of the first poll.

No config files to hand-edit.

## What's next?

- [First boot](/docs/first-boot) — what happens in the first 30 seconds, the MOTD, and finding the dashboard
- [Pair an account](/docs/pairing) for the multi-site cloud dashboard
- [Set up alerts](/docs/alerts). Ntfy, Discord, Pushover, email, MQTT
- [Browse supported hardware](/docs/supported-hardware)
- [How updates work](/docs/updates)
