# Supported hardware

WattPost polls battery management systems, charge controllers, shunts and inverter-chargers over **Bluetooth** or **USB-RS485** (wired). Most installs reuse dongles you already own.

Missing your kit? Email [support@wattpost.io](mailto:support@wattpost.io) with the device name + protocol details. If the BLE protocol is publicly documented (or we can borrow a sniffer), it usually ships in a release or two.

## Connection types at a glance

| Vendor | BT-2 dongle | USB-RS485 (wired) | Direct BLE (no dongle) |
| --- | :---: | :---: | :---: |
| **Renogy** | ✓ (default) | ✓ (recommended for long runs / metal vans) | · |
| **Victron** | · | ✓ (VE.Direct, on models with the port) | ✓ (Instant Readout broadcasts, default) |
| **JK BMS** | · | · | ✓ (BLE service broadcasts) |
| **JBD / Overkill** | · | · | ✓ (FF00 service) |
| **Daly** | · | · | ✓ (FFF0 service) |
| **EPEVER** | · | ✓ (USB-RS485, FC04) | · |
| **AiLi shunt** | · | · | ✓ (FFE0 service) |
| **Junctek shunt** | · | · | ✓ (FFE0 service) |

The wizard's "Add another connection" step lets you mix and match ·
e.g. a BT-2 to a Renogy MPPT in the garage **and** a USB-RS485 wired
straight to a Renogy DCC50S in the van conversion. See
[Wired setup](/docs/wired-setup) for the full cable / pinout reference.

## Renogy

Communicates via a **BT-1** or **BT-2** dongle plugged into the device's RJ45 / RJ12 comms port. One BT-2 on a shared RS-485 bus reaches every Renogy unit on the chain.

- **Rover MPPT** charge controllers (Rover, Rover Li, Rover Elite, Rover Boost, Wanderer, Adventurer, Voyager)
- **DCC50S / DCC30S / DCC25S / DCC15S** DC-DC + MPPT combo chargers
- **Smart Lithium batteries**. Per-pack voltage, temperature, cell drift, cycle count
- **1000 W / 2000 W / 3000 W pure-sine inverter-chargers**. AC in/out, MPPT side, load %
- **Battery Monitor with Shunt**. RBM-S100 / S300 / S500. Voltage, current, SoC, time-to-empty / time-to-full, cumulative Ah counters

If you'd rather not run a BT-2, see [Wired setup](/docs/wired-setup). USB-RS485 + a Cat5 patch cable into the comms port works identically (and is more reliable past ~5 m or through metal van walls).

## Victron

Reads **Instant Readout** BLE advertisements. Victron's Smart-series devices broadcast every ~1 s with no pairing required. You'll need the per-device encryption key (32-char hex string, visible once in the VictronConnect app under **Settings → Product info → Instant readout via Bluetooth → Show**).

- **SmartShunt** 500 / 1000 / 2000 A. Voltage, current, SoC, time-to-go, Ah counters
- **BMV-700 / 702 / 712**. Validated as compatible with the SmartShunt driver
- **SmartSolar MPPT** family. Every model with BLE Instant Readout
- **Orion-Tr Smart** + **Orion XS** DC-DC chargers
- **Blue Smart IP22 / IP65** AC chargers. Multi-bank output models render output_2 / output_3
- **SmartLithium** batteries
- **LynxSmartBMS**
- **SmartBatteryProtect**. Load-disconnect status + voltage thresholds
- **Phoenix Inverter VE.Direct**. The small pure-sine line that exposes a VE.Direct port. Read via cable, not BLE.

Read-only by design. We don't expose Cerbo/VRM-style write control. Heavy-Victron users keep using VRM for that.

### Wired alternative: VE.Direct over cable

For metal-van installs and dense-RF environments where BLE struggles, WattPost also reads SmartShunt / SmartSolar MPPT / Phoenix Inverter over Victron's **VE.Direct** wired protocol. ~£25 Victron-branded VE.Direct-to-USB cable, or a ~£12 DIY JST-to-FTDI rig. See [Wired setup](/docs/wired-setup) for the pinout and config. Read-only on this path too — VE.Direct doesn't expose writes.

## JK BMS

JK B-series (BD6A20S, B1A24S, B2A24S, etc.) advertise their own Bluetooth service. No separate dongle, no encryption keys. The wizard's BLE scan picks them up automatically with a "JK BMS" hint badge. Read-only.

## JBD / Overkill Solar BMS

The BMS family inside most sub-£500 LFP packs. **Pending community validation** — the driver is shipped on protocol docs + open-source reverse engineering (Overkill Solar's reference client). Customer reports against real hardware will catch any remaining field-mapping quirks.

- **Overkill Solar** (US-branded JBD)
- **Battle Born**, **LiTime**, **Power Queen**, many **Eco-Worthy** SKUs — these are JBD-rebranded packs, so the same driver covers them
- **Vatrer** and other AliExpress-direct LFP packs with the BD/JBD-style smart-app sticker

Service UUID `0xFF00`. Read-only. Configure with the pack's BLE MAC.

## Daly Smart BMS

Second-most-common BMS in budget LFP packs after JBD. **Pending community validation.**

- **Daly** B-series (smart variant — the dumb 4S/8S/16S BMS without Bluetooth isn't covered)
- Anything advertising as `DL-…` or `BMS-…` and pairing with the "Smart BMS" Android app

Read-only. BLE service UUID `0xFFF0`.

## EPEVER MPPT

The Tracer family is the #1 budget MPPT in DIY van and cabin builds. **Pending community validation.**

- **Tracer-AN** / **Tracer-BN** charge controllers (every wattage)
- **Triron** and **BN-DR** variants
- **eTracer** (legacy)

USB-RS485 wired, same as Renogy. Slave ID 1 by default. Use the wizard's USB scan; pick `Use as Modbus`, then set `vendor: epever` and `kind: charge_controller` in the device step. Live state arrives over FC04 (input registers) rather than FC03; the driver handles that automatically.

## AiLi smart shunt

Sub-£40 BLE shunt. The first piece of telemetry most DIY van builders buy. **Pending community validation.**

- **AiLi** Battery Monitor with single-display unit
- Various rebrands that ship with the same "Battery Monitor" Android app

Service UUID `0xFFE0`, read-only. No encryption key needed.

## Junctek shunt

Second-most-common cheap shunt after AiLi. **Pending community validation.**

- **Junctek KH-F** (BLE + UART variants)
- **Junctek KG-F** (BLE only)

ASCII-framed protocol on the FFE1 characteristic. Read-only.

## Voltronic / Axpert / MPP Solar (experimental)

Single shared ASCII-over-USB-HID protocol covers a long tail of hybrid-inverter rebadges. **Experimental — built from protocol docs, awaiting first-customer reports.** Plug the inverter's USB cable into the Pi/host; no extra hardware needed.

Rebadges expected to work (one driver, many badges):

- **Axpert** (Voltronic), **MPP Solar PIP / LV-MK**, **EG4 6000XP / 6500EX**
- **Mecer SOL-I-AX**, **RCT Axpert**, **Infinisolar V / E**
- **Anenji**, **Datouboss**, **HZSolar**, **Effekta KS**, **LVTopSun**
- **PowMr**, **Easun ISolar**

Read-only — live status (V / A / W / SoC / temps), mode (line / battery / fault / eco), warning bitmap. We never send a write command. Three-phase / dual-output models (QPIGS2/3) only have phase 1 parsed today.

USB-HID transport (default VID:PID `0665:5161`, override in YAML for EG4 variants on `0001:0000`). Drop in your `config.yaml`:

```yaml
transports:
  - id: voltronic_usb
    type: usbhid_voltronic
    label: Hybrid inverter

devices:
  - vendor: voltronic
    kind: inverter
    transport: voltronic_usb
    slave_id: 1
```

Setup-wizard support comes after the first customer reports the parse looks correct on their firmware.

## On the roadmap

No commit dates yet. If you want one of these sooner, email [support@wattpost.io](mailto:support@wattpost.io).

- **Deye / Sunsynk / Sol-Ark hybrid inverters** (separate protocol family from Voltronic).
- **Sub-metering**. Shelly EM, IoTaWatt

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers. They break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
