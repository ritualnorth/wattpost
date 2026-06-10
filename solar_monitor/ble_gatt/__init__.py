"""BLE GATT peripheral — the appliance as a phone-facing Bluetooth device.

The daemon already acts as a BLE *central* (polling Renogy/Victron). This
package adds the *peripheral* role on the same radio: it advertises a
WattPost GATT service so the mobile app can connect over Bluetooth with no
shared network — for off-grid setup (WiFi provisioning) and off-grid daily
status, the bottom-but-one rung of the app's local-first connection ladder.

Built directly on dbus_fast (already a dependency via bleak) + BlueZ over the
system bus, so there's no new dependency and the privileged surface stays
auditable. Coexistence with the central polling on the single radio was
spiked first (advertising + transient connections showed no degradation);
the service can pause polling during an active session if needed.
"""
from .service import BleGattService, WP_SVC_UUID

__all__ = ["BleGattService", "WP_SVC_UUID"]
