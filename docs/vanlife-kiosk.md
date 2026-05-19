# Vanlife kiosk install

The vanlife setup: a 7" Raspberry Pi touch display flush-mounted in the cabinetry, booting straight into the WattPost dashboard, no chrome, no menus. SoC donut, power flow, weather forecast. Visible at a glance from anywhere in the rig.

This guide walks the full kit. You don't need this if you already use a phone or laptop to check the dashboard. See [Kiosk mode](kiosk.md) for the lighter "old tablet on the wall" version.

## What you'll buy

| Item | Why | Approx cost |
| - | - | - |
| Raspberry Pi 5 (4 GB) | Drives the display + runs the daemon | £60 |
| Official Raspberry Pi 7" Touch Display 2 | The cleanest mounting solution; powered + driven by the Pi over a single ribbon cable | £60 |
| Official Pi 5 case for 7" Display | Sandwiches the Pi behind the screen; one mounting unit | £15 |
| USB-C power supply (Pi 5 official 27 W) | Cheaper PSUs undervolt and break BLE | £12 |
| 32 GB A1 microSD card | Bigger SD = more history retention; A1 = snappy SQLite writes | £8 |
| Renogy BT-2 dongle (if you're on Renogy gear) | Victron / JK BMS skip this. Built-in BLE | £8 |

Total: **~£155 + £8 dongle if needed**.

## Wiring

1. Sandwich the Pi 5 in the back of the official case so the GPIO ribbon reaches the display board. Hand-tight the four standoffs.
2. Plug the DSI ribbon cable between the Pi's DSI port and the display board. The notch on the connector points down on the Pi side.
3. Power the display via the supplied USB-C-to-USB-A cable from one of the Pi's two USB ports · *not* a separate PSU. The Pi's 5 V rail is enough for the screen.
4. Wire your battery / charger comms as normal (BT-2 plugged in, or Bluetooth-direct gear in range).

## Flash + first boot

1. Flash the latest WattPost image per the [SD-card install](getting-started.md). The image already includes the display drivers for both v1 and v2 Pi touch screens. No extra packages needed.
2. Pop the SD in, power on. The Pi boots to a small `Setting up WattPost…` splash on the screen for the first ~45 s, then drops into the dashboard's setup wizard.
3. Step through the wizard on the touch screen. It's mobile-layout-aware and the touch targets are big enough. If you'd rather use a phone, hit any other device on the same Wi-Fi at `http://wattpost.local`.

## Auto-boot to kiosk

After setup is finished and your hardware is paired, switch the Pi's launch URL to the kiosk view.

```bash
ssh wattpost@wattpost.local       # default password is printed on first boot
sudo wattpost-config              # WattPost's TUI config tool
```

In `wattpost-config`:

1. Pick **Display** → **Kiosk URL**
2. Set to `http://localhost:8000/kiosk?lock=1`. The `lock=1` hides the "Exit kiosk" button so a curious passenger can't tap into Settings.
3. Pick **Display** → **Hide cursor**: **Yes**
4. Pick **Display** → **Auto-rotate**: leave at `0` for landscape, set `270` if you mounted the screen vertically.
5. Reboot.

The Pi now boots to a fullscreen Chromium showing the kiosk view, hides the cursor after 2 seconds of no movement, and survives power cycles.

## Power management

The display backlight is on a separate kernel framebuffer. WattPost's `wattpost-display` helper handles:

- **Dim on idle**. Backlight drops to 10 % after 30 seconds of no touch, full brightness on any touch. Configurable in `wattpost-config` → **Display** → **Idle dim timeout**.
- **Off at night**. Optional: backlight off entirely between `quiet_hours.start` and `quiet_hours.end` from your `config.yaml`. Wake on touch.
- **Survives 12 V cutouts**. The Pi shuts down cleanly on power loss via the on-board UPS hat (if you've added one) or just survives the brown-out and re-launches kiosk on next boot.

## Mounting

The official case has four 2.5 mm threaded inserts on the back. Two options:

- **Recessed cabinet mount**. Cut a 165 × 105 mm rectangle in your cabinet face, the case bezel sits flush. M2.5 machine screws from the back into the inserts.
- **VESA arm**. Buy a £6 VESA 75 adapter plate from Amazon, screws into the inserts. Lets you rotate the screen out of the way when not in use.

Ventilate the back. The Pi 5 throttles at 80 °C, and a sealed cabinet on a 35 °C summer day will get there. A single 5 V 30 mm fan in the cabinet wall is plenty.

## Troubleshooting

| Symptom | Likely cause |
| - | - |
| Blank screen, Pi LEDs on | DSI ribbon not seated. Power off, reseat both ends, power on. |
| Screen rotates "wrong way" | Set **Display → Auto-rotate** to 90 / 180 / 270 to match your mount. |
| Touch registers wrong coordinates | After rotation change, run `wattpost-config` → **Display** → **Calibrate touch**. |
| Backlight stays on at night | Set `quiet_hours` in `config.yaml`. The display helper reads it on next reboot. |
| Random reboots | Almost always a weak USB-C supply. Use the official 27 W. |

## Cloud kiosk URL (if you want to flip between vans)

If you have multiple appliances paired to wattpost.cloud. Say, the van and a cabin. And want one kiosk that flips between them, use the cloud broker URL in Chromium's launch URL instead of `localhost`:

```
https://<slug>.wattpost.cloud/kiosk?lock=1
```

Different SSID, different appliance, same dashboard URL. The cloud broker (#139) reverse-proxies to whichever appliance you're paired to from this device.
