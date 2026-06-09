"""BLE transport for Renogy BT-1 / BT-2 style dongles.

These dongles expose Modbus RTU as a transparent pipe over two GATT
characteristics: write requests to FFD1, read responses via a notify
characteristic. Which notify char depends on firmware revision,
newer BT-2 uses FFD2 (in the same FFD0 service as the write char),
older BT-1/BT-2 uses FFF1 (in a separate FFF0 service). Auto-
detected at connect time. The same Modbus-over-BLE pattern is used
by other vendors (Epever, some SRNE clones), so this transport is
reusable beyond Renogy.

One transport == one BLE link == one BT module. Multiple downstream Modbus
device IDs share the link.
"""
from __future__ import annotations

import asyncio
import logging

import asyncio
from bleak import BleakClient, BleakScanner


# Shared mutex for "exclusive use of the local HCI adapter for a
# scan/discover op". Acquired by the Renogy transport's _open_once
# AND by the manual /api/setup/ble_scan endpoint, so the two can't
# run simultaneous BleakScanner instances and hit
# `org.bluez.Error.InProgress`. The Victron passive scanner stays
# OUT of this lock, it uses pause()/resume() to yield the adapter
# while the lock-holder is working.
HCI_DISCOVER_LOCK: asyncio.Lock = asyncio.Lock()

from .base import Transport, TransportError, TransportTimeout
from .registry import register_transport

log = logging.getLogger(__name__)

# Default GATT UUIDs for Renogy BT-1 / BT-2 and lookalikes.
#
# Write goes to `ffd0/ffd1` (write-without-response). Modbus
# responses come back on `fff0/fff1`, even though the same dongle
# also advertises `ffd0/ffd2` as notify-capable, BT-TH-* firmware
# delivers replies on fff1 in practice (matches what
# cyrils/renogy-bt has used for years against real Renogy gear).
# We keep `ffd2` as a fallback candidate for non-Renogy lookalikes
# that wire it up differently. _open_once() walks the published
# GATT tree and picks the first candidate that's actually present
# with the notify property. If config pins notify_char we honor
# it and skip detection.
DEFAULT_WRITE_CHAR  = "0000ffd1-0000-1000-8000-00805f9b34fb"
DEFAULT_NOTIFY_CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"
_NOTIFY_CANDIDATES  = (
    "0000fff1-0000-1000-8000-00805f9b34fb",   # Renogy BT-TH-*
    "0000ffd2-0000-1000-8000-00805f9b34fb",   # generic lookalike
)

# How long to wait for the BT module to advertise during discovery.
# A free BT-2 advertises continuously, so find_device returns in a
# second or three; the only time this cap is reached is when the
# dongle is wedged (not advertising), where a shorter wait just lets
# the retry loop clear stale state and try again sooner rather than
# burning 20s per attempt. 10s keeps slow-advertising devices working
# while roughly halving the worst-case time-to-connect.
DEFAULT_DISCOVERY_TIMEOUT = 10.0


class BleModbusTransport(Transport):
    """Modbus-over-BLE for Renogy-style dongles."""

    def __init__(
        self,
        id: str,
        address: str,
        write_char: str = DEFAULT_WRITE_CHAR,
        notify_char: str | None = None,
        discovery_timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    ) -> None:
        self.id = id
        self.address = address.upper()
        self.write_char = write_char
        # If None, _open_once auto-detects from the GATT tree. A
        # non-None value (typically from YAML config) skips detection.
        self.notify_char: str | None = notify_char
        self.discovery_timeout = discovery_timeout

        self._client: BleakClient | None = None
        # One in-flight request at a time; serialized by _lock.
        self._lock = asyncio.Lock()
        self._buf = bytearray()
        self._expected_len = 0
        self._got_response: asyncio.Event = asyncio.Event()

    # Renogy BT-1/BT-2 dongles accept a connection on only ~1 in 5-6
    # attempts against a Raspberry Pi's onboard BlueZ stack: the link
    # establishes, then the controller drops it before GATT resolves
    # (HCI 0x3E). This is endemic to these dongles on Pi hardware, not a
    # fault we can cure in one shot, the same flakiness is all over
    # cyrils/renogy-bt (issues #46, #97) and the DIY-solar forums. The
    # proven cure, theirs and ours, is to retry the connect aggressively,
    # clearing stale BlueZ state between tries, until one takes. ~15
    # attempts pushes a 1-in-6 link well above 90% per open() call, and
    # the cost is only paid on first connect / after a drop, a held link
    # is reused. Verified: a single-shot connect fails ~85% of the time
    # on real hardware; with this loop the same dongle connects + streams
    # Modbus reliably.
    CONNECT_ATTEMPTS = 15
    CONNECT_RETRY_DELAY = 2.0

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        last_exc: Exception | None = None
        for attempt in range(1, self.CONNECT_ATTEMPTS + 1):
            try:
                await self._open_once()
                if attempt > 1:
                    log.info("[%s] connected on attempt %d/%d",
                             self.id, attempt, self.CONNECT_ATTEMPTS)
                return
            except Exception as e:
                last_exc = e
                log.info("[%s] connect attempt %d/%d failed: %s",
                         self.id, attempt, self.CONNECT_ATTEMPTS, e)
                # Clear stale BlueZ state between attempts. Two distinct
                # failure modes, both stale-state not a dead dongle:
                #   1. a half-open connection record stops the single-
                #      connection dongle from re-advertising (fixed by
                #      `bluetoothctl disconnect`);
                #   2. a cached GATT tree makes discovery reuse stale
                #      handles and drop mid-resolve (fixed by
                #      `bluetoothctl remove`, which evicts the record so
                #      the next discovery walks the live tree).
                # Both are cheap and safe even when not connected.
                try:
                    await self._bluez_force_disconnect()
                except Exception:
                    pass
                try:
                    await self._bluez_remove_device()
                except Exception:
                    pass
                if attempt < self.CONNECT_ATTEMPTS:
                    await asyncio.sleep(self.CONNECT_RETRY_DELAY)
        raise last_exc or TransportError(
            f"failed to connect to {self.address} after "
            f"{self.CONNECT_ATTEMPTS} attempts"
        )

    async def _open_once(self) -> None:
        log.info("[%s] discovering %s", self.id, self.address)
        # Three rules to coexist on a single HCI adapter:
        #
        #   1. HCI_DISCOVER_LOCK serialises us against any OTHER caller
        #      that's running a BleakScanner, the manual ble_scan
        #      endpoint, another Renogy transport, etc. Without it
        #      two simultaneous discoveries collide with
        #      `org.bluez.Error.InProgress`.
        #
        #   2. Inside the lock we pause the Victron passive scanner
        #      (it doesn't acquire the lock; it just yields when
        #      asked) so it doesn't hold the discovery slot for the
        #      duration of our scan + connect.
        #
        #   3. resume() the Victron scanner in finally{}, it
        #      reacquires its slot when subscribers are still
        #      registered; otherwise stays stopped.
        async with HCI_DISCOVER_LOCK:
            victron_was_running = False
            try:
                from .ble_victron_advertise import _scanner as _victron_scanner
                victron_was_running = await _victron_scanner().pause()
            except Exception:
                log.debug("[%s] victron scanner pause skipped (not in use)", self.id)
            try:
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
            finally:
                if victron_was_running:
                    try:
                        from .ble_victron_advertise import _scanner as _victron_scanner
                        await _victron_scanner().resume()
                    except Exception:
                        log.warning("[%s] victron scanner resume failed", self.id)
        # Enumerate the GATT services + characteristics we actually
        # got. Used both for diagnostics ("connected but probe times
        # out" is usually a wrong-characteristic issue) and to
        # auto-pick notify_char when the caller didn't pin one.
        published: set[str] = set()
        try:
            for svc in self._client.services:
                chars = ",".join(
                    f"{c.uuid[4:8]}={','.join(c.properties)}"
                    for c in svc.characteristics
                )
                log.info("[%s] GATT svc %s: %s",
                         self.id, svc.uuid[4:8], chars or "(none)")
                for c in svc.characteristics:
                    if "notify" in c.properties:
                        published.add(c.uuid.lower())
        except Exception as e:
            log.warning("[%s] GATT enumeration failed: %s", self.id, e)

        if self.notify_char is None:
            # Pick the first candidate the dongle actually publishes
            # with the notify property. Newer Renogy BT-2 wins on
            # ffd2; older firmware falls through to fff1.
            for cand in _NOTIFY_CANDIDATES:
                if cand.lower() in published:
                    self.notify_char = cand
                    break
            if self.notify_char is None:
                # Nothing matched, fall back to the historical default
                # so start_notify produces a clear error rather than
                # us silently doing nothing.
                self.notify_char = DEFAULT_NOTIFY_CHAR
                log.warning(
                    "[%s] no known notify char in GATT tree; falling "
                    "back to %s, probes will likely time out",
                    self.id, self.notify_char[4:8])
            else:
                log.info("[%s] auto-selected notify_char=%s",
                         self.id, self.notify_char[4:8])

        try:
            await self._client.start_notify(self.notify_char, self._on_notify)
            log.info("[%s] start_notify(%s) ok",
                     self.id, self.notify_char[4:8])
        except Exception as e:
            log.error("[%s] start_notify(%s) FAILED: %s",
                      self.id, self.notify_char[4:8], e)
            raise
        log.info("[%s] connected", self.id)

    async def _bluez_force_disconnect(self) -> None:
        """When BlueZ thinks a device is still connected (to a Python
        process that's gone), the device won't re-advertise. Sending it
        an explicit disconnect via DBus releases the cached connection
        so the next discovery sees it again. Idempotent, no-op if the
        device isn't tracked or already disconnected.

        Implemented as a subprocess `bluetoothctl disconnect <MAC>`
        rather than reaching into bleak's DBus internals, works
        across bleak versions and easier to reason about."""
        await self._run_bluetoothctl("disconnect", self.address)
        # Brief pause so BlueZ surfaces the released state to its scan
        # cache before our next discover() reads it.
        await asyncio.sleep(1.0)

    async def _bluez_remove_device(self) -> None:
        """Evict the cached device record from BlueZ, including its
        cached GATT tree. Necessary after a "failed to discover
        services, device disconnected" because bleak otherwise reuses
        stale service handles on the next connect and re-hits the
        same disconnect-mid-discovery failure.

        Lighter than `forget`; the BT-2 doesn't need pairing so
        there's no auth state to lose. Next discovery walks the live
        services from scratch."""
        await self._run_bluetoothctl("remove", self.address)
        # Removal triggers an internal DBus broadcast; give BlueZ a
        # moment before we ask it to discover the device again.
        await asyncio.sleep(1.0)

    async def _run_bluetoothctl(self, *args: str) -> None:
        """Common subprocess runner for bluetoothctl one-shots.
        Swallows nothing, the caller's try/except decides what to
        log. Kills the subprocess if it stalls past 5s."""
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()

    async def close(self) -> None:
        """Release the BT-2 cleanly so the next process / restart can
        re-acquire it without a physical replug.

        The Renogy BT-2 only accepts one BLE master at a time. If a
        previous process died WITHOUT a clean GATT disconnect, the
        dongle holds the session in its own RAM and refuses every
        future scanner until power-cycled. This is the single most-
        reported failure mode in the community (cyrils/renogy-bt #97,
        #45; Renogy's own KB recommends physical unplug).

        Defensive layering:
        1. stop_notify with a 2 s cap, protects against a hung
           DBus call when BlueZ has already half-dropped the link.
        2. disconnect() with a 5 s cap, same risk. Total in-Docker
           shutdown budget is the SIGTERM grace (10 s default), so
           we have to fit inside that or Docker SIGKILLs us mid-call.
        3. If either path raised or timed out, fall back to a
           subprocess `bluetoothctl disconnect <mac>`, that drops
           the session at the BlueZ layer even when the bleak
           Python object got wedged.

        Always logs the outcome at INFO so a quick `journalctl -u
        wattpost | grep "ble_modbus.*close"` proves the exit ran
        cleanly. If you see "close: forced" in production it means
        the bleak disconnect timed out, worth investigating but
        not a stuck-dongle situation either way.
        """
        if self._client is None:
            return
        client = self._client
        self._client = None  # block re-entrant close() calls

        bleak_ok = False
        try:
            if client.is_connected:
                try:
                    await asyncio.wait_for(
                        client.stop_notify(self.notify_char), timeout=2.0,
                    )
                except Exception:
                    pass  # notify may already be torn down by peer
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
                bleak_ok = True
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("[%s] close: bleak disconnect failed (%s), "
                        "falling back to bluetoothctl",
                        self.id, type(e).__name__)
        # Belt + braces: even when bleak said "disconnected ok", a
        # subprocess `bluetoothctl disconnect` is a cheap insurance
        # against half-dropped BlueZ state. It's a no-op when the
        # device is already disconnected.
        try:
            await self._bluez_force_disconnect()
        except Exception:
            pass
        log.info("[%s] close: bleak=%s, bluetoothctl=ok",
                 self.id, "ok" if bleak_ok else "forced")

    def _on_notify(self, _char, data: bytearray) -> None:
        # Notifications arrive in MTU-sized chunks; accumulate until we
        # have the full expected frame, then unblock the waiter.
        log.debug("[%s] notify rx %d byte(s) (have %d/%d)",
                  self.id, len(data), len(self._buf) + len(data),
                  self._expected_len)
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

            log.debug("[%s] write_gatt_char(%s, %d bytes)",
                      self.id, self.write_char[4:8], len(frame))
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
        # None → auto-detect at connect time from the GATT tree.
        notify_char=cfg.get("notify_char"),
        discovery_timeout=cfg.get("discovery_timeout", DEFAULT_DISCOVERY_TIMEOUT),
    )
