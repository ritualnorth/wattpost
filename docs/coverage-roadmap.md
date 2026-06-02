---
title: Coverage Roadmap
description: Which battery, charger, MPPT, BMS and shunt models WattPost talks to today, and which are next.
---

# Coverage roadmap

WattPost's wedge against vendor-locked monitors (Victron VRM,
Renogy ONE, MyBattery) is breadth: one dashboard that talks to the
mixed-stack van or cabin install instead of just one company's
gear. Driver count is the moat. This page is the live audit.

## What ships today

### Renogy (read + write where the device supports it)

| Category           | Models                                                |
| ------------------ | ----------------------------------------------------- |
| Charge controllers | Rover, Rover Elite, Rover Boost, Wanderer, Adventurer, Voyager |
| DC-DC + MPPT       | DCC50S, DCC30S, DCC25S, DCC15S                        |
| Smart batteries    | RBT100LFP12S-G1, RBT50LFP12S, RBT170LFP12S            |
| Smart shunts       | RBM-S100, RBM-S300, RBM-S500                          |
| Inverters          | 1000W / 2000W / 3000W pure-sine inverter chargers     |

Transports: BT-1 / BT-2 BLE dongle, USB-RS485 direct,
serial-via-Pi-GPIO. Writable settings (FC06) verified on
controllers + smart batteries; inverter/shunt writes need real
hardware in the lab (issues #185, #186).

### Victron (read-only by design)

| Category           | Models                                                                |
| ------------------ | --------------------------------------------------------------------- |
| Battery monitors   | SmartShunt 500/1000/2000 A, BMV-700/702/712                           |
| Charge controllers | SmartSolar MPPT (every model on BLE Instant Readout)                  |
| DC-DC              | Orion-Tr Smart, Orion XS                                              |
| AC chargers        | Blue Smart IP22 / IP65 / IP67                                         |
| Other              | Smart BatteryProtect, Smart Lithium, Lynx Smart BMS                   |
| Inverters          | Phoenix Inverter VE.Direct (small pure-sine, wired only)              |

Two read paths:

- **BLE Instant Readout**, default for every device above except
  the Phoenix Inverter. Broadcast, no pairing, per-device
  encryption key from VictronConnect.
- **VE.Direct (wired)**, alternative path for SmartShunt /
  SmartSolar MPPT / Phoenix Inverter. Use this in metal-van
  builds or anywhere BLE is unreliable. Cable is Victron's
  VE.Direct-to-USB (~£25) or a £12 DIY JST + FTDI rig. See
  [wired-setup.md](wired-setup.md).

Read-only either way. Heavy-Victron users live inside VRM and
will not switch. Our Victron customer is the mixed-stack builder
who wants one dashboard alongside their Renogy or JBD gear.
MultiPlus / Quattro inverters need VE.Bus, which is a different
physical layer plus an MK3-USB interface, deferred indefinitely.

### Other

| Vendor             | Coverage                          |
| ------------------ | --------------------------------- |
| JK BMS             | Read-only, BLE, every model in the JK B-series. |

## What's next (prioritised)

Ordered by `(persona impact) x (integration ease)`. Persona A is
the mixed-stack van builder; Persona B is the budget upgrader who
bought a cheap shunt as their first piece of telemetry. Both are
already paying personas in our pricing model.

### Tier 1, paying-persona unlocks (shipped in v0.1.25)

1. ✅ **JBD / Overkill Solar BMS** (#201). The BMS inside most
   sub-£500 LFP packs (Eco-Worthy, LiTime, Power Queen, many
   Vatrer SKUs, the JBD-direct-from-AliExpress crowd). Bluetooth,
   one well-documented protocol covers many rebrands. **Pending
   community validation.**
2. ✅ **Daly Smart BMS** (#202). Second-most-common BMS in
   budget LFP packs. BLE first; UART variant can follow when a
   customer asks. **Pending community validation.**
3. ✅ **EPEVER / EPSolar Tracer MPPT** (#203). The #1 budget
   MPPT in DIY van and cabin builds. Modbus RTU over USB-RS485
   with FC04 for live state. **Pending community validation.**

### Tier 2, cheap-shunt wedge (shipped in v0.1.25)

4. ✅ **AiLi shunt** (#204). Sub-£40 BLE shunt; the cheap
   shunt that ships in thousands of first-time DIY installs.
   **Pending community validation.**
5. ✅ **Junctek KH-F / KG-F shunt** (#205). Second-most-common
   cheap shunt. ASCII protocol over BLE; merges r50 / r51 / r53
   responses into one canonical shunt-shaped output. **Pending
   community validation.**
6. ✅ **Battle Born / LiTime / Power Queen LFP**. Covered
   automatically by the JBD driver, they're JBD-rebranded
   inside. No additional code.

### Tier 3, Persona A "I have a generator"

7. **MPP Solar / Voltronic clones (PIP-MS, Axpert,
   EASUN)**. The off-grid inverter segment in DIY. RS-232 /
   RS-485, well-documented protocol, huge installed base in
   solar forums.
8. **Sterling Power BB1230 / WildSide DC-DC**. UK van builder
   favourite. Bluetooth.
9. **REDARC BCDC**. Australian + AU-influenced van market
   dominant. Bluetooth on the newer models.

### Tier 4, long tail / nice-to-have

10. **Bogart TriMetric battery monitor**. Older, niche,
    RS-485, but loyal user base in legacy off-grid cabin
    installs.
11. **Morningstar TriStar / SunSaver MPPT**. Older but still
    installed in cabin setups, Modbus.
12. **REC Active BMS**. Yacht / high-end RV, small but
    vocal user base.

## Out of scope (explicit)

- **Victron MultiPlus / Phoenix (VE.Bus inverters)**. Needs a
  Cerbo GX or MK3-USB adapter and a non-trivial protocol stack.
  Owners of these are already on VRM and aren't our customer.
- **EcoFlow / Bluetti / Anker integrated stations**. Proprietary
  cloud-only. Would need API partnerships, not driver work.
- **Schneider XW Pro / Outback Radian**. Large commercial
  inverters, certification overhead, wrong customer.
- **Magnum MS-series**. Proprietary RS-485 dialect that isn't
  well documented. Reopen if a customer pays for it.

## How priorities change

Three signals shift the order:

- **Discovery telemetry (#129)**. Customers who opt in send
  fingerprints of unrecognised devices. Anything that shows up
  in the dashboard's discovery feed three or more times jumps
  up a tier. The dev team's discovery dashboard is the source
  of truth here; this page is a snapshot.
- **Support tickets**. Two support tickets asking "do you
  support X" promotes X to the active queue regardless of tier.
- **Paying-customer requests**. A paying WattPost Cloud customer
  asking for a driver beats every Tier 4 item.

## How to add a vendor

See [adding-a-vendor.md](adding-a-vendor.md) for the driver
contract, registry wiring, and the discovery-telemetry hook
that makes the new device show up in the setup wizard.
