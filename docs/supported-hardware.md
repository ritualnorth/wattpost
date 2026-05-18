# Supported hardware

WattPost polls battery management systems, charge controllers, shunts and inverter-chargers over **Bluetooth**. Most installs reuse dongles you already own.

Missing your kit? Email [support@wattpost.io](mailto:support@wattpost.io) with the device name + protocol details — if the BLE protocol is publicly documented (or we can borrow a sniffer), it usually ships in a release or two.

## Renogy

Communicates via a **BT-1** or **BT-2** dongle plugged into the device's RJ45 / RJ12 comms port. One BT-2 on a shared RS-485 bus reaches every Renogy unit on the chain.

- **Rover MPPT** charge controllers (Rover, Rover Li, Rover Elite, Rover Boost, Wanderer, Adventurer, Voyager)
- **DCC50S / DCC30S / DCC25S / DCC15S** DC-DC + MPPT combo chargers
- **Smart Lithium batteries** — per-pack voltage, temperature, cell drift, cycle count
- **1000 W / 2000 W / 3000 W pure-sine inverter-chargers** — AC in/out, MPPT side, load %
- **Battery Monitor with Shunt** — RBM-S100 / S300 / S500 — voltage, current, SoC, time-to-empty / time-to-full, cumulative Ah counters

If you'd rather not run a BT-2, see [Wired setup](/docs/wired-setup) — USB-RS485 + a Cat5 patch cable into the comms port works identically (and is more reliable past ~5 m or through metal van walls).

## Victron

Reads **Instant Readout** BLE advertisements — Victron's Smart-series devices broadcast every ~1 s with no pairing required. You'll need the per-device encryption key (32-char hex string, visible once in the VictronConnect app under **Settings → Product info → Instant readout via Bluetooth → Show**).

- **SmartShunt** 500 / 1000 / 2000 A — voltage, current, SoC, time-to-go, Ah counters
- **BMV-700 / 702 / 712** — validated as compatible with the SmartShunt driver
- **SmartSolar MPPT** family — every model with BLE Instant Readout
- **Orion-Tr Smart** + **Orion XS** DC-DC chargers
- **Blue Smart IP22 / IP65** AC chargers — multi-bank output models render output_2 / output_3
- **SmartLithium** batteries
- **LynxSmartBMS**
- **SmartBatteryProtect** — load-disconnect status + voltage thresholds

Read-only by design. We don't expose Cerbo/VRM-style write control — heavy-Victron users keep using VRM for that.

## JK BMS

JK B-series (BD6A20S, B1A24S, B2A24S, etc.) advertise their own Bluetooth service — no separate dongle, no encryption keys. The wizard's BLE scan picks them up automatically with a "JK BMS" hint badge. Read-only.

## On the roadmap

No commit dates yet. If you want one of these sooner, email [support@wattpost.io](mailto:support@wattpost.io).

- **Phoenix inverters** via VE.Direct USB
- **Hybrid inverters** — EG4, Sol-Ark
- **Sub-metering** — Shelly EM, IoTaWatt

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers — they break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
