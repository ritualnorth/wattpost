# Adding devices

WattPost reads telemetry from three kinds of Bluetooth source:

- **Renogy** — Modbus over BLE via a [BT-1 or BT-2 dongle](/docs/supported-hardware) plugged into the device's comms port
- **Victron** — passive **Instant Readout** BLE advertisements, one encryption key per device
- **JK BMS** — its own advertised BLE service, no dongle, no key

Everything below works the same on the SD-card install and the
Docker install. No config files to hand-edit.

## Step 1 — Add a BLE transport

A "transport" is one BT-2 (or compatible) dongle the daemon talks
to. Each dongle is one transport; you can pair multiple if you have
more than one site / RV.

1. **Settings → Setup**. The banner at the top shows whether the
   daemon can see a Bluetooth adapter. Green ⇒ ready. Red ⇒ check
   the dongle / Docker passthrough first.
2. **Step 1 → Find my dongle**. The wizard scans BLE advertisements
   for ~8 seconds and lists every device it sees. Renogy BT-2
   dongles advertise as `BT-TH-XXXXXXXX` and get a "looks like a
   Renogy BT-2" hint badge in the result row.
3. Click **Use this** on the right row. The transport is written to
   `/etc/wattpost/config.yaml` and a "restart daemon" banner appears.
4. **Settings → System → Restart daemon** (or `docker compose restart
   wattpost` if running in Docker).

## Step 2 — Scan for devices on a transport

Once you have at least one transport configured:

1. **Settings → Setup → Step 2 Scan for devices** is now active.
2. Hit **Scan**. The wizard probes the standard Renogy slave IDs
   (1, 16, 32–36, 48–55, 96, 97) on the live BLE link.
3. Each device that responds is shown with its model + slave ID.
4. Click **+ Add** on each one you want to poll, give it a label
   (e.g. "Main MPPT", "Pack #1"), **Save**.
5. Restart daemon once more. Live data starts flowing within ~10 s.

The new entries are written into `config.yaml` atomically (the
previous version is preserved at `config.yaml.bak`).

## Removing a device

**Settings → Setup → Devices** lists every paired device with a
**Remove** button next to each. The wizard rewrites `config.yaml`
atomically and prompts to restart the daemon.

## Supported vendors / kinds

| Vendor | Kind | Notes |
|---|---|---|
| renogy | charge_controller | Rover / Wanderer / Adventurer / Voyager MPPT family |
| renogy | dcdc | DCC50S / DCC30S / DCC25S / DCC15S DC-DC + MPPT combos |
| renogy | smart_battery | RBT100LFP12S-G1 and LFP siblings |
| renogy | inverter | 1000 W / 2000 W / 3000 W pure-sine inverter-chargers |
| renogy | shunt | RBM-S100 / S300 / S500 Battery Monitor + Shunt |
| victron | shunt | SmartShunt 500 / 1000 / 2000 A, BMV-700 / 702 / 712 (BLE) |
| victron | charge_controller | SmartSolar MPPT family (BLE Instant Readout) |
| victron | dcdc | Orion-Tr Smart / Orion XS DC-DC chargers |
| victron | ac_charger | Blue Smart IP22 / IP65 chargers |
| victron | smart_battery | SmartLithium batteries |
| victron | bms | LynxSmartBMS |
| victron | load_disconnect | SmartBatteryProtect |
| jkbms | bms | JK-B series BMS via BLE |
