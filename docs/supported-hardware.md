# Supported hardware

WattPost polls battery management systems, charge controllers, and shunts over **Bluetooth**. Reuses dongles you likely already own.

Missing your kit? Email [support@wattpost.io](mailto:support@wattpost.io) with the device name + protocol details — if the BLE protocol is publicly documented (or we can borrow a sniffer), it usually ships in a release or two.

## Shipping today

### Renogy

Communicates via a **BT-1** or **BT-2** dongle plugged into the device's RJ45 / RJ12 comms port.

Drivers:

- **Rover MPPT** charge controllers (Rover Li, Rover Boost)
- **Smart Lithium batteries** — per-pack voltage, temperature, cell drift, cycle count

Connect one BT-2 to the master device on a shared RS-485 bus and WattPost can talk to every Renogy unit on the chain via the same dongle.

## On the roadmap

No commit dates yet. If you want one of these sooner, email [support@wattpost.io](mailto:support@wattpost.io).

- **Victron** — Smart MPPT, SmartShunt, Phoenix inverters via Victron Smart Bluetooth / VE.Direct
- **JK BMS** — Inverter BMS B-series, JK-B1A20S / B2A24S via the JK BLE module
- **Renogy DC-DC chargers** (DCC30S, DCC50S) and **Smart Shunt 300**
- **Hybrid inverters** — EG4, Sol-Ark
- **Sub-metering** — Shelly EM, IoTaWatt

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers — they break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
