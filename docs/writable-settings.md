# Writable settings (charge profiles, voltage cutoffs)

Most of WattPost reads. Some of it writes. **Settings → Device
settings** lists every parameter on every paired device that the
daemon can change for you — typically charger profile, absorption /
float voltages, low-voltage disconnect, load output, eco mode.

Writes are guarded: each change shows a confirmation modal with the
old and new value, and the daemon reads the device back after each
write so you can see the new value live before closing the modal.

## Where writes go

| Vendor | Mechanism | Read-back? |
|---|---|---|
| Renogy MPPT (Rover, Wanderer, etc.) | Modbus FC06 over BT-2 | Yes — FC03 within 500 ms |
| Renogy DC-DC (DCC50S etc.) | Modbus FC06 over BT-2 | Yes |
| Renogy inverter-chargers | Modbus FC06 over BT-2 | Yes |
| Renogy smart shunt | Modbus FC06 over BT-2 | Yes |
| JK BMS | Read-only — no writes by design | n/a |
| Victron | Read-only — Cerbo / VRM is the right tool for writes | n/a |

If you mix Victron + Renogy on one appliance, you can still write
to the Renogy half. The Victron half stays read-only, which is the
right answer for a multi-vendor monitor — Victron has its own
mature write path via VictronConnect / Cerbo.

## What you can change today

### Renogy Rover / Wanderer MPPT

- **Battery type** — flooded / sealed / gel / lithium / custom
- **Absorption (boost) voltage** — 12.0–16.0 V
- **Float voltage** — 12.0–15.0 V
- **Low-voltage disconnect** — 10.0–12.8 V
- **Low-voltage reconnect** — 10.5–13.5 V
- **Load output** — on / off (covered separately in [Adding devices](/docs/devices))

### Renogy DCC50S / DCC30S DC-DC

The DCC50S shares charger silicon with the Rover family — Renogy
reuses the same `0xE004 / 0xE008..0xE00C` register block across
both products, so the writable settings are identical:

- **Battery type** — flooded / sealed / gel / lithium / custom
- **Absorption (boost) voltage** — 12.0–16.0 V
- **Float voltage** — 12.0–15.0 V
- **Low-voltage disconnect / reconnect** — same ranges as the Rover

## Pending hardware validation

These drivers ship today as read-only and will pick up writable
settings once they've been verified against real hardware in the
lab (no register guessing — too easy to brick a customer's gear):

- **Renogy inverter-chargers** (1000 W / 2000 W / 3000 W) — AC charger enable, eco mode, output V/Hz
- **Renogy smart shunt** (RBM-S100 / S300 / S500) — battery capacity, full-charge voltage threshold for SoC sync
- **Renogy smart lithium batteries** — most parameters are BMS-side only, no documented user-writable surface

## The confirm modal

Click any value to open the modal. It shows:

- The setting's current device-reported value
- The new value you've entered
- A short description of what the setting controls
- The mechanism (FC06 over BT-2, BLE characteristic write)
- A 5-second countdown before the **Confirm** button enables, so
  you have a beat to check what you're about to change

After pressing Confirm, the daemon:

1. Issues the write
2. Waits up to 500 ms for the device ack
3. Reads the register back
4. Compares — if read ≠ written, surfaces the discrepancy

The modal closes only after the read-back lands.

## BT-2 ack quirk

Renogy's BT-2 dongles **swallow FC06 write acks** — the daemon
issues the write, the device performs it, but the dongle never
forwards an ack back over BLE. We treat the absence of an explicit
NAK as "probably succeeded" and rely entirely on the FC03 read-back
to confirm.

You'll occasionally see a brief "write timed out, verifying…"
message before the modal confirms. That's the read-back catching up
with a successful but ack-less write. If the read-back ALSO fails,
the write didn't take and you'll see a red error in the modal.

USB-RS485 transports don't have this quirk — acks come back
immediately.

## Things you can't change

- **Anything on a Victron device** — by design. Use VictronConnect.
- **Anything on a JK BMS over BLE** — JK doesn't expose writes on
  the BLE characteristic.
- **Modbus slave ID / baud rate / address** — too easy to brick a
  device and not enough operator demand. Edit the unit on its own
  buttons.

## Rolling back a bad change

Each successful write is logged to `/var/lib/wattpost/audit.log`
with the previous and new value. Open **Settings → Device settings →
History** to see every change with one-click revert (writes the
previous value back, with the same confirm flow).

## Coming next

The first round shipped covers Renogy Rover; the wider fan-out
across DCC, inverter, smart battery and smart shunt is mid-flight.
If a parameter you want isn't editable yet, email
[support@wattpost.io](mailto:support@wattpost.io) — the priority
order is operator-driven.
