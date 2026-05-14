# Quick start

WattPost is a Raspberry Pi appliance that polls your off-grid solar gear over Bluetooth and serves a live dashboard. Five minutes from flash to first SoC reading.

## What you need

- A Raspberry Pi 4 or 5 (any RAM size — even the 1 GB Pi 4 is plenty).
- An 8 GB+ microSD card.
- A [Renogy BT-1 or BT-2 dongle](/docs/supported-hardware) (Victron and JK BMS are on the roadmap, not in this release).
- A USB-C power supply (Pi 4 / 5 official is ideal).

## Flash the SD card

1. Install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
2. [Download the WattPost image](/download) — about 600 MB.
3. In Imager: **Choose OS → Use custom** → select the `.img.xz` you just downloaded.
4. **Choose Storage** → pick the SD card.
5. Hit **Write**. Takes 3–5 minutes depending on card speed.

## First boot

1. Slot the SD card into the Pi, plug in your BT dongle, power on.
2. Wait ~60 seconds for first boot.
3. Open `http://wattpost.local` from any browser on the same network.
4. If your router doesn't resolve `wattpost.local`, look up the Pi's IP and use that.

You should see the **WattPost dashboard** with a state-of-charge donut, power flow visualisation, and an empty Devices list waiting for you to pair gear.

## Pair your first device

Settings → **Devices & setup** → **Pair a new device** scans for Renogy gear in range. Tap the one you want and confirm. The dashboard starts filling in within ~10 seconds of the first poll.

## What's next?

- [Pair an account](/docs/pairing) for the multi-site cloud dashboard
- [Set up alerts](/docs/alerts) — ntfy, Discord, Pushover, email, MQTT
- [Browse supported hardware](/docs/supported-hardware)
- [How updates work](/docs/updates)
