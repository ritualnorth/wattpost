# Adding devices

WattPost reads telemetry from devices that speak Modbus over BLE
(via a Renogy BT-1 / BT-2 dongle today; Victron / JK-BMS coming).

## Already paired one BT-2 dongle?

For adding *more devices on the same dongle* (extra battery packs,
a second charge controller), use the **Setup wizard**:

1. Settings → Devices & setup → **Run device setup**.
2. Pick the transport (e.g. `hub_bt`).
3. Tap **Scan**. The wizard tries the common Renogy slave IDs
   (1, 16, 32–36, 48–55, 96, 97) on the live BLE link.
4. For each device that responds with a model string, tap **+ Add**,
   give it a label, **Save**.

The new entries are written into `/etc/wattpost/config.yaml`
atomically (backup kept at `.bak`). After adding, hit Settings →
System → **Restart daemon** to start polling them.

## Adding a brand-new BLE transport

Adding a new BT-2 dongle (a second hub) still needs YAML for now.
SSH into the Pi and edit `/etc/wattpost/config.yaml`:

```yaml
transports:
  - id: hub_bt_2
    type: ble_modbus
    address: AA:BB:CC:DD:EE:FF       # the BT-2's BLE MAC
```

Then hit Restart daemon. Future versions will add a BLE-scan flow
for this too.

## Removing a device

Same place — Setup wizard. The "+ Add" button is replaced with a
trash icon for already-configured devices. (Coming soon — until
then, edit `config.yaml` directly.)

## Supported vendors / kinds

| Vendor | Kind | Notes |
|---|---|---|
| renogy | charge_controller | Rover MPPT family |
| renogy | smart_battery | RBT100LFP12S-G1 and siblings |
| (planned) victron | smart_shunt | BMV-712 / SmartShunt 500A |
| (planned) jk | bms | JK-BMS over BLE |
| (planned) renogy | inverter | Renogy AC inverter via BLE |
