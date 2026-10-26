"""The Renogy BLE transport must pause the Govee and Ruuvi passive
scanners while it connects, and resume them afterwards even when the
connect fails; otherwise BlueZ answers org.bluez.Error.InProgress."""
import asyncio

import pytest

from solar_monitor.transport import ble_govee_advertise, ble_ruuvi_advertise, ble_modbus
from solar_monitor.transport.base import TransportError


class _FakeScanner:
    def __init__(self, was_running: bool) -> None:
        self.was_running = was_running
        self.calls: list[str] = []

    async def pause(self) -> bool:
        self.calls.append("pause")
        return self.was_running

    async def resume(self) -> None:
        self.calls.append("resume")


@pytest.fixture
def scanners(monkeypatch):
    govee, ruuvi = _FakeScanner(True), _FakeScanner(False)
    monkeypatch.setattr(ble_govee_advertise, "_scanner", lambda: govee)
    monkeypatch.setattr(ble_ruuvi_advertise, "_scanner", lambda: ruuvi)

    async def _not_found(address, timeout=None, **kw):
        return None

    monkeypatch.setattr(ble_modbus.BleakScanner, "find_device_by_address", _not_found)
    return govee, ruuvi


def test_passive_scanners_paused_and_resumed_on_failed_connect(scanners):
    govee, ruuvi = scanners
    t = ble_modbus.BleModbusTransport("t", "AA:BB:CC:DD:EE:FF", discovery_timeout=0.1)
    with pytest.raises(TransportError):
        asyncio.run(t._open_once())
    assert govee.calls == ["pause", "resume"]
    # Only scanners that were running are resumed.
    assert ruuvi.calls == ["pause"]
