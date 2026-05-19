# Wire your Renogy gear to WattPost instead of using a BT-2 dongle

WattPost reads Renogy devices over Bluetooth out of the box.
You plug a BT-1 or BT-2 dongle into the comms port of your Rover
or DCC50S, the Pi picks it up, you pair it through the wizard,
done.

For some installs, the dongle is the weakest link. A BT-2 plugged
into a charge controller buried at the back of a van or stuffed
into a battery box only has a few metres of useful BLE range. Walk
to the front of the van with your laptop and the connection drops.
Move the Pi to the cab to keep it cool and the dongle goes silent.

Wire the same comms port directly into the Pi and the dongle
becomes a non-issue. USB-RS485 over Cat5e gives you a reliable,
~30 m link with no Bluetooth involved at all. This guide walks
the setup end to end.

## Prerequisites

- A WattPost appliance with at least the wizard reachable.
- A Renogy device with an RJ45 or RJ12 comms port: Rover,
  Wanderer, Adventurer, Voyager, DCC50S / DCC30S / DCC25S /
  DCC15S, the 1000-3000 W inverter-chargers, or the Battery
  Monitor with Shunt.
- One of:
  - **Renogy "RS485 to USB" cable** (~£15-20). Pre-wired RJ45
    on one end, USB-A on the other. The safe path.
  - **DSD TECH SH-U11** USB-to-RS485 adapter (~£13) plus a
    Cat5e patch cable (~£3). FTDI FT232R chip, screw terminals.
    You'll crimp or strip one end of the patch cable to match
    Renogy's pinout.

The cheaper SH-U10 in the same DSD TECH range uses Silicon Labs
CP2102 instead of FTDI. Both work on Linux; FTDI is the more
bulletproof chip if you can find it for the same money.

**Avoid the SH-RJ45K and other "USB-to-RJ45 RS485 console
cables".** They use a generic Cisco/console pinout, not
Renogy's. You'd either get no response at all or have to cut
the RJ45 end off and re-crimp.

## Step 1: Pick your wiring approach

If you bought the Renogy pre-wired cable, skip to Step 2. The
RJ45 end goes straight into the device.

If you bought the SH-U11 plus a Cat5e patch cable, you need to
expose pins 3 and 4 of the Cat5e at one end and screw them into
the A and B terminals on the SH-U11.

**Renogy's RJ45 pinout (looking into the device socket, tab
down, pin 1 on the left):**

```
pin 1   GND
pin 2   GND
pin 3   RS485-A
pin 4   RS485-B
pin 5   unused
pin 6   unused
pin 7   +5V   ← do NOT connect to the USB adapter
pin 8   +5V   ← do NOT connect to the USB adapter
```

Cat5e wires from pin 3 = white-with-green stripe (TIA-568B),
pin 4 = blue. Different patch-cable manufacturers use different
colour mappings, so verify with a continuity tester before
crimping rather than trusting the colour.

Strip the patch cable's other end. The white-green wire goes
into the **A** screw terminal on the SH-U11. The blue wire
goes into **B**. Leave the others disconnected.

If your A and B end up reversed (most common cause of "no
response on serial" in support tickets), the dashboard will
show the device as silent. Swap the two wires and try again.

## Step 2: Plug everything in

USB end of the cable goes into the Pi. The RJ45 end goes into
the Renogy device's comms port.

If you have multiple Renogy devices and want them all on one
cable, you have three options:

- **A Renogy Hub.** A passive RS485 splitter box; every device
  cable plugs in, the USB adapter plugs into one port. All
  devices appear on the same shared bus at different slave IDs.
- **Daisy-chain.** Most Renogy devices have two RJ12 ports
  specifically for chaining. Plug device A → device B → device
  C → USB adapter, all sharing the same bus.
- **One adapter per device.** Several SH-U11s in the same Pi.
  Each becomes its own transport in WattPost. More cables, more
  USB ports, but partial failure mode (one cable dies, others
  keep working).

The Hub is the cheapest if you already have one. Daisy-chain is
the cheapest if you don't. One-per-device is the most resilient
but the most cable.

## Step 3: Find the device's stable path

On the Pi, run:

```bash
ls /dev/serial/by-id/
```

That lists USB serial adapters by manufacturer and serial
number. The path stays the same across reboots and across USB
port reshuffles. The transient `/dev/ttyUSB0` path doesn't,
which means if you unplug and replug or boot the Pi with
something else attached, the number can change and your config
breaks.

Note the path:

```
/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AB0J7XYZ-if00-port0
```

It'll look slightly different per adapter. For an SH-U11 it
mentions "FTDI"; for the Renogy pre-wired cable it might
mention "Silicon Labs" or "USB-Serial". Whichever, copy the
full path.

## Step 4: Add the transport in the WattPost wizard

Open the dashboard. Go to **Setup → Find USB**. WattPost scans
`/dev/ttyUSB*` and `/dev/ttyACM*`, sniffs each for half a
second, and lists what it found.

Your adapter should appear with a chip label ("FTDI FT232",
"Silicon Labs CP210x", or similar). Click **Use as Modbus**.

WattPost writes the transport into `config.yaml` and hot-reloads
the scheduler. Within one poll cycle the next step opens
asking which Renogy device sits on this transport.

If you'd rather do it by hand:

```yaml
transports:
  - id: serial_usb0
    type: serial_modbus
    port: /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AB0J7XYZ-if00-port0
    baudrate: 9600

devices:
  - id: rover
    transport: serial_usb0
    vendor: renogy
    kind: charge_controller
    slave_id: 0xFF       # Renogy's default
    label: "Rover MPPT"
```

`slave_id` defaults to `0xFF` for Renogy charge controllers and
inverters. Renogy smart batteries default to `0x30`. Renogy
shunts default to a configurable address, but `0x30` is the
out-of-box setting on the RBM range.

## Step 5: Watch the device land on the dashboard

Open WattPost. Within one poll cycle (default 60 s) the device
tile appears with voltage, current, charge state, and whatever
fields the driver exposes for that model. The Flow strip picks
it up automatically too.

If the tile reads "Silent" or "Stale" rather than live numbers:

- **Slave ID wrong.** Open the device's own app (Renogy DC Home)
  and check the configured address. Update the `slave_id` in
  config to match.
- **A and B swapped.** Cut the patch cable, re-strip, try again.
  Five-minute fix.
- **Adapter held by another process.** Check `lsof
  /dev/serial/by-id/...` if you're comfortable on the
  command line. If you ran the daemon while configuring, restart
  it.
- **Bus terminator missing on long runs.** For runs over ~10 m
  you may need a 120 Ω resistor across A and B at the far end of
  the bus. Most short van-build runs don't need it.

## Why wire when Bluetooth works

BLE is fine when it works. Wire wins when it doesn't:

- **Metal van bodies.** Bluetooth doesn't go through 3 mm of
  steel. The BT-2 in your battery bay, the Pi up in the cab is
  the canonical "BLE keeps dropping" scenario.
- **Long-running installs.** A wired link doesn't care about
  Bluetooth scanner cycles, BlueZ dongle firmware quirks, or
  the "BT-2 silently held by another LAN host" failure mode we
  shipped a wizard hint for in v0.1.20.
- **Multi-device setups on one cable.** The Renogy Hub (or
  daisy-chain) puts every connected device on one shared RS485
  bus reached by one USB adapter. One adapter, three devices,
  much simpler than three BT-2s.
- **Compatibility with WattPost's verified write path.** The
  FC06 settings-write path (#116) is bulletproof on serial. BLE
  works too but the BT-2 firmware swallows the ack on Rover
  writes, which our adapter tolerates via read-back. Wire skips
  that whole class of issue.

If your BT-2 setup is healthy on whatever your install is, stay
on it. If it isn't, ~£15 of cable will probably fix it.

## Mixing wired Renogy with the rest

The wired path coexists with every other transport on the same
Pi. A representative mixed-stack van setup looks like:

- Renogy Rover MPPT on a USB-RS485 cable (this guide)
- Renogy DCC50S on the same bus via daisy-chain
- Victron SmartShunt on a VE.Direct cable
  ([guide](/blog/victron-over-cable))
- JK BMS in the battery box (BLE broadcast)

Four devices, three protocols, two cables and one BLE listener
on the same Pi. The dashboard treats them all identically.

## Conclusion

You've got Renogy gear reading WattPost over a real cable, which
solves the BLE-through-metal problem the BT-2 dongles can't fix.
The Flow strip and bank-aggregation logic treat wired devices
identically to BLE ones because the field surface is the same.

For the full transport reference (BLE, USB-RS485, VE.Direct,
mixing them) see
[Wired setup](/docs/wired-setup).
