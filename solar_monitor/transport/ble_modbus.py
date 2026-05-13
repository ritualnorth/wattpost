"""BLE transport for Renogy BT-1 / BT-2 style dongles.

These dongles expose Modbus RTU as a transparent pipe over two GATT
characteristics: write requests to FFD1, read responses via FFF1 notifications.
The same protocol is used by other vendors (Epever, some SRNE clones), so this
transport is potentially reusable beyond Renogy.

One transport == one BLE link == one BT module. Multiple downstream Modbus
device IDs share the link.
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner

from .base import Transport, TransportError, TransportTimeout
from .registry import register_transport

log = logging.getLogger(__name__)

# Default GATT UUIDs used by Renogy BT-1/BT-2 and several lookalikes.
DEFAULT_WRITE_CHAR = "0000ffd1-0000-1000-8000-00805f9b34fb"
DEFAULT_NOTIFY_CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"

# How long to wait for the BT module to advertise during discovery.
DEFAULT_DISCOVERY_TIMEOUT = 20.0


class BleModbusTransport(Transport):
    """Modbus-over-BLE for Renogy-style dongles."""

    def __init__(
        self,
        id: str,
        address: str,
        write_char: str = DEFAULT_WRITE_CHAR,
        notify_char: str = DEFAULT_NOTIFY_CHAR,
        discovery_timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    ) -> None:
        self.id = id
        self.address = address.upper()
        self.write_char = write_char
        self.notify_char = notify_char
        self.discovery_timeout = discovery_timeout

        self._client: BleakClient | None = None
        # One in-flight request at a time; serialized by _lock.
        self._lock = asyncio.Lock()
        self._buf = bytearray()
        self._expected_len = 0
        self._got_response: asyncio.Event = asyncio.Event()

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        try:
            await self._open_once()
            return
        except TransportError as e:
            # Most common cause of a "not advertising" timeout right
            # after the daemon restarts is BlueZ still holding the
            # previous Python process's connection — the dongle won't
            # re-advertise until that's cleared. Kick it via DBus and
            # try one more time before giving up.
            log.warning("[%s] %s — trying to clear stale BlueZ state",
                        self.id, e)
            try:
                await self._bluez_force_disconnect()
            except Exception:
                pass
            await self._open_once()

    async def _open_once(self) -> None:
        log.info("[%s] discovering %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout
        )
        if dev is None:
            raise TransportError(
                f"BLE device {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to {self.address}")
        await self._client.start_notify(self.notify_char, self._on_notify)
        log.info("[%s] connected", self.id)

    async def _bluez_force_disconnect(self) -> None:
        """When BlueZ thinks a device is still connected (to a Python
        process that's gone), the device won't re-advertise. Sending it
        an explicit disconnect via DBus releases the cached connection
        so the next discovery sees it again. Idempotent — no-op if the
        device isn't tracked or already disconnected.

        Implemented as a subprocess `bluetoothctl disconnect <MAC>`
        rather than reaching into bleak's DBus internals — works
        across bleak versions and easier to reason about."""
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "disconnect", self.address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
        # Brief pause so BlueZ surfaces the released state to its scan
        # cache before our next discover() reads it.
        await asyncio.sleep(1.0)

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            if self._client.is_connected:
                try:
                    await self._client.stop_notify(self.notify_char)
                except Exception:
                    pass
                await self._client.disconnect()
        finally:
            self._client = None

    def _on_notify(self, _char, data: bytearray) -> None:
        # Notifications arrive in MTU-sized chunks; accumulate until we
        # have the full expected frame, then unblock the waiter.
        self._buf.extend(data)
        if len(self._buf) >= self._expected_len:
            self._got_response.set()

    async def request(
        self,
        frame: bytes,
        expected_response_len: int,
        timeout: float = 5.0,
    ) -> bytes:
        if self._client is None or not self._client.is_connected:
            raise TransportError(f"transport {self.id} is not open")

        async with self._lock:
            self._buf = bytearray()
            self._expected_len = expected_response_len
            self._got_response = asyncio.Event()

            await self._client.write_gatt_char(
                self.write_char, bytearray(frame), response=False
            )

            try:
                await asyncio.wait_for(self._got_response.wait(), timeout=timeout)
            except asyncio.TimeoutError as e:
                raise TransportTimeout(
                    f"no response within {timeout}s on transport {self.id}"
                ) from e

            return bytes(self._buf[:expected_response_len])


@register_transport("ble_modbus")
def _factory(cfg: dict) -> BleModbusTransport:
    """Build a BleModbusTransport from a YAML config dict.

    Expected fields:
      id: stable id
      type: "ble_modbus"
      address: BT MAC (CC:45:A5:83:B7:42)
      write_char: (optional) override default write char UUID
      notify_char: (optional) override default notify char UUID
      discovery_timeout: (optional) seconds, default 20
    """
    return BleModbusTransport(
        id=cfg["id"],
        address=cfg["address"],
        write_char=cfg.get("write_char", DEFAULT_WRITE_CHAR),
        notify_char=cfg.get("notify_char", DEFAULT_NOTIFY_CHAR),
        discovery_timeout=cfg.get("discovery_timeout", DEFAULT_DISCOVERY_TIMEOUT),
    )
