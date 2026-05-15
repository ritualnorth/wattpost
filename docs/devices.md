# Adding devices

WattPost reads telemetry from devices that speak Modbus over BLE
(via a Renogy BT-1 / BT-2 dongle today; Victron and JK BMS are on
the roadmap).

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

Edit `config.yaml` directly (`/etc/wattpost/config.yaml` on the Pi,
`./wattpost-config/config.yaml` on the Docker host) — remove the
`devices:` entry, restart the daemon. A UI "remove" button is on
the roadmap.

## Supported vendors / kinds

| Vendor | Kind | Notes |
|---|---|---|
| renogy | charge_controller | Rover MPPT family |
| renogy | smart_battery | RBT100LFP12S-G1 and siblings |
| (roadmap) victron | smart_shunt | BMV-712 / SmartShunt 500A |
| (roadmap) jk | bms | JK-BMS over BLE |
| (roadmap) renogy | inverter | Renogy AC inverter via BLE |
