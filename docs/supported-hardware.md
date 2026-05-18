# Supported hardware

WattPost polls battery management systems, charge controllers, and shunts over **Bluetooth**. Reuses dongles you likely already own.

Missing your kit? Email [support@wattpost.io](mailto:support@wattpost.io) with the device name + protocol details — if the BLE protocol is publicly documented (or we can borrow a sniffer), it usually ships in a release or two.

## Shipping today

### Renogy

Communicates via a **BT-1** or **BT-2** dongle plugged into the device's RJ45 / RJ12 comms port.

Drivers:

- **Rover MPPT** charge controllers (Rover, Rover Li, Rover Elite, Rover Boost, Wanderer, Adventurer, Voyager)
- **DCC50S / DCC30S / DCC25S / DCC15S** DC-DC + MPPT combo chargers
- **Smart Lithium batteries** — per-pack voltage, temperature, cell drift, cycle count
- **1000 W / 2000 W / 3000 W pure-sine inverter-chargers** — AC in/out, MPPT side, load %
- **Battery Monitor with Shunt** — RBM-S100 / S300 / S500 — voltage, current, SoC, time-to-empty / time-to-full, cumulative Ah counters

Connect one BT-2 to the master device on a shared RS-485 bus and WattPost can talk to every Renogy unit on the chain via the same dongle.

## On the roadmap

No commit dates yet. If you want one of these sooner, email [support@wattpost.io](mailto:support@wattpost.io).

- **Victron** — Phoenix inverters via VE.Direct USB
- **Hybrid inverters** — EG4, Sol-Ark
- **Sub-metering** — Shelly EM, IoTaWatt

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers — they break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
