"""BLE GATT transport for Junctek KH-F / KG-F smart shunts (#205).

Junctek's BLE shunts are the second-most-common cheap shunt in
budget van builds. Protocol is ASCII-framed:

  * Service UUID:    0000FFE0-0000-1000-8000-00805F9B34FB
  * Write / Notify:  0000FFE1-0000-1000-8000-00805F9B34FB

Commands look like `:R50=1,2,1,\\n` (request register-50, with two
fields). Responses look like `:r50=1,42,0,0,...,\\n`. Cells of
interest, per mpp-solar's junctek module + the dbus-serialbattery
LifePower4 reverse engineering:

  * `r50`  → voltage, current, direction, residual capacity, etc.
  * `r51`  → temperature, accumulated charge / discharge
  * `r53`  → SoC + time-to-empty

We send the requests on connect and then re-issue periodically;
the device replies on the same notify characteristic. Replies are
text + LF-terminated, so the parser splits on \\n.

Read-only. Junctek does expose write commands (set capacity, zero
counters) but those need real-hardware validation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak import BleakClient, BleakScanner

from .base import Transport, TransportError
from .registry import register_transport


log = logging.getLogger(__name__)


JUNCTEK_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
JUNCTEK_RW_UUID      = "0000ffe1-0000-1000-8000-00805f9b34fb"

POLL_REQUESTS = (b":R50=1,2,1,\n", b":R51=1,2,1,\n", b":R53=1,2,1,\n")

STALE_AFTER_SECONDS = 60.0


def parse_response(line: str) -> dict[str, Any] | None:
    """Parse one response line into a {key: value} dict. Returns
    None if the line isn't a recognisable Junctek response."""
    line = line.strip()
    if not line.startswith(":r") or "=" not in line:
        return None
    head, _, rest = line.partition("=")
    cmd = head[2:]  # drop ":r"
    parts = [p for p in rest.rstrip(",").split(",") if p]
    if not parts:
        return None
    # All Junctek fields arrive as ASCII decimal integers. Reference
    # field positions come from mpp-solar's junctek module.
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    out: dict[str, Any] = {}
    if cmd == "50" and len(nums) >= 5:
        # Field 0: voltage  (0.01 V)
        # Field 1: current  (0.01 A, unsigned magnitude)
        # Field 2: direction (0 = charging, 1 = discharging)
        # Field 3: residual capacity (0.001 Ah)
        # Field 4: bank capacity (Ah, integer)
        voltage = nums[0] / 100.0
        magnitude = nums[1] / 100.0
        # Junctek field 2: 0=charging, 1=discharging.
        # WattPost convention: +ve current = charging.
        current = magnitude if nums[2] == 0 else -magnitude
        out.update({
            "voltage_v": voltage,
            "current_a": current,
            "remaining_ah": nums[3] / 1000.0,
            "bank_capacity_ah": float(nums[4]),
        })
    elif cmd == "51" and len(nums) >= 3:
        # Field 0: temperature (offset 100 by spec, so temp_c = raw - 100)
        # Field 1: cumulative charge Ah
        # Field 2: cumulative discharge Ah
        out["temperature_c"] = nums[0] - 100
        out["cumulative_charge_ah"] = nums[1] / 1000.0
        out["cumulative_discharge_ah"] = nums[2] / 1000.0
    elif cmd == "53" and len(nums) >= 3:
        # Field 0: SoC (0-100)
        # Field 1: time-to-go in minutes (0xFFFFFFFF on undefined)
        # Field 2: power (W, integer)
        soc = nums[0]
        ttg = nums[1]
        out["soc_pct"] = float(soc)
        if ttg < 0xFFFFFF:
            out["time_to_go_minutes"] = int(ttg)
        out["power_w"] = float(nums[2])
    return out or None


class BleJunctekTransport(Transport):
    def __init__(self, id: str, address: str,
                 discovery_timeout: float = 20.0) -> None:
        self.id = id
        self.address = address.upper()
        self.discovery_timeout = discovery_timeout
        self._client: BleakClient | None = None
        self._buf = bytearray()
        # Last value per field (merged across r50 / r51 / r53). Single
        # flat dict so the driver doesn't have to know about the
        # multiple Junctek registers behind the scenes.
        self._latest: dict[str, Any] = {}
        self._latest_at: float = 0.0
        self._poll_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        log.info("[%s] discovering Junctek shunt %s", self.id, self.address)
        dev = await BleakScanner.find_device_by_address(
            self.address, timeout=self.discovery_timeout,
        )
        if dev is None:
            raise TransportError(
                f"Junctek shunt {self.address} not advertising within "
                f"{self.discovery_timeout}s"
            )
        self._client = BleakClient(dev)
        await self._client.connect()
        if not self._client.is_connected:
            raise TransportError(f"failed to connect to Junctek {self.address}")
        try:
            await self._client.start_notify(JUNCTEK_RW_UUID, self._on_notify)
        except Exception as e:
            raise TransportError(f"start_notify failed: {e}")
        self._stop.clear()
        self._poll_task = asyncio.get_event_loop().create_task(self._poll_loop())
        log.info("[%s] connected; poll loop started", self.id)

    async def close(self) -> None:
        self._stop.set()
        if self._poll_task is not None:
            try:
                await asyncio.wait_for(self._poll_task, timeout=1.5)
            except asyncio.TimeoutError:
                self._poll_task.cancel()
            self._poll_task = None
        if self._client is not None:
            try:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(JUNCTEK_RW_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
            finally:
                self._client = None
                self._buf.clear()

    async def request(self, frame: bytes, expected_response_len: int,
                      timeout: float = 5.0) -> bytes:
        raise TransportError(
            f"{self.id}: request() is unsupported on ble_junctek, "
            "drivers must override poll() and use get_latest()"
        )

    def get_latest(self) -> dict[str, Any] | None:
        if not self._latest:
            return None
        if time.time() - self._latest_at > STALE_AFTER_SECONDS:
            return None
        return dict(self._latest)

    def last_frame_age_s(self) -> float | None:
        if self._latest_at == 0.0:
            return None
        return time.time() - self._latest_at

    async def _poll_loop(self) -> None:
        assert self._client is not None
        while not self._stop.is_set():
            for req in POLL_REQUESTS:
                try:
                    await self._client.write_gatt_char(
                        JUNCTEK_RW_UUID, req, response=False,
                    )
                except Exception as e:
                    log.info("[%s] write %r failed: %s",
                             self.id, req[:5].decode(), e)
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.5)

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._buf.extend(data)
        # Slice on LF. Junctek lines are short (~80 bytes) so a
        # single notify usually carries one complete line; we still
        # buffer in case the firmware fragments.
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                return
            line = self._buf[:nl].decode("ascii", errors="ignore")
            del self._buf[:nl + 1]
            parsed = parse_response(line)
            if parsed is None:
                continue
            self._latest.update(parsed)
            self._latest_at = time.time()


@register_transport("ble_junctek")
def _factory(cfg: dict) -> BleJunctekTransport:
    return BleJunctekTransport(
        id=cfg["id"],
        address=cfg["address"],
        discovery_timeout=float(cfg.get("discovery_timeout", 20.0)),
    )
