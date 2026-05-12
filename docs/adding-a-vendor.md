# Adding a vendor

The framework is designed so adding a Modbus-speaking vendor (Renogy clone,
Epever, SRNE-based controller, generic Modbus shunt) is a self-contained
folder drop. Non-Modbus vendors (Victron broadcast, JK-BMS GATT) require an
additional transport type — see the section at the bottom.

## Steps for a Modbus vendor

### 1. Create the package

```
solar_monitor/vendors/myvendor/
    __init__.py
    rover.py            # or whatever device kinds you implement
    smart_battery.py
    _util.py            # optional shared helpers
```

### 2. Write one driver per device kind

A device kind = "one type of device" (`charge_controller`, `smart_battery`,
`shunt`, `inverter`). One driver class per kind. Each driver declares a list
of `Section`s (one Modbus read each) and a parser function per section.

```python
# solar_monitor/vendors/myvendor/charge_controller.py
from ..base import DeviceDriver, Section


def _parse_status(bs: bytes) -> dict:
    # bs is the raw response: [slave_id, fc, byte_count, ...data, crc, crc]
    return {
        "battery_voltage_v": int.from_bytes(bs[5:7], "big") * 0.1,
        "battery_current_a": int.from_bytes(bs[7:9], "big") * 0.01,
        # ... etc, in SI units
    }


class MyVendorMPPT(DeviceDriver):
    vendor_id = "myvendor"
    device_kind = "charge_controller"

    @property
    def sections(self) -> list[Section]:
        return [
            Section(register=256, word_count=10, parser=_parse_status, name="status"),
        ]
```

The base class `poll()` walks the sections, sends each as a Modbus function-3
read, validates the response, and merges parsed dicts. If your protocol
needs anything different (different function code, write-then-read, custom
framing), override `poll()` directly.

### 3. Register the vendor

```python
# solar_monitor/vendors/myvendor/__init__.py
from ..base import VendorInfo
from ..registry import register_vendor
from .charge_controller import MyVendorMPPT

INFO = VendorInfo(
    id="myvendor",
    display_name="My Vendor",
    description="…",
)

register_vendor(
    info=INFO,
    drivers={
        "charge_controller": MyVendorMPPT,
    },
)
```

### 4. Wire it into the package init

One line in `solar_monitor/vendors/__init__.py`:

```python
from . import renogy   # existing
from . import myvendor  # add this
```

That's it. The orchestrator will route any `vendor: myvendor` device from the
YAML config to your driver.

## Field naming conventions

Output dicts should use SI-unit suffixes so the UI can be vendor-agnostic:

| Quantity | Unit | Key suffix |
|---|---|---|
| Voltage | volts | `_v` |
| Current | amps | `_a` |
| Power | watts | `_w` |
| Energy | watt-hours | `_wh` |
| Charge | amp-hours | `_ah` |
| Temperature | celsius | `_c` |
| Percentage | 0–100 | `_pct` (or none for SoC which is canonically a percent) |
| Frequency | hertz | `_hz` |

Examples already in the Renogy driver: `battery_voltage_v`, `pv_power_w`,
`charging_ah_today`, `controller_temperature_c`.

The UI layer is responsible for converting to user-preferred units (°F, etc.).
Never put unit conversion in a driver.

## Common normalized keys

When a value has a natural home across vendors, use these keys so dashboards
"just work":

- `battery_voltage_v`, `battery_current_a`, `battery_percentage`
- `pv_voltage_v`, `pv_current_a`, `pv_power_w`
- `load_voltage_v`, `load_current_a`, `load_power_w`
- `cell_voltage_N_v` (0-indexed), `cell_count`
- `temperature_N_c`, `temperature_sensor_count`
- `model` (string)
- `charging_state` (string enum: `mppt|boost|float|equalize|off|…`)

Vendor-specific data is fine; just use a more specific key name.

## Non-Modbus protocols (Victron, JK-BMS, etc.)

The current `Transport.request(frame, expected_len)` interface is
Modbus-flavored. Protocols that don't fit (Victron's BLE Instant Readout
advertisements, JK-BMS's GATT framing) need either:

1. A new transport type that exposes its own interface (e.g.
   `BroadcastTransport.subscribe(address, callback)`), and drivers that
   override `poll()` to use it, **or**
2. A driver that opens its own BLE handle directly via `bleak` and bypasses
   the Modbus pipeline.

Pick option 1 if multiple vendors will share the new protocol family.
Pick option 2 for one-off bespoke protocols.

See `solar_monitor/vendors/victron/PROTOCOL.md` and
`solar_monitor/vendors/jkbms/PROTOCOL.md` for the relevant protocol notes
once those packages exist.
