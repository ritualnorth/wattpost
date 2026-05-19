# Read your Victron gear over cable when Bluetooth isn't reliable

WattPost reads Victron devices over Bluetooth out of the box.
Their Instant Readout broadcasts are short, encrypted, and
require no pairing. For most installs that's perfect.

Some installs aren't most installs. Metal vans where the SmartShunt
is bolted to the negative terminal in the battery bay and the Pi is
up front near the driver. Marina installs with twenty Bluetooth
sources fighting for spectrum. Off-grid sheds with the Pi in a
metal enclosure. In any of those, BLE goes flaky: 30 seconds of
data, two minutes of silence, repeat.

WattPost v0.1.23 added a wired alternative. Victron publishes a
text protocol called **VE.Direct** on a small 4-pin port on the
side of every SmartShunt, every SmartSolar MPPT, and every Phoenix
Inverter VE.Direct. You can plug a USB cable in and read the same
data over wire instead of over the air.

This guide walks the setup.

## Prerequisites

- A WattPost appliance on v0.1.23 or newer.
- A Victron device with a VE.Direct port: SmartShunt, BMV-7xx,
  SmartSolar MPPT, or Phoenix Inverter VE.Direct.
- One of:
  - **Victron VE.Direct to USB interface** cable (~£25-30). The
    safe path. Plug-and-play.
  - A DIY rig: a 4-pin JST PH 2.0mm pigtail (~£3) wired to an
    FTDI or CP2102 USB-TTL adapter (~£8). Total ~£12 but you
    have to wire it yourself.
- USB-accessible Pi (any model works).

## Step 1: Plug in the cable

Pop the VE.Direct cover on your Victron device. The connector is
keyed; it only goes in one way. The other end is USB-A; plug
that into your Pi.

If you're doing the DIY route, the pinout is:

```
Victron VE.Direct (looking into the device socket, key facing down):
  pin 1  GND  ── GND on your USB-TTL adapter
  pin 2  RX   ── adapter TX (data into the device, unused for read-only)
  pin 3  TX   ── adapter RX (data from the device)
  pin 4  +5V  ── leave disconnected
```

Three wires, not four. Pin 4 is a 5 V supply Victron offers for
adapters that need to be powered from the device; we're using a
USB-powered adapter, so leave it open. If you connect pin 4 to
the USB +5V it doesn't damage anything but it's unnecessary.

## Step 2: Find the device path

On the Pi, plug the cable in and check what serial port appeared:

```bash
ls /dev/ttyUSB*
```

If you only have one USB-serial adapter on the Pi, it'll be
`/dev/ttyUSB0`. If you've already got a Renogy USB-RS485
adapter plugged in, the Victron cable will likely show up as
`/dev/ttyUSB1`. To be sure which is which, unplug the Victron
cable, run the command, plug it back in, run again. The one
that appeared is yours.

A stable name that won't change between reboots:

```bash
udevadm info -q property -n /dev/ttyUSB0 | grep ID_SERIAL
```

Note the value; you'll use the by-id path in config so a future
USB reshuffle doesn't break things:

```
/dev/serial/by-id/usb-Victron_Energy_BV_VE_Direct_cable_XXXXXX-if00-port0
```

## Step 3: Add the transport and device to config.yaml

SSH in (or `docker exec`) and edit:

```bash
sudoedit /etc/wattpost/config.yaml
```

Add a transport and a matching device:

```yaml
transports:
  - id: vedirect_shunt
    type: ve_direct
    port: /dev/serial/by-id/usb-Victron_Energy_BV_VE_Direct_cable_XXXXXX-if00-port0

devices:
  - id: smartshunt
    transport: vedirect_shunt
    vendor: victron_vedirect
    kind: shunt
    slave_id: 0
    label: "Smart shunt"
```

Three things to note:

- The vendor id is `victron_vedirect`, **not** `victron`. The
  BLE driver and the wired driver are sibling vendor entries
  with the same field-output shape but different transport
  contracts.
- `kind` matches the device type: `shunt` for SmartShunt and BMV,
  `charge_controller` for SmartSolar MPPT, `inverter` for Phoenix
  Inverter VE.Direct.
- `slave_id: 0` is a placeholder. VE.Direct doesn't have a slave
  concept (it's one cable per device), but the field is
  required by the config schema.

Save the file. The daemon picks the change up on the next poll
cycle.

## Step 4: See it on the dashboard

Open WattPost. The device tile appears within one poll. Voltage,
current, SoC, time-to-go, all the usual fields. The Flow tile
treats this device the same as a BLE-discovered one. The only
difference is the underlying transport, and the dashboard
doesn't see that.

If the tile shows "Silent" or "Stale" rather than live numbers:

- The cable might be on the wrong port. Re-run the `ls
  /dev/ttyUSB*` step.
- The device might have its VE.Direct port disabled (some
  SmartSolar models gate it behind a VictronConnect toggle).
  Open VictronConnect, navigate to **Settings → VE.Direct port
  → mode**, confirm it's set to **Normal** (not **VE.Smart
  Networking** or **MPPT RX pin function**).
- The cable might be DIY-wired with TX and RX swapped. Try
  flipping them.

## Mixing wired Victron with everything else

The wired Victron path coexists with every other transport on
the same Pi. A representative mixed-stack van setup:

- Renogy MPPT on a BT-2 dongle (BLE)
- Renogy DCC50S on a USB-RS485 adapter (wired, Modbus RTU)
- Victron SmartShunt on a VE.Direct cable (wired, text protocol)
- JK BMS in the battery box (BLE broadcast)

That's four transports on one Pi reading four different
protocols. One dashboard. The Pi handles it without breaking a
sweat.

## Why not just use BLE everywhere

BLE is wonderful right up until it isn't. The cases where wire
genuinely wins:

- **Metal vehicle bodies.** Bluetooth doesn't go through 3 mm
  of steel. A SmartShunt in the battery bay of a Sprinter
  reading to a Pi in the driver-side cabinet is borderline
  reception at the best of times.
- **Marinas and RV parks.** Twenty Bluetooth sources within
  range of each other will eat each other's airtime.
- **Long-term reliability.** A VE.Direct cable just works. BLE
  has weather: encryption-key edge cases, scanner cycles,
  dongle firmware quirks. Most installs never hit any of those.
  Some installs hit all of them.

If your install is fine on BLE, stay on BLE. If it isn't,
you've got £25 worth of cable to fix it.

## Conclusion

You've got a wired Victron read path running on the same Pi as
everything else. The Flow strip and the bank-aggregation logic
treat the device identically to a BLE-discovered one because
the field surface is the same. You can mix this path with BLE,
with USB-RS485, and with the smart-plug control output for the
solar-pause rule. One Pi, four transports, one dashboard.

The full transport reference lives in
[Wired setup](/docs/wired-setup).
