# Supported hardware

WattPost polls battery management systems, charge controllers, and shunts over **Bluetooth**. Most off-grid users already have a BLE-capable device or dongle; WattPost reuses it instead of adding a new gateway.

Missing your kit? Email [support@wattpost.io](mailto:support@wattpost.io) with the device name + protocol details — if the BLE protocol is publicly documented (or we can borrow a sniffer), we can usually add a vendor in a release or two.

## Renogy

Communicates via a **BT-1** or **BT-2** dongle plugged into the device's RJ45 / RJ12 comms port. Most Renogy units ship with one BT dongle compatible with all their Bluetooth-modbus-speaking gear.

Drivers:

- **Rover MPPT** family (Rover Li, Rover Boost)
- **DC-DC chargers** (DCC30S, DCC50S)
- **Smart Shunt 300** — bank-level voltage / current / SoC / remaining Ah
- **Smart Lithium batteries** — per-pack voltage, temperature, cell drift, cycle count

Connect one BT-2 to the master device on a shared RS-485 bus and WattPost can talk to every Renogy unit on the chain via the same dongle.

## Victron

Communicates via the device's built-in **Victron Smart Bluetooth** advertising packets — no extra dongle.

Drivers:

- **Smart MPPT** (BlueSolar, SmartSolar)
- **SmartShunt** — battery monitor, bank-level metrics
- **Phoenix inverters** with VE.Direct BLE
- **MultiPlus** (via VE.Bus pass-through, currently beta)

## JK BMS

Communicates via the optional **JK BLE module** that snaps onto the BMS.

Drivers:

- **Inverter BMS B-series**
- **JK-B1A20S / B2A24S** family
- Multi-pack systems supported — pair each pack individually, see them all aggregated in the bank view

## What's planned

- Victron SmartShunt full feature parity (currently basic)
- **EG4 / Sol-Ark hybrid inverters** — AC-out telemetry for the load tile
- **Shelly EM / IoTaWatt** — whole-house AC sub-metering when the inverter doesn't expose its own
- **Load-side shunts** — Victron BMV-712 / SmartShunt 500A configured as a DC load monitor

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers — they break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
