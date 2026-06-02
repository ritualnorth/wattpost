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

For metal-van installs and dense-RF environments where BLE struggles, WattPost also reads SmartShunt / SmartSolar MPPT / Phoenix Inverter over Victron's **VE.Direct** wired protocol. ~£25 Victron-branded VE.Direct-to-USB cable, or a ~£12 DIY JST-to-FTDI rig. See [Wired setup](/docs/wired-setup) for the pinout and config. Read-only on this path too, VE.Direct doesn't expose writes.

## JK BMS

JK B-series (BD6A20S, B1A24S, B2A24S, etc.) advertise their own Bluetooth service. No separate dongle, no encryption keys. The wizard's BLE scan picks them up automatically with a "JK BMS" hint badge. Read-only.

## JBD / Overkill Solar BMS

The BMS family inside most sub-£500 LFP packs. **Pending community validation**, the driver is shipped on protocol docs + open-source reverse engineering (Overkill Solar's reference client). Customer reports against real hardware will catch any remaining field-mapping quirks.

- **Overkill Solar** (US-branded JBD)
- **Battle Born**, **LiTime**, **Power Queen**, many **Eco-Worthy** SKUs, these are JBD-rebranded packs, so the same driver covers them
- **Vatrer** and other AliExpress-direct LFP packs with the BD/JBD-style smart-app sticker

Service UUID `0xFF00`. Read-only. Configure with the pack's BLE MAC.

## Daly Smart BMS

Second-most-common BMS in budget LFP packs after JBD. **Pending community validation.**

- **Daly** B-series (smart variant, the dumb 4S/8S/16S BMS without Bluetooth isn't covered)
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

Single shared ASCII-over-USB-HID protocol covers a long tail of hybrid-inverter rebadges. **Experimental, built from protocol docs, awaiting first-customer reports.** Plug the inverter's USB cable into the Pi/host; no extra hardware needed.

Rebadges expected to work (one driver, many badges):

- **Axpert** (Voltronic), **MPP Solar PIP / LV-MK**, **EG4 6000XP / 6500EX**
- **Mecer SOL-I-AX**, **RCT Axpert**, **Infinisolar V / E**
- **Anenji**, **Datouboss**, **HZSolar**, **Effekta KS**, **LVTopSun**
- **PowMr**, **Easun ISolar**

Read-only, live status (V / A / W / SoC / temps), mode (line / battery / fault / eco), warning bitmap. We never send a write command. Three-phase / dual-output models (QPIGS2/3) only have phase 1 parsed today.

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

## EG4 XP / kPV / FlexBOSS (experimental)

The EG4 inverter line splits into two protocol families. The
6500EX is a Voltronic rebadge and lives in the section above.
The XP / kPV / FlexBOSS line is **Luxpower-derived** and lives
here as its own driver.

Models expected to work, sharing the Luxpower input-register base:

- **EG4 12000XP**, **EG4 6000XP** (off-grid, split-phase)
- **EG4 18kPV**, **EG4 12kPV** (hybrid, grid-tied)
- **EG4 FlexBOSS21**, **EG4 FlexBOSS18**
- **Luxpower-branded LXP** siblings (LXP-LB, LXP-EU, LXP-LB-BR)

Read-only over **Modbus RTU on RS485** through the inverter's
**CT1 RJ45 port**:

| RJ45 pin | Signal |
|----------|--------|
| 7 | RS485-B |
| 8 | RS485-A |

USB-RS485 dongle (any FTDI, CH340, CP2102, etc.), 9600 baud,
8N1, slave ID 1. Same hardware path Renogy and EPEVER customers
use. Driver covers: device mode, battery V / SoC / SoH /
charge+discharge power / temperature, PV1+PV2 voltage + power
(both strings summed for the dashboard), AC output, EPS
(off-grid) output, grid V/Hz, internal + radiator temps, running
time. The 12000XP's split-phase L1/L2 voltages populate the
optional `eps_l1_voltage_v` / `eps_l2_voltage_v` fields; hybrid
models leave them empty.

Drop in your `config.yaml`:

```yaml
transports:
  - id: eg4_serial
    type: serial_modbus
    port: /dev/ttyUSB0
    baudrate: 9600
    label: EG4 USB-RS485

devices:
  - vendor: eg4
    kind: inverter
    transport: eg4_serial
    slave_id: 1
    label: EG4 inverter
```

Marked **experimental**: the register addresses are confirmed
across three independent public sources, but Luxpower firmwares
occasionally ship `battery_temperature` ÷10 vs ÷1 and a couple
of mode codes vary by family. First customer probe paste flips
this to stable. If you're running an EG4 XP / kPV and want to
help validate, email
[support@wattpost.io](mailto:support@wattpost.io).

## Deye / Sunsynk / Sol-Ark (experimental)

Three brand names, one Chinese OEM. Deye builds the chassis; Sunsynk rebrands them for the UK / EU market; Sol-Ark rebrands them for the US market. Same Modbus RTU register map across all three brands.

The catalogue splits into two register-map variants by chassis size:

**Single-phase + split-phase** (driver kind: `inverter_1p`)

- **Deye SUN-5K-SG04LP1**, **SUN-8K-SG04LP1**
- **Sunsynk SG01LP1** family (3.6 kW / 5.5 kW / 8 kW / 16 kW)
- **Sol-Ark 5K**, **8K**, **12K-1P**, **12K-2P** (US split-phase)

**Three-phase** (driver kind: `inverter_3p`)

- **Deye SUN-12K / 15K / 20K / 25K-SG01HP3**
- **Sunsynk Max-15K**, **Max-20K**
- **Sol-Ark 15K-3P**

Read-only over **Modbus RTU on RS485** through the inverter's RJ45 port. Pinout is non-standard (NOT T568), Deye uses pins 1 and 2 for the differential pair:

| RJ45 pin | Signal |
|----------|--------|
| 1 | RS485-B (D−) |
| 2 | RS485-A (D+) |
| 3 | GND |

Any FTDI / CH340 / CP2102 USB-RS485 dongle works, 9600 8N1, slave ID 1.

Drop in your `config.yaml`:

```yaml
transports:
  - id: deye_serial
    type: serial_modbus
    port: /dev/ttyUSB0
    baudrate: 9600
    label: Deye USB-RS485

devices:
  - vendor: deye
    kind: inverter_1p           # or inverter_3p for the bigger 3-phase units
    transport: deye_serial
    slave_id: 1
    label: Deye inverter
```

Marked **experimental** until a customer with real hardware confirms the per-firmware scaling. Three known footguns the driver catches up-front (different between 1P and 3P):

- Battery voltage scale (1P ÷100, 3P ÷10)
- Battery power magnitude (1P watts, 3P deci-watts)
- PV power sign convention (1P sign-flipped, 3P positive deci-watts)

If you're running a Deye / Sunsynk / Sol-Ark and want to help validate the first real-hardware probe, email [support@wattpost.io](mailto:support@wattpost.io).

## On the roadmap

No commit dates yet. If you want one of these sooner, email [support@wattpost.io](mailto:support@wattpost.io).

- **Sub-metering**. Shelly EM, IoTaWatt

## Hardware we won't add

Devices that **require cloud-side credentials** (proprietary auth, vendor-locked OAuth) won't get drivers. They break the local-first guarantee. If you have to log into the vendor's app to read your own battery, WattPost isn't the right tool.
