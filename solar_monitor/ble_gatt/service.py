"""BlueZ GATT peripheral for the WattPost appliance (dbus_fast, async).

Hand-rolled on dbus_fast (already a dependency via bleak) — no extra dep,
full control of a security-relevant subsystem that's going open-source.

Exports a GATT application (service + characteristic) and an LE
advertisement on the system bus and registers both with BlueZ. Requires
bluetoothd to run with `--experimental` (GATT-server read dispatch is gated
behind it on BlueZ); the appliance image/install enables that.

Lifecycle mirrors the other long-running scheduler services: start()/stop().
No-ops where dbus_fast / a system bus / BlueZ isn't present (Docker, dev).

Milestone 1: advertise + a read/notify Status characteristic. Milestone 2
adds scan/join characteristics bridging to the WiFi backend.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

WP_SVC_UUID        = "9a8b1e00-7761-7474-706f-737400000001"
WP_STATUS_CHR_UUID = "9a8b1e00-7761-7474-706f-737400000002"
LOCAL_NAME = "WattPost"

BLUEZ          = "org.bluez"
GATT_MANAGER   = "org.bluez.GattManager1"
LE_ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
DBUS_OM_IFACE  = "org.freedesktop.DBus.ObjectManager"

APP_ROOT  = "/com/wattpost/gatt"
SVC_PATH  = APP_ROOT + "/service0"
CHR_PATH  = SVC_PATH + "/char0"
ADV_PATH  = "/com/wattpost/adv0"

try:
    from dbus_fast import BusType, Variant, PropertyAccess
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method, dbus_property
    _DBUS_OK = True
except Exception:  # pragma: no cover
    _DBUS_OK = False


if _DBUS_OK:

    class _Advertisement(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.LEAdvertisement1")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return [WP_SVC_UUID]

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> "s":
            return LOCAL_NAME

        @dbus_property(access=PropertyAccess.READ)
        def Includes(self) -> "as":
            return ["tx-power"]

        @method()
        def Release(self):  # noqa: N802
            log.debug("advertisement released by BlueZ")

    class _Service(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.GattService1")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return WP_SVC_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

    class _StatusCharacteristic(ServiceInterface):
        def __init__(self, provider: Callable[[], dict[str, Any]]) -> None:
            super().__init__("org.bluez.GattCharacteristic1")
            self._provider = provider
            self._notifying = False
            self._value = self._encode()

        def _encode(self) -> bytes:
            try:
                data = self._provider() or {}
            except Exception:
                log.exception("status provider failed")
                data = {}
            return json.dumps(data, separators=(",", ":")).encode("utf-8")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return WP_STATUS_CHR_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> "o":
            return SVC_PATH

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return ["read", "notify"]

        @dbus_property(access=PropertyAccess.READ)
        def Descriptors(self) -> "ao":
            return []

        @dbus_property(access=PropertyAccess.READ)
        def Notifying(self) -> "b":
            return self._notifying

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":  # noqa: N802
            self._value = self._encode()
            off = 0
            try:
                ov = options.get("offset") if options else None
                if ov is not None:
                    off = int(ov.value if hasattr(ov, "value") else ov)
            except Exception:
                off = 0
            log.info("ReadValue(offset=%d) -> %d bytes", off, len(self._value))
            return self._value[off:]

        @method()
        def StartNotify(self):  # noqa: N802
            self._notifying = True
            log.debug("status notify started")

        @method()
        def StopNotify(self):  # noqa: N802
            self._notifying = False

        def push(self) -> None:
            self._value = self._encode()
            if self._notifying:
                self.emit_properties_changed({"Value": self._value})

    class _Application(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(DBUS_OM_IFACE)

        @method()
        def GetManagedObjects(self) -> "a{oa{sa{sv}}}":  # noqa: N802
            return {
                SVC_PATH: {
                    "org.bluez.GattService1": {
                        "UUID": Variant("s", WP_SVC_UUID),
                        "Primary": Variant("b", True),
                    }
                },
                CHR_PATH: {
                    "org.bluez.GattCharacteristic1": {
                        "UUID": Variant("s", WP_STATUS_CHR_UUID),
                        "Service": Variant("o", SVC_PATH),
                        "Flags": Variant("as", ["read", "notify"]),
                        "Descriptors": Variant("ao", []),
                    }
                },
            }


class BleGattService:
    def __init__(
        self,
        status_provider: Callable[[], dict[str, Any]],
        *,
        adapter: str = "hci0",
        notify_interval_s: float = 5.0,
    ) -> None:
        self._provider = status_provider
        self._adapter = adapter
        self._notify_interval = notify_interval_s
        self._bus = None
        self._char = None
        self._notify_task: asyncio.Task | None = None
        self._available = _DBUS_OK
        self._registered = False

    async def start(self) -> None:
        if not self._available:
            log.info("ble_gatt: dbus_fast unavailable, peripheral disabled")
            return
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            log.warning("ble_gatt: no system bus (%s); peripheral disabled", e)
            self._available = False
            return

        svc = _Service()
        self._char = _StatusCharacteristic(self._provider)
        app = _Application()
        adv = _Advertisement()
        self._bus.export(SVC_PATH, svc)
        self._bus.export(CHR_PATH, self._char)
        self._bus.export(APP_ROOT, app)
        self._bus.export(ADV_PATH, adv)

        adapter_path = f"/org/bluez/{self._adapter}"
        try:
            mgr = await self._bluez_iface(adapter_path, GATT_MANAGER)
            await mgr.call_register_application(APP_ROOT, {})
            adv_mgr = await self._bluez_iface(adapter_path, LE_ADV_MANAGER)
            await adv_mgr.call_register_advertisement(ADV_PATH, {})
        except Exception as e:
            log.warning("ble_gatt: BlueZ registration failed (%s); peripheral disabled", e)
            self._available = False
            return

        self._registered = True
        self._notify_task = asyncio.create_task(self._notify_loop(), name="ble-gatt-notify")
        log.info("ble_gatt: advertising %s as %r on %s", WP_SVC_UUID, LOCAL_NAME, self._adapter)

    async def _bluez_iface(self, path: str, iface: str):
        introspection = await self._bus.introspect(BLUEZ, path)
        obj = self._bus.get_proxy_object(BLUEZ, path, introspection)
        return obj.get_interface(iface)

    async def _notify_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._notify_interval)
                if self._char is not None:
                    self._char.push()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("ble_gatt: notify loop crashed")

    async def stop(self) -> None:
        if self._notify_task is not None:
            self._notify_task.cancel()
            self._notify_task = None
        if self._bus is not None and self._registered:
            adapter_path = f"/org/bluez/{self._adapter}"
            try:
                mgr = await self._bluez_iface(adapter_path, GATT_MANAGER)
                await mgr.call_unregister_application(APP_ROOT)
                adv_mgr = await self._bluez_iface(adapter_path, LE_ADV_MANAGER)
                await adv_mgr.call_unregister_advertisement(ADV_PATH)
            except Exception:
                log.debug("ble_gatt: unregister on stop failed")
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
        self._registered = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _n = {"i": 0}

    def _demo_status() -> dict[str, Any]:
        _n["i"] += 1
        return {"alive": True, "soc_pct": 100, "power_w": 248, "alerts": 0, "tick": _n["i"]}

    async def _main() -> None:
        svc = BleGattService(_demo_status, notify_interval_s=3.0)
        await svc.start()
        print("ble_gatt self-test running; Ctrl-C to stop")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await svc.stop()

    asyncio.run(_main())
