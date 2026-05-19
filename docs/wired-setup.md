# Wired setup. Cat5 / USB-RS485 / BT-2 alternatives

The default WattPost install talks Modbus over Bluetooth via a Renogy
BT-1 / BT-2 dongle. That's the fastest path to "it works", but it's
not the only option. Three setups are worth knowing about.

## Cable shopping list (read this first)

If you've decided to skip the BT-2 and run wired, pick one of these
before you order anything else:

| Option | Approx cost | Wiring effort | Reliability |
| - | - | - | - |
| **Renogy "RS485-to-USB" cable** (pre-wired RJ45 → USB) | £15-20 | None | Best. Renogy-spec, no crimping |
| **DSD TECH SH-U10** (FTDI FT232 USB-RS485) + Cat5e patch cable | ~£12 | One end of the patch cable: strip pins 3 + 4, screw-terminal them into A + B on the adapter | Excellent. FTDI is the gold-standard chip on Linux |
| **Generic USB-to-RJ45 RS485 console cable** | ~£12-18 | Plug-and-play | Mixed. Usually CH340 chip; pinout sometimes A↔B reversed |

**Rule of thumb:** if you're testing WattPost for the first time, buy
the **Renogy pre-wired cable**. Zero variables, RJ45 plugs straight
in. Move to the DSD/FTDI adapter once you're confident in the
software path and want longer runs (Cat5e is good to ~30 m, far
better than BLE through walls).

**Whichever you pick, the chip matters more than the brand.** FTDI
(FT232) and CP2102 chipsets work out of the box on every Linux distro
WattPost runs on. CH340 also works but needs `ch341` in the kernel
(present on Pi OS Bookworm and Debian 12+, occasionally missing on
older builds). When the wizard's `Find USB` step shows the adapter as
`/dev/ttyUSB0` with chip name FTDI / CP210x / CH341, you're good.

## 1. The default: Renogy BT-2 (BLE)

The BT-2 plugs into your MPPT's RJ45 (Cat5-style) communication port
and bridges Modbus-over-RS485 to BLE so the Pi can reach it without
running wires.

```
  ┌────────────┐     RJ45      ┌──────────┐    BLE     ┌──────┐
  │  Renogy    │──Cat5────────▶│  BT-2    │ ─────────▶ │  Pi  │
  │  Rover/Cube│               │ dongle   │            └──────┘
  └────────────┘               └──────────┘
```

**Pros:** plug-and-play, no soldering, no driver hunting.
**Cons:** BLE link can be flaky at >5 m or through metal van walls.
The BT-2 swallows FC06 write acks (we work around it; see the
[Renogy MPPT load output](/docs/devices#controllable-outputs) doc).

## 2. Direct USB-RS485 (wired)

If BLE is unreliable (concrete shed, metal van, distant Pi) you can
swap the BT-2 for a USB-to-RS485 adapter and run a serial cable
straight to the MPPT's RJ45 port.

```
  ┌────────────┐     RJ45     ┌──────────────┐  USB   ┌──────┐
  │  Renogy    │──Cat5───────▶│ USB-RS485    │ ────▶  │  Pi  │
  │  Rover/Cube│              │ adapter      │        └──────┘
  └────────────┘              └──────────────┘
```

**Pinout (MPPT side, RJ45):**

```
  RJ45 pin 1 ─── GND
  RJ45 pin 2 ─── GND
  RJ45 pin 3 ─── RS485-A
  RJ45 pin 4 ─── RS485-B
  RJ45 pin 5 ─── (unused)
  RJ45 pin 6 ─── (unused)
  RJ45 pin 7 ─── 5 V (do NOT connect to USB adapter)
  RJ45 pin 8 ─── 5 V (do NOT connect to USB adapter)
```

Crimp pins **3 + 4** (A + B) into the screw terminals of any FTDI- or
CH340-based USB-RS485 dongle. Leave 7 + 8 unconnected. The MPPT
supplies its own 5 V on those, and shorting them to the Pi side is
not recommended.

**Wire choice:** a single Cat5e patch cable works for runs up to ~30
m. For longer runs use shielded twisted pair and terminate at one end
with a 120 Ω resistor across A/B.

**Add it in the wizard:** Settings → Setup → Add transport → pick
**Serial Modbus**. The wizard's USB scan tries to classify each
`/dev/ttyUSB*` device as `modbus_rtu`, `nmea_gps`, or `unknown`.

**Pros:** rock-solid, no BLE flakiness, works at any distance up to
30 m on plain Cat5.
**Cons:** you need a wire path between MPPT and Pi.

## 3. Direct serial on a Pi (UART)

Pi 3/4/5 expose a 3.3 V UART on the GPIO header (pins 8 + 10). With a
MAX485 or similar TTL-to-RS485 transceiver you can skip the USB
adapter entirely.

```
  Pi GPIO 14 (TX) ─── DI
  Pi GPIO 15 (RX) ─── RO              MAX485 ──── A/B ──▶ MPPT RJ45 3/4
  Pi GPIO  4 ─────── DE + ~RE         module
  Pi 3V3 ───────────  VCC
  Pi GND ───────────  GND
```

Then set the serial transport's `port` to `/dev/serial0` in the
wizard. The Pi's UART contention with bluetooth-on-UART means you'll
want to set `dtoverlay=disable-bt` in `/boot/firmware/config.txt`
first (or use the bluetooth-friendly mini-UART instead).

**Pros:** zero USB devices to track, ultra-low latency.
**Cons:** requires soldering / level-shifter hookup, knocks Pi
on-board BLE out of action.

## 4. JK BMS BLE

JK BMSes (B series, BD6A20S etc.) advertise their own Bluetooth
service. No separate dongle. The wizard's BLE scan picks them up
automatically; they show with a "JK BMS" hint badge.

```
  ┌──────────────┐    BLE     ┌──────┐
  │   JK BMS     │ ─────────▶ │  Pi  │
  │  (B series)  │            └──────┘
  └──────────────┘
```

JK BMS BLE is read-only forever. See
[Adding devices](/docs/devices) for what we expose.

## 5. Victron Instant Readout

Victron's Smart-series devices (SmartShunt, SmartSolar MPPT, Orion-Tr
Smart, BMV-7xx etc.) broadcast unsolicited BLE advertisements every
~1 s. WattPost scans them passively. No pairing. But you do need
the per-device encryption key (a 32-character hex string visible
once in the VictronConnect app under **Settings → Product info →
Instant readout via Bluetooth → Show**).

```
  ┌──────────────────┐    BLE     ┌──────┐
  │ Victron Smart*   │ ─advert──▶ │  Pi  │
  │ (Shunt/MPPT/etc) │            └──────┘
  └──────────────────┘
```

In the wizard's BLE scan, Victron devices show with their model
name and a "Victron Instant Readout" hint. Pick **Use this**, paste
the encryption key, save. No daemon restart needed.

## Mixing transports

You can run any combination of the above on one Pi. A typical
mixed-stack van setup:

- One BT-2 → Renogy MPPT
- One USB-RS485 → wired to a Renogy DCC50S DC-DC charger
- Passive BLE scanner picking up a Victron SmartShunt + JK BMS

Every device shows up on the same dashboard; the bank aggregate
chooses the shunt over the BMS for SoC (see the
[Battery health tile](/docs/devices#battery-health) for how).

## Troubleshooting

- **USB-RS485 not detected**: check the adapter is in
  `lsusb` (`lsusb | grep -i ch340`). FTDI-based adapters use
  `dmesg | grep tty` to find the assigned `/dev/ttyUSB*`.
- **No response on serial**: A/B reversed is the most common cause ·
  swap pins 3 and 4 and re-scan.
- **BLE drops every few minutes**: power-saving on the Pi's onboard
  radio. `sudo iwconfig wlan0 power off` and add it to `/etc/rc.local`.
