"""Setup wizard endpoints.

The SPA's Setup route uses these to scan for new devices on an already-open
transport, identify them by reading the vendor-specific model register, and
append a validated device entry to config.yaml. The live daemon keeps
polling on its own loop while a probe runs, the transport's request lock
serialises so the two callers don't collide.

A daemon restart is required for new devices to start polling. The endpoint
returns a flag the SPA uses to show the restart prompt.
"""
from __future__ import annotations

import asyncio
import logging
import time
import re
import shutil
from pathlib import Path
from typing import Any

import json

import msgspec
import yaml
from litestar import delete, get, patch, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException
from litestar.response import Stream

from ..config import Config
from ..modbus import build_read_holding, expected_read_response_len, verify_response
from ..scheduler import PollScheduler
from ..vendors import VENDORS

log = logging.getLogger(__name__)


# Serializes background hot-reloads. Two saves in quick succession
# (which happens when a user batch-adds devices found by a scan)
# would otherwise spawn two reloads concurrently, racing to swap
# state["scheduler"]. The lock turns that into one-at-a-time, with
# the latest config.yaml winning.
_hot_reload_lock = asyncio.Lock()


async def _hot_reload_bg(state: State) -> None:
    """Run _hot_reload as a fire-and-forget background task.
    Wizard-side callers don't have to wait the ~5s for the running
    poll cycle to drain before the user gets a "Saved" response.

    Failures are logged but not surfaced to the original caller,
    by the time this runs, the HTTP response is long gone. The
    daemon health pill on the dashboard surfaces a non-running
    scheduler if the reload bricks itself."""
    async with _hot_reload_lock:
        try:
            await _hot_reload(state)
        except Exception:
            log.exception("background hot-reload failed")


async def _hot_reload(state: State) -> dict[str, Any]:
    """Replace the running scheduler with a fresh one built from the
    on-disk config.yaml. Used after wizard writes (add-transport,
    add-device) so the user doesn't have to manually restart the
    daemon, flow ends with "polling started" instead of "now click
    Restart daemon".

    Strategy:
      1. Re-read config.yaml from disk.
      2. await scheduler.stop() , waits for any in-flight poll.
      3. Construct a new PollScheduler with the same Store (so the
         database connection survives and history is uninterrupted).
      4. await new_scheduler.start().
      5. Swap into app.state. Subsequent endpoints see the new
         scheduler + config.

    Returns a dict so the caller can include reload status / errors
    in their own response. Never raises, if reload fails, we keep
    the old scheduler stopped and log the failure; the dashboard
    surfaces it via the daemon-stopped state on the next poll.
    """
    from ..config import load_config
    from ..scheduler import PollScheduler
    config_path: str = state.get("config_path", "config.yaml")

    try:
        new_config = load_config(config_path)
    except Exception as e:
        log.exception("hot-reload: config parse failed (%s), keeping old scheduler", e)
        return {"reloaded": False, "error": f"config parse: {e}"}

    old_scheduler: PollScheduler = state["scheduler"]
    store = state["store"]
    interval = old_scheduler.interval_seconds
    maintenance = getattr(old_scheduler, "maintenance_interval_seconds", 600)

    try:
        await old_scheduler.stop()
    except Exception as e:
        log.exception("hot-reload: old scheduler stop raised (%s), continuing anyway", e)

    new_scheduler = PollScheduler(
        new_config, store,
        interval_seconds=interval,
        maintenance_interval_seconds=maintenance,
    )
    try:
        await new_scheduler.start()
    except Exception as e:
        log.exception("hot-reload: new scheduler start failed (%s)", e)
        # state["scheduler"] is the now-stopped old one. Leave it in
        # place so health endpoints can read its dead state, better
        # than silently swallowing the start failure.
        return {"reloaded": False, "error": f"start failed: {e}"}

    state["scheduler"] = new_scheduler
    state["config"]    = new_config
    log.info("hot-reload: scheduler swapped, %d transports, %d devices, %d alerts",
             len(new_config.transports), len(new_config.devices), len(new_config.alerts))
    return {"reloaded": True}


# Slave IDs we try by default, covers Renogy factory conventions:
#   1, 16:      charge controllers (Rover/Wanderer/Adventurer)
#   32–55:      smart batteries (battery_index + 32, or 48-63)
#   96, 97:     hub-mode addresses
DEFAULT_PROBE_IDS: tuple[int, ...] = (
    1, 16,
    32, 33, 34, 35, 36,
    48, 49, 50, 51, 52, 53, 54, 55,
    96, 97,
)

# Register slots that hold a model-name ASCII string for each vendor we
# probe. The wizard tries each entry in order until one comes back with
# usable ASCII. Order matters, DCC + inverter blocks come first because
# they're more specific; the generic Rover block at register 12 is the
# fallback catch-all for any other Renogy device that happens to keep
# the model string there.
_MODEL_PROBES: list[tuple[str, str, int, int]] = [
    # (vendor, suggested_kind, register, word_count)
    ("renogy", "smart_battery",    5122, 8),    # LFP smart batteries
    ("renogy", "inverter",         4311, 8),    # 1000W/2000W/3000W inverters
    ("renogy", "charge_controller",  12, 8),    # Rover/Wanderer/Adv/Voyager
                                                # + DCC50S/DCC30S (driver
                                                # picks via _classify_renogy)
]

# Per-vendor heuristics that map a model string → recommended device kind.
# Order matters, more specific patterns first (DCC, inverter) so the
# generic Rover catch-all doesn't claim them by mistake.
def _classify_renogy(model: str) -> str | None:
    m = (model or "").upper()
    # Shunt / Battery Monitor, RBM-S100 / S300 / S500. Must come BEFORE
    # the smart-battery RBT check; RBM and RBT differ by one letter but
    # are completely different products. Also catches "SHUNT" in case
    # newer firmware names it that way.
    if m.startswith("RBM") or "SHUNT" in m:
        return "shunt"
    # Smart batteries: most LFP packs start with "RBT" (Renogy Battery
    # Type) or include "LFP" in the model.
    if m.startswith("RBT") or "LFP" in m:
        return "smart_battery"
    # DC-DC + MPPT combo (DCC50S, DCC30S, DCC25S, DCC15S). Sometimes
    # the model string is just "DCC50S"; sometimes "RNG-DCC50S". Match
    # both. These MUST be checked before the generic charge_controller
    # patterns, they'd otherwise match "RNG-" and end up routed to
    # the wrong driver.
    if "DCC" in m and any(c.isdigit() for c in m):
        return "dcdc"
    # Inverters: Renogy uses "RIV" (Renogy Inverter), or sometimes
    # "RNG-INVT-". Both have INV in them so we just match on that.
    if "INV" in m or m.startswith("RIV"):
        return "inverter"
    # Charge controllers, the catch-all bucket. Covers Rover, Rover
    # Elite, Rover Boost, Wanderer (all current/Li/PG variants),
    # Adventurer, Voyager, plus the generic "RNG-CTRL-" prefix used
    # on newer model SKUs.
    if any(s in m for s in (
        "ROVER", "WANDER", "ADVENTUR", "VOYAGER",
        "RNG-CTRL", "RNG-",
    )):
        return "charge_controller"
    return None


def _likely_ascii(b: bytes) -> bool:
    """A probe response is plausible ASCII model text if at least one byte
    is a printable letter/digit and there are no high-bit bytes."""
    if not b:
        return False
    if any(c > 0x7E for c in b):
        return False
    return any(0x30 <= c <= 0x7E for c in b if c != 0x20)


def _clean_ascii(b: bytes) -> str:
    text = b.decode("ascii", errors="replace").replace("\x00", "").strip()
    return re.sub(r"\s+", " ", text)


@get("/api/setup/discovered")
async def discovered() -> dict[str, Any]:
    """Broadcast devices the always-on advert scanner has heard recently
    (Victron / sensors / Renogy BT), classified by vendor. The setup UI
    offers these as add-candidates without the user first configuring a
    transport — no add-a-connection-then-scan dance."""
    from ..transport import ble_discovery
    return {"devices": ble_discovery.snapshot()}


@get("/api/setup/ble_status")
async def ble_status() -> dict[str, Any]:
    """List BLE adapters visible to the daemon. Used by the Setup
    wizard to surface "Bluetooth is reaching the container / Pi"
    vs "no radio detected, check the dongle".

    Parses `bluetoothctl list` (and falls back to nothing if the
    command isn't installed). Each adapter shows its name (hci0,
    hci1, …), MAC, and powered-state from `bluetoothctl show`.
    """
    bctl = shutil.which("bluetoothctl")
    if bctl is None:
        return {
            "available": False,
            "reason": "bluetoothctl not installed in this environment",
            "adapters": [],
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            bctl, "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (FileNotFoundError, asyncio.TimeoutError) as e:
        return {"available": False, "reason": str(e), "adapters": []}

    adapters: list[dict[str, Any]] = []
    # `bluetoothctl list` output: `Controller XX:XX:XX:XX:XX:XX hci0 [default]`
    for line in out.decode("utf-8", errors="replace").splitlines():
        m = re.match(r"^Controller\s+([0-9A-Fa-f:]{17})\s+(\S+)(.*)$", line.strip())
        if not m:
            continue
        mac, name, rest = m.group(1), m.group(2), m.group(3)
        # Default-controller flag.
        default = "[default]" in rest
        # Probe powered-state. Best-effort, separate call per adapter
        # so a stuck adapter doesn't kill the whole listing.
        powered = None
        try:
            sp = await asyncio.create_subprocess_exec(
                bctl, "show", mac,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            sout, _ = await asyncio.wait_for(sp.communicate(), timeout=3)
            for ln in sout.decode("utf-8", errors="replace").splitlines():
                if "Powered:" in ln:
                    powered = "yes" in ln.lower()
                    break
        except Exception:
            pass
        adapters.append({
            "name":    name,
            "address": mac,
            "default": default,
            "powered": powered,
        })

    return {
        "available": bool(adapters),
        "reason":    None if adapters else "no Bluetooth controllers found",
        "adapters":  adapters,
    }


@get("/api/setup/ble_diagnose")
async def ble_diagnose() -> dict[str, Any]:
    """Side-by-side bleak vs bluetoothctl scan to catch the
    Realtek+BlueZ 5.72 silent-failure case (#158).

    The failure mode: `bluetoothctl --timeout N scan on` exits 0
    with no errors but emits zero `[NEW] Device` lines, even when
    a phone running the same kind of scan a metre away picks up
    plenty. Bleak talks DBus directly and tends to keep working
    when bluetoothctl is in this state.

    Returns counts from both scanners + a verdict the wizard can
    render directly. The endpoint is intentionally synchronous from
    the user's POV, they pressed "Run diagnostics" and expect a
    result, not a stream. Total time budget ~8 seconds: 3s bleak,
    3s bluetoothctl, plus a bit of subprocess startup.
    """
    import asyncio as _asyncio
    bleak_count: int | None = None
    bleak_err: str | None = None
    bctl_count: int | None = None
    bctl_err: str | None = None

    # 1. Bleak scan, same coexistence guards as the regular wizard
    #    scan so a running Victron passive scanner doesn't make us
    #    look broken when actually the radio is shared.
    try:
        from bleak import BleakScanner
        from ..transport.ble_modbus import HCI_DISCOVER_LOCK
        async with HCI_DISCOVER_LOCK:
            victron_was_running = False
            try:
                from ..transport.ble_victron_advertise import _scanner as _vs
                victron_was_running = await _vs().pause()
            except Exception:
                pass
            try:
                devs = await BleakScanner.discover(timeout=3)
                bleak_count = len(devs or [])
            finally:
                if victron_was_running:
                    try:
                        from ..transport.ble_victron_advertise import _scanner as _vs
                        await _vs().resume()
                    except Exception:
                        pass
    except ImportError:
        bleak_err = "bleak not installed"
    except Exception as e:
        bleak_err = str(e)

    # 2. bluetoothctl scan. The `--timeout N scan on` form is the
    #    only way to do a non-interactive bounded scan. Anything
    #    older needs `echo scan on; sleep N; echo scan off` piped
    #    in, which is dodgier. Count `[NEW] Device …` lines in
    #    stdout. Filter out the controller's own MAC.
    bctl = shutil.which("bluetoothctl")
    if bctl is None:
        bctl_err = "bluetoothctl not installed"
    else:
        try:
            proc = await _asyncio.create_subprocess_exec(
                bctl, "--timeout", "3", "scan", "on",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            out, err = await _asyncio.wait_for(
                proc.communicate(), timeout=8,
            )
            text = out.decode("utf-8", errors="replace")
            macs: set[str] = set()
            for line in text.splitlines():
                m = re.search(
                    r"\[NEW\] Device ([0-9A-Fa-f:]{17})", line,
                )
                if m:
                    macs.add(m.group(1).upper())
            bctl_count = len(macs)
        except _asyncio.TimeoutError:
            bctl_err = "bluetoothctl scan timed out"
        except Exception as e:
            bctl_err = str(e)

    # 3. Verdict. The interesting cases are the divergent ones.
    verdict = "ok"
    suggestion = None
    if bleak_count is None and bctl_count is None:
        verdict = "no_scanner_available"
        suggestion = (
            "Neither bleak nor bluetoothctl could run. Check the "
            "BLE adapter is connected and not held by another "
            "process. Reboot the Pi if the adapter status pill "
            "shows no controllers."
        )
    elif bleak_count is None:
        verdict = "bleak_failed"
        suggestion = (
            f"Bleak scan failed ({bleak_err}). The daemon uses bleak "
            f"for every poll, so this is the failure path to fix. "
            f"Check `dmesg | tail` for HCI errors."
        )
    elif bctl_count is None:
        verdict = "bluetoothctl_failed"
        suggestion = (
            "bluetoothctl could not run. This usually means bluez "
            "is not installed in the container. Bleak still works "
            "for normal polling, so this is informational."
        )
    elif bleak_count > 0 and bctl_count == 0:
        verdict = "scan_silent_failure"
        suggestion = (
            "Bleak finds devices but bluetoothctl returns zero. "
            "This is a known bug on Realtek BLE chips with BlueZ "
            "5.72 (the bluetoothctl D-Bus session loses scan "
            "events). It does NOT affect WattPost polling, which "
            "uses bleak directly. If you also see issues from the "
            "main wizard scan, the dongle is probably held by "
            "another host on the LAN."
        )
    elif bleak_count == 0 and bctl_count > 0:
        verdict = "bleak_silent_failure"
        suggestion = (
            "bluetoothctl finds devices but bleak returns zero. "
            "Most likely a stale BleakScanner instance is holding "
            "the discovery slot. Restart the daemon: "
            "`docker compose restart wattpost` or `sudo systemctl "
            "restart wattpost`."
        )
    elif bleak_count == 0 and bctl_count == 0:
        verdict = "no_devices_seen"
        suggestion = (
            "Neither scanner found anything. Either no BLE devices "
            "are advertising in range, OR another host on your LAN "
            "is holding all the dongles. The setup wizard's main "
            "scan checks for that case automatically."
        )
    return {
        "bleak":         {"count": bleak_count, "error": bleak_err},
        "bluetoothctl":  {"count": bctl_count,  "error": bctl_err},
        "verdict":       verdict,
        "suggestion":    suggestion,
    }


class BleScanRequest(msgspec.Struct):
    seconds: int = 8


# In-memory MAC last-seen cache. Keyed by uppercase MAC, value is
# {last_seen_ts, last_rssi, name}. Used to populate the
# `seen_recently_missing` field, MACs we saw in a prior scan but
# aren't in the current one. Big quality-of-life signal for "my
# dongle was here a minute ago, now it isn't" debug.
#
# Module-level, in-memory only. Resets on daemon restart, which is
# fine: the cache is a hint, not a source of truth.
_BLE_SEEN_CACHE: dict[str, dict[str, Any]] = {}
_BLE_SEEN_TTL_S = 15 * 60   # 15 min, long enough to catch most
                            # "happened a minute ago" cases without
                            # holding onto stale entries forever.


@post("/api/setup/ble_scan")
async def ble_scan(data: BleScanRequest, state: State) -> dict[str, Any]:
    """Scan BLE advertisements for `seconds`. Returns a list of every
    advertising device the host can see, MAC, name (if advertised),
    RSSI. The UI dedupes and shows them so the user can pick their
    Renogy BT-2 dongle by name pattern (`BT-TH-…`) or by MAC printed
    on the dongle itself.

    Also returns `seen_recently_missing`, MACs we saw in a previous
    scan within the last 15 minutes that AREN'T in the current scan.
    A Renogy BT-2 that suddenly disappears between scans is almost
    always being held by the Renogy mobile app (the BT-2 only allows
    one BLE master at a time). Surfacing that to the user saves a
    long debug session.

    Clamped to 2..30 seconds. Eight is the default, long enough that
    a slow-advertising dongle shows up, short enough that the user
    doesn't think the page froze.
    """
    secs = max(2, min(30, int(data.seconds or 8)))
    try:
        from bleak import BleakScanner
    except ImportError:
        raise HTTPException(status_code=500, detail="bleak not available")

    log.info("ble_scan: discover for %ds", secs)
    # Two coexistence steps (same pattern as the Renogy transport's
    # _open_once, see ble_modbus.HCI_DISCOVER_LOCK):
    #
    #   1. Acquire HCI_DISCOVER_LOCK so we don't collide with a
    #      simultaneous Renogy reconnect (also a BleakScanner.discover).
    #
    #   2. Pause the Victron passive scanner so it yields its
    #      discovery slot on the same HCI adapter. Resume it in
    #      finally{} so the dashboard keeps getting fresh Victron
    #      advertisements after the scan window ends.
    from ..transport.ble_modbus import HCI_DISCOVER_LOCK
    async with HCI_DISCOVER_LOCK:
        victron_was_running = False
        mopeka_was_running = False
        try:
            from ..transport.ble_victron_advertise import _scanner as _victron_scanner
            victron_was_running = await _victron_scanner().pause()
        except Exception:
            log.debug("ble_scan: victron scanner pause skipped (not in use)")
        try:
            from ..transport.ble_mopeka_advertise import _scanner as _mopeka_scanner
            mopeka_was_running = await _mopeka_scanner().pause()
        except Exception:
            log.debug("ble_scan: mopeka scanner pause skipped (not in use)")
        govee_was_running = False
        try:
            from ..transport.ble_govee_advertise import _scanner as _govee_scanner
            govee_was_running = await _govee_scanner().pause()
        except Exception:
            log.debug("ble_scan: govee scanner pause skipped (not in use)")
        ruuvi_was_running = False
        try:
            from ..transport.ble_ruuvi_advertise import _scanner as _ruuvi_scanner
            ruuvi_was_running = await _ruuvi_scanner().pause()
        except Exception:
            log.debug("ble_scan: ruuvi scanner pause skipped (not in use)")
        try:
            # `return_adv=True` makes BleakScanner give us each device's
            # latest advertisement_data alongside the BLEDevice object.
            # We need the manufacturer_data map to identify Victron Instant
            # Readout broadcasts (#118), their advertisements carry a
            # payload under manufacturer ID 0x02E1.
            devices_with_adv = await BleakScanner.discover(
                timeout=secs, return_adv=True,
            )
        except Exception as e:
            # Most failures here are bluez transport problems: adapter
            # powered off, DBus passthrough broken in Docker, etc.
            # Surface the message, it's the most useful debugging hint.
            raise HTTPException(
                status_code=500,
                detail=f"BLE scan failed: {e}. Check the BLE adapter "
                       f"status in step 0 of the wizard.",
            )
        finally:
            if victron_was_running:
                try:
                    from ..transport.ble_victron_advertise import _scanner as _victron_scanner
                    await _victron_scanner().resume()
                except Exception:
                    log.warning("ble_scan: victron scanner resume failed")
            if mopeka_was_running:
                try:
                    from ..transport.ble_mopeka_advertise import _scanner as _mopeka_scanner
                    await _mopeka_scanner().resume()
                except Exception:
                    log.warning("ble_scan: mopeka scanner resume failed")
            if govee_was_running:
                try:
                    from ..transport.ble_govee_advertise import _scanner as _govee_scanner
                    await _govee_scanner().resume()
                except Exception:
                    log.warning("ble_scan: govee scanner resume failed")
            if ruuvi_was_running:
                try:
                    from ..transport.ble_ruuvi_advertise import _scanner as _ruuvi_scanner
                    await _ruuvi_scanner().resume()
                except Exception:
                    log.warning("ble_scan: ruuvi scanner resume failed")
    import time as _time
    now = int(_time.time())
    out = []
    current_macs: set[str] = set()
    # Victron's manufacturer ID. Any advertisement carrying a payload
    # under this key in manufacturer_data is one of their Instant
    # Readout devices (SmartShunt, SmartSolar, Orion-Tr/XS, etc.).
    VICTRON_MFR_ID = 0x02E1
    # Mopeka uses Nordic Semiconductor's manufacturer ID and disambiguates
    # by the hardware-id byte at offset 0 (see ble_mopeka_advertise._HW_KINDS).
    NORDIC_MFR_ID = 0x0059
    MOPEKA_HW_IDS = {0x03, 0x05, 0x06, 0x08, 0x09}
    # Govee thermo-hygrometers (H507x / H510x) share a single manufacturer ID.
    GOVEE_MFR_ID = 0xEC88
    # Ruuvi Innovations, RuuviTag environmental sensors.
    RUUVI_MFR_ID = 0x0499
    for mac, (d, ad) in devices_with_adv.items():
        mac = (mac or "").upper()
        if not mac:
            continue
        current_macs.add(mac)
        rec: dict[str, Any] = {
            "address": mac,
            "name":    d.name or None,
            "rssi":    getattr(ad, "rssi", None),
        }
        # Vendor / protocol detection. The UI uses these to badge the
        # device + route to the correct add-transport form.
        mfr_data = getattr(ad, "manufacturer_data", None) or {}
        if VICTRON_MFR_ID in mfr_data:
            rec["protocol"] = "victron_instant_readout"
            rec["vendor"]   = "victron"
            # Attempt to identify the Victron device kind from the raw
            # payload header. The victron-ble library has detect_device_type
            # which classifies based on the first few bytes (model id).
            # We can't decrypt without the user's key, but we CAN tell
            # the user "this looks like a SmartShunt / SmartSolar / …".
            try:
                from victron_ble.devices import detect_device_type
                payload = mfr_data[VICTRON_MFR_ID]
                dc = detect_device_type(payload)
                if dc is not None:
                    rec["victron_device_class"] = dc.__name__
            except Exception:
                pass
        # Mopeka tank sensors broadcast under the generic Nordic
        # manufacturer ID. The first byte of payload tells us if it's
        # actually a Mopeka vs some unrelated Nordic nRF device.
        if not rec.get("vendor"):
            mp = mfr_data.get(NORDIC_MFR_ID)
            if mp and len(mp) >= 1 and mp[0] in MOPEKA_HW_IDS:
                rec["protocol"] = "mopeka_tank"
                rec["vendor"]   = "mopeka"
                rec["mopeka_hw_id"] = mp[0]
        # Govee H507x / H510x thermometer-hygrometers.
        if not rec.get("vendor") and GOVEE_MFR_ID in mfr_data:
            rec["protocol"] = "govee_ambient"
            rec["vendor"]   = "govee"
        # RuuviTag environmental sensor (format 5 payload).
        if not rec.get("vendor") and RUUVI_MFR_ID in mfr_data:
            rec["protocol"] = "ruuvi_ambient"
            rec["vendor"]   = "ruuvi"

        # Name-based hints stay as before, covers Renogy BT-* and
        # any other vendor identifiable by advertised name.
        nm = (d.name or "").lower()
        if not rec.get("vendor"):
            if nm.startswith("bt-th") or "renogy" in nm:
                rec["vendor"] = "renogy"
                rec["protocol"] = "modbus_bt2"
            elif "jk" in nm or "jbd" in nm:
                rec["vendor"] = "jkbms"
                rec["protocol"] = "jk_ble"

        # For the discovery telemetry path (#129), stash enough of the
        # advertisement to build a fingerprint for unknown devices.
        # These fields are NOT serialised to the UI, they're stripped
        # below before the response is returned. We only use them
        # internally when discovery is opted-in.
        if not rec.get("vendor"):
            if mfr_data:
                try:
                    first_id = min(mfr_data.keys())
                    rec["_mfr_id"] = first_id
                    payload = mfr_data[first_id] or b""
                    rec["_mfr_prefix_hex"] = bytes(payload[:4]).hex()
                except Exception:
                    pass
            svc = getattr(ad, "service_uuids", None) or []
            if svc:
                rec["_service_uuids"] = list(svc)[:8]

        out.append(rec)
        # Refresh the cache so re-appearing devices stay fresh.
        _BLE_SEEN_CACHE[mac] = {
            "last_seen_ts": now,
            "last_rssi":    rec["rssi"],
            "name":         rec["name"],
        }

    # Build the "was visible recently but isn't now" list. Cleans
    # up stale entries past the TTL while we're walking the dict.
    seen_recently_missing: list[dict[str, Any]] = []
    stale_macs: list[str] = []
    for mac, meta in _BLE_SEEN_CACHE.items():
        age = now - int(meta.get("last_seen_ts") or 0)
        if age > _BLE_SEEN_TTL_S:
            stale_macs.append(mac)
            continue
        if mac in current_macs:
            continue
        seen_recently_missing.append({
            "address":      mac,
            "name":         meta.get("name"),
            "last_rssi":    meta.get("last_rssi"),
            "seconds_ago":  age,
            # Renogy-specific hint: the BT-2 advertises as BT-TH-…
            # and is single-master, so a disappearance is almost
            # always the Renogy mobile app holding it. We surface
            # this as a likely_cause string the UI can render
            # without needing its own classification logic.
            "likely_cause": _classify_disappearance(meta.get("name")),
        })
    for mac in stale_macs:
        _BLE_SEEN_CACHE.pop(mac, None)

    # Sort: known-vendor names first (BT-TH = Renogy BT-2), then
    # by signal strength desc, then by name.
    def _vendor_pri(name: str | None) -> int:
        if not name: return 9
        n = name.lower()
        if n.startswith("bt-th") or "renogy" in n: return 0
        if n.startswith("victron") or "smart" in n: return 1
        if "jk" in n or "bms" in n: return 2
        return 5
    out.sort(key=lambda d: (_vendor_pri(d.get("name")), -(d.get("rssi") or -200)))
    seen_recently_missing.sort(key=lambda d: d.get("seconds_ago", 99999))
    log.info("ble_scan: %d devices, %d recently-missing",
             len(out), len(seen_recently_missing))
    # Anonymous discovery telemetry (#129). Strict opt-in, config
    # block discovery.enabled must be true AND the appliance must be
    # cloud-paired. Fire-and-forget; failures are logged but never
    # bubble up to the scan response. Stripping the underscore-prefixed
    # internal keys happens unconditionally just below.
    cfg = state.get("config") if hasattr(state, "get") else state["config"]
    await _maybe_push_discovery(out, cfg)
    # Scrub internal-only fields from the response shape.
    for rec in out:
        for k in ("_mfr_id", "_mfr_prefix_hex", "_service_uuids"):
            rec.pop(k, None)

    # LAN peer hint (#184). Only fire when this scan turned up zero
    # Renogy devices, that's the symptom of "BT-2 held by another
    # host". If Renogy gear is already advertising at us, the
    # single-master collision isn't happening and a network probe is
    # just noise. Same /24 + same default WattPost port; see
    # `_scan_lan_for_wattpost_peers` for the trade-offs.
    found_renogy = any(d.get("vendor") == "renogy" for d in out)
    lan_peers: list[dict[str, Any]] = []
    if not found_renogy:
        try:
            lan_peers = await _scan_lan_for_wattpost_peers()
        except Exception:
            log.debug("lan_peer_scan failed", exc_info=True)

    return {
        "devices": out,
        "scanned_seconds": secs,
        "seen_recently_missing": seen_recently_missing,
        "lan_peers": lan_peers,
    }


async def _maybe_push_discovery(
    scanned: list[dict[str, Any]], cfg: Any,
) -> None:
    """Best-effort: push fingerprints of unrecognised scan results to
    the cloud when the user has opted in. Never raises."""
    from ..discovery import build_ble_fingerprint, push_observations
    if cfg is None or cfg.discovery is None or not cfg.discovery.enabled:
        return
    if cfg.cloud is None or not (
        cfg.cloud.bearer_token and cfg.cloud.endpoint
    ):
        return  # paired-only, discovery POST is bearer-authed
    fps: list[dict[str, Any]] = []
    for rec in scanned:
        enriched = dict(rec)
        # Promote internal fields to the explicit names build_ble_fingerprint
        # expects, without leaking them to the scan response.
        if "_mfr_id" in rec:
            enriched["manufacturer_first_id"] = rec["_mfr_id"]
        if "_mfr_prefix_hex" in rec:
            enriched["manufacturer_prefix_hex"] = rec["_mfr_prefix_hex"]
        if "_service_uuids" in rec:
            enriched["service_uuids"] = rec["_service_uuids"]
        fp = build_ble_fingerprint(enriched)
        if fp is not None:
            fps.append(fp)
    if not fps:
        return
    try:
        await push_observations(
            cfg.cloud.endpoint, cfg.cloud.bearer_token, fps,
        )
    except Exception:
        # Never let telemetry break the user-facing scan.
        log.debug("discovery push raised, swallowing", exc_info=True)


@get("/api/setup/hid_scan")
async def hid_scan() -> dict[str, Any]:
    """Enumerate USB-HID devices and flag known hybrid-inverter VID:PIDs.

    The wizard calls this from the "Add hybrid inverter" branch to
    show installers which of their plugged-in HID devices match a
    supported family. Falls back gracefully when the `hid` Python
    package isn't installed (default on every install today,
    optional dep) so the wizard can render a clear "install hidapi
    on this host" hint instead of a 500.

    Each entry carries:
      vid + pid             integers, ready for AddTransportRequest
      manufacturer + product strings from the device descriptor
      serial_number          for multi-inverter installs
      match                  "voltronic" | None, tells the wizard
                             whether to default to usbhid_voltronic
                             on Add.
    """
    try:
        import hid  # type: ignore[import-not-found]
    except ImportError:
        return {
            "devices": [],
            "hidapi_available": False,
            "hint": (
                "Install the `hid` python package on the appliance to "
                "enable USB-HID scanning. On the Pi: "
                "`sudo apt-get install -y libhidapi-libusb0 && "
                "pip install --break-system-packages hid` (matches the "
                "library used by the Voltronic driver)."
            ),
        }

    # Known VID:PIDs that map to the Voltronic family. Pulled from
    # community reports + mpp-solar's device list. Add new entries
    # here as customer reports come in.
    VOLTRONIC_VID_PIDS = {
        (0x0665, 0x5161),  # Cypress HID chip, Axpert / MPP / Mecer / Effekta
        (0x0001, 0x0000),  # EG4 6500EX-48 variant
    }

    out: list[dict[str, Any]] = []
    try:
        for d in hid.enumerate():
            vid = int(d.get("vendor_id") or 0)
            pid = int(d.get("product_id") or 0)
            rec: dict[str, Any] = {
                "vid":           vid,
                "pid":           pid,
                "manufacturer":  d.get("manufacturer_string") or "",
                "product":       d.get("product_string") or "",
                "serial_number": d.get("serial_number") or "",
                "match":         None,
            }
            if (vid, pid) in VOLTRONIC_VID_PIDS:
                rec["match"] = "voltronic"
            out.append(rec)
    except Exception:
        log.exception("hid_scan: enumerate failed")

    log.info("hid_scan: %d HID device(s); %d voltronic match(es)",
             len(out), sum(1 for r in out if r["match"]))
    return {"devices": out, "hidapi_available": True}


@get("/api/setup/usb_scan")
async def usb_scan() -> dict[str, Any]:
    """Enumerate USB serial devices and classify what protocol each is
    emitting.

    Returns every `/dev/ttyUSB*` and `/dev/ttyACM*` the host can see,
    along with chip-level identifiers from sysfs (vendor + product IDs,
    chip name) AND a brief protocol sniff per device so the wizard
    can route the user correctly:

      * `nmea_gps`        , emitted NMEA sentences (`$GP…` / `$GN…`).
                             Routed to GPS setup (when #125 lands).
      * `modbus_rtu`      , silent at 1s of read, plausible Modbus
                             host, routed through the existing
                             "Use as Modbus" flow.
      * `unknown`         , port opens, no recognisable output,
                             user can still pick it manually.
      * `busy`            , port couldn't be opened (held by the
                             daemon or another process).

    Protocol sniff is read-only: open at 9600 baud, read for ~700 ms,
    classify by what landed. We don't write a Modbus probe here, the
    existing `/api/setup/probe` endpoint does that once the user has
    selected a transport, and writing blindly could clobber something
    else on the bus.
    """
    import asyncio as _asyncio
    import glob
    import os

    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        rec: dict[str, Any] = {"port": path}
        leaf = path.rsplit("/", 1)[-1]
        sys_base = f"/sys/class/tty/{leaf}/device"
        # Walk up the sysfs chain to find the USB device node, the
        # immediate `device` symlink is a per-driver wrapper that
        # doesn't carry idVendor / idProduct, but its grand-parent
        # (or great-grand-parent for FTDI) does.
        for hop in range(0, 6):
            probe = sys_base + ("/.." * hop)
            try:
                if os.path.isfile(probe + "/idVendor"):
                    with open(probe + "/idVendor") as f:
                        rec["vendor_id"] = f.read().strip()
                    with open(probe + "/idProduct") as f:
                        rec["product_id"] = f.read().strip()
                    for fname in ("manufacturer", "product", "serial"):
                        try:
                            with open(f"{probe}/{fname}") as f:
                                v = f.read().strip()
                                if v:
                                    rec[fname] = v
                        except OSError:
                            pass
                    break
            except OSError:
                continue

        # Friendly chip label so the UI doesn't need to map VID/PIDs.
        # These four IDs cover ~95% of consumer USB-RS485 adapters in
        # the wild, FTDI, WCH (CH340/CH341), Prolific, Silicon Labs.
        vid = rec.get("vendor_id", "").lower()
        pid = rec.get("product_id", "").lower()
        chip = None
        if vid == "0403":               chip = "FTDI FT232"
        elif vid == "1a86" and pid == "7523": chip = "WCH CH340"
        elif vid == "1a86" and pid == "5523": chip = "WCH CH341"
        elif vid == "067b":             chip = "Prolific PL2303"
        elif vid == "10c4":             chip = "Silicon Labs CP210x"
        if chip:
            rec["chip"] = chip

        # Protocol sniff. Best-effort, any failure tags the device
        # as `unknown` rather than blocking the scan.
        rec["protocol"] = await _asyncio.get_event_loop().run_in_executor(
            None, _sniff_serial_protocol, path,
        )
        out.append(rec)

    log.info("usb_scan: %d device(s) found", len(out))
    return {"adapters": out}


def _sniff_serial_protocol(port: str) -> str:
    """Open a serial port, read briefly, classify what came out.

    Runs in a thread-pool executor, pyserial is synchronous and we
    don't want a long-read to block the event loop while scanning a
    handful of devices.

    Classifications:
      * 'nmea_gps', at least one `$GP…` / `$GN…` / `$GL…` / `$GA…`
        sentence within the read window. Reliable signal: NMEA
        receivers emit ~10 sentences per second at 9600 baud.
      * 'modbus_rtu', zero bytes received. Silent serial ports are
        the Modbus default: the device only speaks when polled. NOT a
        guarantee (could be an unplugged adapter), but the right
        default for routing in the wizard.
      * 'unknown', bytes received but no recognised pattern.
      * 'busy', port couldn't be opened (already in use).
    """
    try:
        import serial as _serial
    except ImportError:
        return "unknown"
    try:
        ser = _serial.Serial(
            port=port, baudrate=9600,
            bytesize=8, parity="N", stopbits=1,
            timeout=0.7,
        )
    except Exception:
        return "busy"
    try:
        buf = bytearray()
        # ~700 ms total budget: NMEA at 9600 baud emits well over
        # one sentence in this window. Modbus devices are silent.
        # We read in small chunks so a really chatty source doesn't
        # let us miss the deadline waiting for a buffer fill.
        import time as _time
        deadline = _time.monotonic() + 0.7
        while _time.monotonic() < deadline and len(buf) < 256:
            chunk = ser.read(64)
            if not chunk:
                break
            buf.extend(chunk)
    finally:
        try:
            ser.close()
        except Exception:
            pass

    if not buf:
        # Silent serial = either Modbus (typical) or unplugged.
        # We hint Modbus because that's the dominant case in our
        # customer base; the wizard's slave-ID probe will tell the
        # truth once the user picks the device.
        return "modbus_rtu"

    # NMEA: starts with `$` and one of the standard talker IDs.
    # We scan the buffer rather than just looking at byte 0 because
    # some receivers boot with a few bytes of junk before the first
    # full sentence.
    text = bytes(buf).decode("ascii", errors="ignore")
    for line in text.splitlines():
        if line.startswith(("$GP", "$GN", "$GL", "$GA", "$GB", "$BD")):
            return "nmea_gps"

    # Got bytes but no NMEA pattern, could be a Victron VE.Direct
    # device emitting at 19200 baud (we were reading at 9600 so any
    # frames came out as garbled symbols). Re-sniff at 19200 looking
    # for the VE.Direct frame signature (`\r\nPID\t` or `Checksum\t`).
    if _looks_like_ve_direct(port):
        return "ve_direct"
    return "unknown"


def _looks_like_ve_direct(port: str) -> bool:
    """Second-pass sniff at 19200 baud for Victron VE.Direct frames.
    Cheap; ~500 ms read window. Looks for the literal substrings
    that bracket every text frame so noise can't trigger a false
    positive."""
    try:
        import serial as _serial
    except ImportError:
        return False
    try:
        ser = _serial.Serial(
            port=port, baudrate=19200,
            bytesize=8, parity="N", stopbits=1, timeout=0.5,
        )
    except Exception:
        return False
    try:
        import time as _time
        deadline = _time.monotonic() + 1.2
        buf = bytearray()
        while _time.monotonic() < deadline and len(buf) < 1024:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf.extend(chunk)
            if b"PID\t" in buf or b"Checksum\t" in buf:
                return True
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return False


def _own_route_ip() -> str | None:
    """The single IP the kernel would route outbound traffic through.

    This is the IP a peer on the same LAN would see when we connect
    to them, which makes it the right anchor for "what's my subnet".
    Distinct from `_own_lan_ips()` (below) because that returns ALL
    of our NIC IPs, including the docker0 bridge `172.17.0.1` when
    we're in a host-network container, which would wrongly anchor
    the subnet scan to the docker bridge instead of the LAN.
    """
    import socket as _socket
    for target in ("8.8.8.8", "1.1.1.1"):
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((target, 53))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
    return None


def _own_lan_ips() -> set[str]:
    """ALL of the host's own IPv4 addresses, for self-exclusion.

    Includes the docker bridge (`172.17.0.1`), secondary NICs, and
    whatever else the kernel knows about. Don't use this to pick the
    subnet anchor, that's what `_own_route_ip()` is for. This is
    only for "don't probe ourselves".
    """
    import socket as _socket
    ips: set[str] = set()
    # The route-out IP belongs here too, of course.
    route_ip = _own_route_ip()
    if route_ip:
        ips.add(route_ip)
    # Hostname lookup. Catches localhost variants + secondary NICs.
    try:
        hn = _socket.gethostname()
        for info in _socket.getaddrinfo(hn, None, _socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                ips.add(addr)
    except Exception:
        pass
    return ips


async def _scan_lan_for_wattpost_peers(
    web_port: int = 8000, timeout: float = 0.4,
) -> list[dict[str, Any]]:
    """Concurrent TCP probe of the local /24 looking for other WattPost
    appliances on the same web port.

    Used by the setup wizard when a BLE scan finds no Renogy devices:
    if there's another WattPost on the LAN it's quite possibly holding
    the BT-2 dongle's single BLE master slot, which makes the dongle
    invisible to us. Surfacing that to the user is the #1 thing we
    learned debugging issue #184 (laptop appliance holding the BT-2
    across the network).

    Trade-offs deliberately accepted:
      * Only scans a /24 around our own IP. A /16 or bridged-AP
        network would miss peers; that's fine, the wizard hint is a
        speculative cause, not a guarantee.
      * Probes the *default* WattPost port. A peer on a non-default
        port (the wattpost-config TUI can change it) won't be found.
      * Identification is via our own `/api/health` returning
        `{"service": "wattpost"}`. A peer running an older version
        without that field will be skipped silently, also fine.

    Returns a list of `{ip, version}` for confirmed peers, EXCLUDING
    this host's own IPs. Never raises; on any failure returns [].
    """
    import socket as _socket
    own = _own_lan_ips()
    # Anchor on the route-out IP, NOT just "first non-loopback own
    # IP". In a host-network docker container, gethostname()
    # surfaces `172.17.0.1` (docker0) alongside the real LAN IP,
    # and we want the LAN one to derive the subnet from.
    anchor = _own_route_ip()
    if not anchor:
        return []
    parts = anchor.split(".")
    if len(parts) != 4:
        return []
    subnet_prefix = ".".join(parts[:3]) + "."
    candidates = [
        f"{subnet_prefix}{i}" for i in range(1, 255)
        if f"{subnet_prefix}{i}" not in own
    ]

    async def _probe(ip: str) -> dict[str, Any] | None:
        # Two-stage: 1) connect on the WattPost port, 2) GET /api/health
        # and check `service == "wattpost"`. Both stages clamp tight so
        # the whole subnet sweep completes inside the wizard's UX window.
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(ip, web_port), timeout=timeout,
            )
        except Exception:
            return None
        try:
            req = (
                f"GET /api/health HTTP/1.1\r\n"
                f"Host: {ip}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode()
            w.write(req)
            await asyncio.wait_for(w.drain(), timeout=timeout)
            data = await asyncio.wait_for(r.read(2048), timeout=timeout)
        except Exception:
            return None
        finally:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        if b'"service":"wattpost"' not in data and b'"service": "wattpost"' not in data:
            return None
        # Extract version if present.
        version = None
        try:
            body = data.split(b"\r\n\r\n", 1)[1]
            j = json.loads(body)
            version = j.get("version")
        except Exception:
            pass
        return {"ip": ip, "version": version}

    log.info("lan_peer_scan: probing %s0/24 (excluding own %s)",
             subnet_prefix, sorted(own))
    results = await asyncio.gather(
        *(_probe(ip) for ip in candidates), return_exceptions=True,
    )
    peers = [r for r in results
             if isinstance(r, dict) and r is not None]
    log.info("lan_peer_scan: %d peer(s) found", len(peers))
    return peers


def _classify_disappearance(name: str | None) -> str | None:
    """Best-guess explanation for why a previously-seen MAC isn't
    in this scan's results. Renogy BT-2 single-master behaviour is
    the #1 cause we want to surface; other vendors get a generic
    'recently disappeared' note."""
    if not name:
        return "recently disappeared"
    n = name.lower()
    if n.startswith("bt-th") or "renogy" in n:
        return ("Renogy BT-2 only allows one connection at a time. "
                "Force-quit the Renogy DC Home / DC Connect app on "
                "any phone in range, or power-cycle the dongle.")
    if n.startswith("victron") or "smart" in n:
        return ("Victron dongles can be held by VictronConnect. "
                "Close the app on any phone in range.")
    return "recently disappeared"


class AddTransportRequest(msgspec.Struct):
    # BLE Modbus (Renogy BT-2 etc.): provide `address` (MAC).
    # serial_modbus (USB-RS485 dongle):  provide `port` (e.g. /dev/ttyUSB0)
    #                                    + optional `baudrate` (defaults to
    #                                    9600, Renogy default).
    # ble_victron_advertise (Victron Instant Readout):
    #                                    provide `address` (MAC) + `encryption_key`
    #                                    (32-char hex from VictronConnect →
    #                                    Product info → Show device key).
    # usbhid_voltronic (Axpert / MPP / EG4 hybrid inverter, USB-HID):
    #                                    provide `vid` + `pid` (default 0665:5161,
    #                                    the Cypress HID chip in every Voltronic
    #                                    rebadge, EG4 6500EX uses 0001:0000),
    #                                    + optional `serial_number` if multiple
    #                                    inverters are wired to the same host.
    # Discriminated by `type` so a single endpoint handles every transport
    # kind the unified wizard surfaces (#120 / #118).
    address: str | None = None
    port: str | None = None
    baudrate: int = 9600
    encryption_key: str | None = None
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    # device_class: optional hint from the wizard's scan results (the
    # victron-ble Device class name, e.g. "AcCharger" / "SolarCharger" /
    # "BatteryMonitor"). Used by ble_victron_advertise to pick the
    # right device_kind when auto-creating the device row. Falls back to
    # a sensible default if absent.
    device_class: str | None = None
    label: str | None = None
    type: str = "ble_modbus"


@post("/api/setup/transports/add")
async def add_transport(data: AddTransportRequest, state: State) -> dict[str, Any]:
    """Append a new transport to config.yaml. UI-driven replacement for
    editing yaml by hand. Supports `ble_modbus` (BLE dongles like
    Renogy BT-2) and `serial_modbus` (USB-RS485 adapters for wired
    installs). The unified wizard (#120) sends every transport
    through this same endpoint regardless of category."""
    config_path: str = state.get("config_path", "config.yaml")
    path = Path(config_path)

    # Read current yaml from disk (not the boot-time `state["config"]`)
    # so duplicate detection sees any transports added since boot via
    # this same endpoint. Yaml is the source of truth.
    raw = yaml.safe_load(path.read_text()) or {}
    current_transports = raw.get("transports") or []

    if data.type == "ble_modbus":
        mac = (data.address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC (e.g. CC:45:A5:83:B7:42)",
            )
        # Reject duplicates so we don't end up with two transports
        # racing for the same BT-2 dongle.
        for t in current_transports:
            if (t.get("address") or "").upper() == mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"address {mac} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        suffix = mac.replace(":", "").lower()[-4:]
        new_id = f"ble_{suffix[:2]}_{suffix[2:]}"
        block: dict[str, Any] = {
            "type":    "ble_modbus",
            "address": mac,
        }
        default_label = f"BLE dongle {mac[-5:]}"

    elif data.type == "ble_victron_advertise":
        # Victron Instant Readout, passive BLE advertisement decode.
        # Needs the device's encryption key (revealed in VictronConnect
        # under Product info → Show device key). 32-char hex, tolerant
        # of common separator clutter.
        mac = (data.address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC (e.g. CC:CC:CC:CC:CC:CC)",
            )
        key_raw = (data.encryption_key or "").strip()
        # Strip the kinds of separators users typically paste from
        # VictronConnect's "Show device key" dialog (sometimes the key
        # is displayed with spaces or colons every 2 chars).
        key_clean = key_raw.replace(" ", "").replace(":", "").replace("-", "").lower()
        if not re.fullmatch(r"[0-9a-f]{32}", key_clean):
            raise HTTPException(
                status_code=400,
                detail="encryption_key must be 32 hex chars (the value "
                       "VictronConnect shows under Product info → Show "
                       "device key)",
            )
        # Dedupe on MAC across both BLE transport types, a single
        # physical device can't be polled by two different transports
        # at once.
        for t in current_transports:
            if (t.get("address") or "").upper() == mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"address {mac} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        suffix = mac.replace(":", "").lower()[-4:]
        new_id = f"victron_{suffix[:2]}_{suffix[2:]}"
        block = {
            "type":           "ble_victron_advertise",
            "address":        mac,
            "encryption_key": key_clean,
        }
        default_label = f"Victron {mac[-5:]}"

    elif data.type == "ve_direct":
        # Victron VE.Direct text protocol over a USB-TTL cable. The
        # Victron-branded "VE.Direct to USB" cable shows up as a
        # /dev/ttyUSB* (FTDI or SiLabs chip inside); the same path
        # works for a DIY FTDI/CP2102 + JST pigtail rig. One cable
        # per Victron device; no slave concept here, so the device
        # row uses slave_id=0 as a placeholder.
        port = (data.port or "").strip()
        if not port or not port.startswith("/dev/"):
            raise HTTPException(
                status_code=400,
                detail="port must be a serial-device path like /dev/ttyUSB0",
            )
        baud = int(data.baudrate or 19200)
        if baud != 19200:
            # VE.Direct devices are fixed at 19200 baud. Reject silently
            # rather than letting a fat-fingered config produce confusing
            # silence on the dashboard.
            raise HTTPException(
                status_code=400,
                detail="VE.Direct devices are fixed at 19200 baud",
            )
        for t in current_transports:
            if (t.get("port") or "") == port and t.get("type") == "ve_direct":
                raise HTTPException(
                    status_code=409,
                    detail=f"port {port} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        leaf = port.rsplit("/", 1)[-1] or "serial"
        new_id = f"vedirect_{leaf}"
        block = {
            "type":     "ve_direct",
            "port":     port,
            "baudrate": baud,
        }
        default_label = f"Victron VE.Direct {leaf}"

    elif data.type == "ble_govee_advertise":
        # Govee H507x / H510x. Passive BLE, plaintext payload, no key.
        mac = (data.address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC (e.g. A4:C1:38:AA:BB:CC)",
            )
        for t in current_transports:
            if (t.get("address") or "").upper() == mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"address {mac} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        suffix = mac.replace(":", "").lower()[-4:]
        new_id = f"govee_{suffix[:2]}_{suffix[2:]}"
        block = {
            "type":    "ble_govee_advertise",
            "address": mac,
        }
        default_label = f"Govee {mac[-5:]}"

    elif data.type == "ble_ruuvi_advertise":
        # RuuviTag, passive BLE, format-5 plaintext payload, no key.
        mac = (data.address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC",
            )
        for t in current_transports:
            if (t.get("address") or "").upper() == mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"address {mac} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        suffix = mac.replace(":", "").lower()[-4:]
        new_id = f"ruuvi_{suffix[:2]}_{suffix[2:]}"
        block = {
            "type":    "ble_ruuvi_advertise",
            "address": mac,
        }
        default_label = f"Ruuvi {mac[-5:]}"

    elif data.type == "ble_mopeka_advertise":
        # Mopeka tank-level sensor, passive BLE, plaintext payload,
        # NO encryption key. Just a MAC. Auto-creates the device row
        # so the user doesn't have to (Mopeka has no slave-ID concept
        # any more than Victron does, one MAC, one device).
        mac = (data.address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC (e.g. EC:1B:BD:0A:12:34)",
            )
        for t in current_transports:
            if (t.get("address") or "").upper() == mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"address {mac} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        suffix = mac.replace(":", "").lower()[-4:]
        new_id = f"mopeka_{suffix[:2]}_{suffix[2:]}"
        block = {
            "type":    "ble_mopeka_advertise",
            "address": mac,
        }
        default_label = f"Mopeka {mac[-5:]}"

    elif data.type == "usbhid_voltronic":
        # Voltronic-family hybrid inverter (Axpert / MPP Solar / EG4
        # rebadges) over USB-HID. Default VID:PID matches the Cypress
        # HID chip every Voltronic firmware ships with; EG4 6500EX
        # variants overlay 0001:0000 so the wizard accepts either.
        # Treat 0 as a real value, EG4's PID literally is 0x0000.
        vid = int(0x0665 if data.vid is None else data.vid)
        pid = int(0x5161 if data.pid is None else data.pid)
        if not (0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF):
            raise HTTPException(
                status_code=400,
                detail="vid + pid must be 16-bit USB IDs (0–65535)",
            )
        serial = (data.serial_number or "").strip() or None
        # Dedupe per (vid, pid, serial_number). Two transports holding
        # the same HID handle would race on every command.
        for t in current_transports:
            if (t.get("type") == "usbhid_voltronic"
                    and int(t.get("vid", 0)) == vid
                    and int(t.get("pid", 0)) == pid
                    and (t.get("serial_number") or None) == serial):
                raise HTTPException(
                    status_code=409,
                    detail=f"HID device {vid:04x}:{pid:04x}"
                           + (f" serial {serial}" if serial else "")
                           + f" is already configured as transport "
                           f"{t.get('id')!r}",
                )
        new_id = f"voltronic_{vid:04x}_{pid:04x}"
        block = {
            "type": "usbhid_voltronic",
            "vid":  vid,
            "pid":  pid,
        }
        if serial:
            block["serial_number"] = serial
        default_label = "Hybrid inverter"
        mac = ""  # unused for HID; satisfies the shared log.info below

    elif data.type == "serial_modbus":
        port = (data.port or "").strip()
        # Tolerant validation: accept any /dev/* path; pyserial will
        # raise a clearer error than a regex check if the path doesn't
        # resolve to a real char device. We just block obviously-bad
        # input like empty strings.
        if not port or not port.startswith("/dev/"):
            raise HTTPException(
                status_code=400,
                detail="port must be a serial-device path like /dev/ttyUSB0",
            )
        baud = int(data.baudrate or 9600)
        if baud < 1200 or baud > 230400:
            raise HTTPException(
                status_code=400,
                detail="baudrate must be in [1200, 230400]; Renogy/Epever default 9600",
            )
        # Dedupe on (port). Two transports holding the same /dev/ttyUSB*
        # would race on every read.
        for t in current_transports:
            if (t.get("port") or "") == port and t.get("type") == "serial_modbus":
                raise HTTPException(
                    status_code=409,
                    detail=f"port {port} is already configured as transport "
                           f"{t.get('id')!r}",
                )
        # ID built from the tail of the device path so multiple USB
        # dongles get distinct, human-readable ids (serial_ttyUSB0).
        leaf = port.rsplit("/", 1)[-1] or "serial"
        new_id = f"serial_{leaf}"
        block = {
            "type":     "serial_modbus",
            "port":     port,
            "baudrate": baud,
        }
        default_label = f"USB-RS485 {leaf}"

    else:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported transport type {data.type!r}, wizard "
                   f"supports 'ble_modbus', 'serial_modbus', "
                   f"'ble_victron_advertise', 've_direct', "
                   f"'ble_mopeka_advertise', 'ble_govee_advertise', "
                   f"'ble_ruuvi_advertise' and 'usbhid_voltronic'",
        )

    # Bump id if collision (rare, different MAC, same tail).
    existing_ids = {t.get("id") for t in current_transports}
    base = new_id; n = 2
    while new_id in existing_ids:
        new_id = f"{base}_{n}"
        n += 1

    label = (data.label or "").strip() or default_label

    # ---- write ----
    block["id"]    = new_id
    block["label"] = label
    raw.setdefault("transports", []).append(block)

    # Victron Instant Readout transports auto-create their corresponding
    # device row. Unlike Modbus transports (where the user runs a slave-ID
    # scan to discover devices), a Victron passive transport IS the
    # device, one MAC, one device, one driver. Without this row the
    # daemon happily listens for advertisements but has no DeviceCfg to
    # bind the decoded data to. The wizard's slave-ID scan button
    # doesn't apply (Victron has no slave_id), so the user has no path
    # to add the device manually from the UI. Auto-create here so the
    # data starts flowing the moment Save is clicked.
    if data.type == "ble_victron_advertise":
        # Map victron-ble's device-class names → WattPost device_kind.
        # Filled when the wizard scan passes data.device_class; falls
        # back to "ac_charger" (a safe poll-anything kind) if we don't
        # know, the driver's payload-classifier will still parse,
        # just under a less-specific kind label.
        VICTRON_CLASS_TO_KIND = {
            "AcCharger":           "ac_charger",
            "BatteryMonitor":      "shunt",
            "SolarCharger":        "charge_controller",
            "OrionXS":             "dcdc_xs",
            "DcDcConverter":       "dcdc",
            "SmartLithium":        "smart_battery",
            "LynxSmartBMS":        "bms",
            "SmartBatteryProtect": "load_disconnect",
        }
        cls = (data.device_class or "").strip()
        kind = VICTRON_CLASS_TO_KIND.get(cls, "ac_charger")
        device_block = {
            "vendor":    "victron",
            "kind":      kind,
            "transport": new_id,
            # slave_id intentionally omitted, Victron BLE is MAC-addressed.
            # DeviceCfg.slave_id is `int | None = None` so this is valid.
            "label":     label,
        }
        raw.setdefault("devices", []).append(device_block)
        log.info("setup wizard: auto-added victron device kind=%s for transport %s",
                 kind, new_id)

    # Same auto-create logic for Mopeka: one MAC = one tank sensor,
    # no slave-ID scan possible. Without this row the listener decodes
    # adverts into the void.
    if data.type == "ble_mopeka_advertise":
        device_block = {
            "vendor":    "mopeka",
            "kind":      "tank",
            "transport": new_id,
            "label":     label,
        }
        raw.setdefault("devices", []).append(device_block)
        log.info("setup wizard: auto-added mopeka tank device for transport %s",
                 new_id)

    # Same pattern for Govee + Ruuvi, passive transports, one
    # device row per MAC.
    if data.type == "ble_govee_advertise":
        raw.setdefault("devices", []).append({
            "vendor":    "govee",
            "kind":      "ambient",
            "transport": new_id,
            "label":     label,
        })
        log.info("setup wizard: auto-added govee ambient device for transport %s",
                 new_id)
    if data.type == "ble_ruuvi_advertise":
        raw.setdefault("devices", []).append({
            "vendor":    "ruuvi",
            "kind":      "ambient",
            "transport": new_id,
            "label":     label,
        })
        log.info("setup wizard: auto-added ruuvi ambient device for transport %s",
                 new_id)

    # USB-HID Voltronic transports: one HID handle = one hybrid inverter.
    # Auto-create the device row so polling starts the moment Save is
    # pressed; the wizard has no slave-ID scan step for HID.
    if data.type == "usbhid_voltronic":
        raw.setdefault("devices", []).append({
            "vendor":    "voltronic",
            "kind":      "inverter",
            "transport": new_id,
            "slave_id":  1,
            "label":     label,
        })
        log.info("setup wizard: auto-added voltronic inverter device for transport %s",
                 new_id)

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: added transport %s type=%s address=%s label=%s",
             new_id, data.type, mac, label)

    # Background hot-reload, see _hot_reload_bg. Save returns
    # immediately; daemon health pill catches reload failures.
    asyncio.create_task(_hot_reload_bg(state))

    return {
        "ok": True,
        "id": new_id,
        "label": label,
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }


@get("/api/setup/transports")
async def list_setup_transports(state: State) -> dict[str, Any]:
    """Return configured transports with their live open/closed state.

    The notion of "open" varies by transport class:
      * `ble_modbus` / `serial_modbus`: a GATT or serial connection is
        actually held open. `_client.is_connected` (bleak) or the
        socket-style equivalent is the signal.
      * `ble_victron_advertise`: PASSIVE, no connection is ever held.
        The transport is "open" iff its passive listener is registered
        with the shared scanner AND we've seen an advertisement
        recently (within 60s). Otherwise we'd mark a perfectly-healthy
        Victron transport OFFLINE indefinitely (#159-adjacent UX bug
        from v0.0.77).
    Per-class fallbacks below; new transport types should add a
    branch here OR expose a uniform `is_connected` property on the
    Transport base class (preferred when we get around to it).
    """
    scheduler: PollScheduler = state["scheduler"]
    config: Config = state["config"]
    out: list[dict[str, Any]] = []
    for tcfg in config.transports:
        tid = tcfg.get("id")
        ttype = tcfg.get("type") or ""
        t = scheduler.get_transport(tid) if tid else None
        # Class-aware open-state probe. Passive BLE-advertise transports
        # have an extra "registered with the scanner" precondition on
        # top of the generic _latest_at freshness check; everything else
        # delegates to the shared helper in scheduler.
        from ..scheduler import _transport_is_open
        is_open = False
        if t is not None:
            if ttype in (
                "ble_victron_advertise", "ble_mopeka_advertise",
                "ble_govee_advertise", "ble_ruuvi_advertise",
            ):
                registered = bool(getattr(t, "_registered", False))
                last_at = float(getattr(t, "_latest_at", 0.0) or 0.0)
                fresh = (time.time() - last_at) < 60 if last_at else False
                is_open = registered and fresh
            else:
                is_open = _transport_is_open(t)
        # BLE transports expose `address` (MAC); serial transports
        # expose `port` (/dev/ttyUSB0). The wizard shows whichever is
        # present so a mixed-transport install reads cleanly.
        out.append({
            "id":      tid,
            "type":    ttype,
            "address": tcfg.get("address"),
            "port":    tcfg.get("port"),
            "open":    is_open,
        })
    return {"transports": out}


@delete("/api/setup/device", status_code=200)
async def delete_device_by_label(
    state: State, label: str, transport: str = "", slave_id: int | None = None,
) -> dict[str, Any]:
    """Delete a device by its `label` (the DB key shown on the card).

    Removes the matching config entry when `(transport, slave_id)` is
    supplied AND present, and *always* purges the device's DB rows. This
    is what clears a stale "silent" card that has no transport — a device
    removed from config (or one that never had a config entry, e.g. a
    Victron advert device that was reconfigured) leaves orphaned
    `device_meta`/`latest` rows the old `(transport, slave_id)`-keyed
    delete could never reach (#225 follow-up). Works for non-Modbus
    devices too, since it doesn't require a slave_id in the path.

    404 only when the device is in neither config nor the DB.
    """
    store = state["store"]
    config_path: str = state.get("config_path", "config.yaml")
    config_removed = 0

    # 1. Config removal — only when we have the config coordinates and a
    #    matching entry actually exists. A phantom (DB-only) device skips
    #    this and goes straight to the DB purge below.
    if transport and slave_id is not None:
        path = Path(config_path)
        raw = yaml.safe_load(path.read_text()) or {}
        devices = raw.get("devices") or []
        kept = [d for d in devices
                if not (d.get("transport") == transport
                        and int(d.get("slave_id", -1)) == slave_id)]
        config_removed = len(devices) - len(kept)
        if config_removed:
            raw["devices"] = kept
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
            tmp.replace(path)
            log.info("setup: removed device %r (transport=%s slave=%s)",
                     label, transport, slave_id)

    # 2. DB purge — always, keyed by the label (the DB device key).
    db_rows = await store.purge_device(label)

    if not config_removed and not db_rows:
        raise NotFoundException(
            f"no device {label!r} found in config or database"
        )

    # Reload only when the running config actually changed.
    if config_removed:
        asyncio.create_task(_hot_reload_bg(state))

    return {
        "ok": True,
        "label": label,
        "config_removed": config_removed,
        "db_rows_purged": db_rows,
        "restart_required": False,
        "reloaded": bool(config_removed),
    }


@delete("/api/setup/devices/{slave_id:int}", status_code=200)
async def delete_device(
    slave_id: int, state: State, transport: str = "",
) -> dict[str, Any]:
    """Remove a device from config.yaml + hot-reload so polling stops
    immediately. `transport` query param is required because the same
    slave_id can exist on different transports."""
    if not transport:
        raise HTTPException(
            status_code=400,
            detail="transport= query param required (slave_id alone isn't unique)",
        )
    config_path: str = state.get("config_path", "config.yaml")
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    devices = raw.get("devices") or []
    new_devices = [d for d in devices
                   if not (d.get("transport") == transport and int(d.get("slave_id", -1)) == slave_id)]
    if len(new_devices) == len(devices):
        raise NotFoundException(
            f"no device with slave_id={slave_id} on transport {transport!r}"
        )
    raw["devices"] = new_devices

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: removed device slave_id=%d on transport=%s",
             slave_id, transport)

    asyncio.create_task(_hot_reload_bg(state))
    return {
        "ok": True,
        "removed":          1,
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }


class EditTransportRequest(msgspec.Struct, kw_only=True):
    """In-place edits to an existing transport. None = leave alone.
    Editable fields are exactly the ones that change in the field:
    the Victron encryption key when the device is factory-reset, the
    BLE MAC when a dongle is replaced, the serial port path when a
    USB-RS485 adapter moves to a different tty. The transport `id`
    is the stable handle and is never renamed, devices reference
    it, history rows are keyed off it, MQTT topics include it."""
    address:        str | None = None
    encryption_key: str | None = None
    port:           str | None = None


@patch("/api/setup/transports/{transport_id:str}", status_code=200)
async def edit_setup_transport(
    transport_id: str, data: EditTransportRequest, state: State,
) -> dict[str, Any]:
    """In-place edit of an existing transport's mutable fields.
    Saves the customer from the delete-and-recreate dance every time
    Victron rotates the BLE key after a factory reset or a BT-2 gets
    replaced. Hot-reloads on success so the connection re-opens with
    the new credentials without a daemon restart."""
    config_path: str = state.get("config_path", "config.yaml")
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    transports = raw.get("transports") or []
    target = next((t for t in transports if t.get("id") == transport_id), None)
    if target is None:
        raise NotFoundException(f"no transport with id {transport_id!r}")

    t_type = target.get("type")
    changes: dict[str, Any] = {}

    if data.address is not None:
        new_mac = data.address.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", new_mac):
            raise HTTPException(
                status_code=400,
                detail="address must be a Bluetooth MAC (e.g. CC:45:A5:83:B7:42)",
            )
        # Reject duplicates with OTHER transports (same MAC on the same
        # transport-id is a no-op rewrite, that's fine).
        for t in transports:
            if t.get("id") == transport_id:
                continue
            if (t.get("address") or "").upper() == new_mac:
                raise HTTPException(
                    status_code=409,
                    detail=f"MAC {new_mac} is already configured on transport "
                           f"{t.get('id')!r}",
                )
        changes["address"] = new_mac

    if data.encryption_key is not None:
        if t_type != "ble_victron_advertise":
            raise HTTPException(
                status_code=400,
                detail="encryption_key only applies to ble_victron_advertise transports",
            )
        key_clean = (data.encryption_key
                     .strip()
                     .replace(" ", "")
                     .replace(":", "")
                     .replace("-", "")
                     .lower())
        if not re.fullmatch(r"[0-9a-f]{32}", key_clean):
            raise HTTPException(
                status_code=400,
                detail="encryption_key must be 32 hex chars (VictronConnect "
                       "→ Product info → Show device key)",
            )
        changes["encryption_key"] = key_clean

    if data.port is not None:
        if t_type != "serial_modbus":
            raise HTTPException(
                status_code=400,
                detail="port only applies to serial_modbus transports",
            )
        new_port = data.port.strip()
        if not new_port.startswith("/dev/"):
            raise HTTPException(
                status_code=400,
                detail="port must be an absolute /dev/... path",
            )
        changes["port"] = new_port

    if not changes:
        raise HTTPException(status_code=400, detail="no editable fields supplied")

    # Apply, persist, hot-reload.
    target.update(changes)
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: edited transport %s (changed: %s)",
             transport_id, ", ".join(changes.keys()))
    asyncio.create_task(_hot_reload_bg(state))
    return {
        "ok":               True,
        "transport_id":     transport_id,
        "changed":          list(changes.keys()),
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }


@delete("/api/setup/transports/{transport_id:str}", status_code=200)
async def delete_setup_transport(
    transport_id: str, state: State,
) -> dict[str, Any]:
    """Remove a transport AND every device that referenced it, then
    hot-reload so the BLE connection closes immediately. Cascade is
    deliberate, a transport with orphan devices wouldn't poll
    anyway, and leaving them in the yaml is confusing."""
    config_path: str = state.get("config_path", "config.yaml")
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}

    transports = raw.get("transports") or []
    keep_t = [t for t in transports if t.get("id") != transport_id]
    if len(keep_t) == len(transports):
        raise NotFoundException(f"no transport with id {transport_id!r}")

    devices = raw.get("devices") or []
    keep_d  = [d for d in devices if d.get("transport") != transport_id]
    dropped = len(devices) - len(keep_d)

    raw["transports"] = keep_t
    raw["devices"]    = keep_d

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: removed transport %s (also dropped %d child devices)",
             transport_id, dropped)

    asyncio.create_task(_hot_reload_bg(state))
    return {
        "ok": True,
        "transport_id":     transport_id,
        "devices_removed":  dropped,
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }


@get("/api/setup/known_devices")
async def known_devices(state: State) -> dict[str, Any]:
    config: Config = state["config"]
    return {
        "devices": [
            {"transport": d.transport, "slave_id": d.slave_id,
             "vendor": d.vendor, "kind": d.kind, "label": d.label}
            for d in config.devices
        ]
    }


class ProbeRequest(msgspec.Struct):
    transport: str
    slave_ids: list[int] | None = None


async def _probe_one(t, sid: int) -> dict[str, Any]:
    """Probe a single slave ID. Tries each model-register guess in
    order, first one that returns plausible ASCII wins."""
    if not (1 <= sid <= 247):
        return {"slave_id": sid, "alive": False, "vendor": None,
                "kind": None, "model": None, "error": "id out of range"}
    err: str | None = None
    for v, suggested_kind, register, count in _MODEL_PROBES:
        try:
            frame = build_read_holding(sid, register, count)
            # 2.5 s timeout, first probe after a fresh BLE connect
            # often hits ~1.5 s on its first round-trip before settling.
            # 1.2 s was clipping legit responses on cold links.
            resp = await t.request(
                frame, expected_read_response_len(count), timeout=2.5,
            )
            verify_response(resp, sid)
        except Exception as e:
            err = type(e).__name__
            log.debug("probe slave=%d vendor=%s reg=%d failed: %s",
                      sid, v, register, e)
            continue
        payload = resp[3:3 + count * 2]
        if not _likely_ascii(payload):
            err = "non-ascii response"
            continue
        text = _clean_ascii(payload)
        kind = _classify_renogy(text) or suggested_kind if v == "renogy" \
                else suggested_kind
        return {"slave_id": sid, "alive": True, "vendor": v, "kind": kind,
                "model": text, "error": None}
    return {"slave_id": sid, "alive": False, "vendor": None, "kind": None,
            "model": None, "error": err}


@post("/api/setup/probe")
async def probe(data: ProbeRequest, state: State) -> Stream:
    """Sweep slave IDs on a transport. Streams one NDJSON record per
    probe, so the wizard UI can show "Probing #16 → found Rover
    RVR40" live instead of staring at a spinner for 60s while the
    full sweep finishes. Last record is a `{"done": true, ...}`
    summary.

    Reopens the transport at scan start so an idle-dropped BLE link
    gets reconnected automatically, the user shouldn't have to
    restart the daemon to scan a second time.

    The transport's own lock serialises against the scheduler's polls."""
    scheduler: PollScheduler = state["scheduler"]
    t = scheduler.get_transport(data.transport)
    if t is None:
        raise NotFoundException(f"transport {data.transport!r} not open")

    ids = tuple(data.slave_ids) if data.slave_ids else DEFAULT_PROBE_IDS

    async def gen():
        # Reopen the link if it's dropped, idle BLE connections can
        # die after a few minutes of no traffic.
        reopened = False
        try:
            if hasattr(t, "_client") and (t._client is None or not t._client.is_connected):
                yield json.dumps({"event": "reopening",
                                  "transport": data.transport}).encode() + b"\n"
                await t.open()
                reopened = True
        except Exception as e:
            yield json.dumps({
                "event": "open_failed",
                "error": f"{type(e).__name__}: {e}",
            }).encode() + b"\n"
            yield json.dumps({"done": True, "alive_count": 0,
                              "total": 0}).encode() + b"\n"
            return

        yield json.dumps({"event": "start", "total": len(ids),
                          "reopened": reopened}).encode() + b"\n"

        alive_count = 0
        for idx, sid in enumerate(ids, start=1):
            yield json.dumps({"event": "probing", "slave_id": sid,
                              "index": idx, "total": len(ids)}).encode() + b"\n"
            result = await _probe_one(t, sid)
            if result.get("alive"):
                alive_count += 1
            yield (json.dumps({"event": "result", **result}).encode()
                   + b"\n")
            # Small breather between probes so we don't starve the
            # live poll.
            await asyncio.sleep(0.05)

        yield json.dumps({"done": True, "alive_count": alive_count,
                          "total": len(ids)}).encode() + b"\n"

    return Stream(gen(), media_type="application/x-ndjson")


class AddDeviceRequest(msgspec.Struct):
    transport: str
    vendor: str
    kind: str
    slave_id: int
    label: str | None = None


@post("/api/setup/add_device")
async def add_device(data: AddDeviceRequest, state: State) -> dict[str, Any]:
    """Append a new device to config.yaml after validating it. Returns a
    flag the SPA uses to show a "restart required" banner, the running
    Poller is configured at boot, so it won't poll the new device until
    the daemon restarts."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    # ---- validation ----
    if data.vendor not in VENDORS:
        raise HTTPException(status_code=400, detail=f"unknown vendor {data.vendor!r}")
    if data.kind not in VENDORS[data.vendor].drivers:
        raise HTTPException(
            status_code=400,
            detail=f"vendor {data.vendor!r} has no driver for kind {data.kind!r}",
        )
    if not any(t.get("id") == data.transport for t in config.transports):
        raise HTTPException(status_code=400, detail=f"unknown transport {data.transport!r}")
    if not (1 <= data.slave_id <= 247):
        raise HTTPException(status_code=400, detail="slave_id must be 1..247")
    for d in config.devices:
        if d.transport == data.transport and d.slave_id == data.slave_id:
            raise HTTPException(
                status_code=409,
                detail=f"slave {data.slave_id} already configured on transport "
                       f"{data.transport!r} as {d.label or d.vendor + '/' + d.kind}",
            )

    label = data.label or f"{data.kind}_{data.slave_id}"

    # ---- write ----
    # Round-trip through PyYAML preserves data shape but loses comments;
    # acceptable for a config that's now wizard-managed. We back up the
    # previous version so the user can revert by hand if needed.
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("devices", []).append({
        "vendor": data.vendor,
        "kind": data.kind,
        "transport": data.transport,
        "slave_id": data.slave_id,
        "label": label,
    })

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: added %s/%s @ %s slave=%d label=%s",
             data.vendor, data.kind, data.transport, data.slave_id, label)

    # Schedule the hot-reload to run in the background, it can take
    # ~5s while the in-flight poll cycle drains, and there's no
    # reason for the user's "Saved" feedback to wait that long. The
    # device starts polling within a few seconds after the response
    # returns. Reload failures show up via the dashboard's daemon
    # health pill, not the save response. See _hot_reload_bg for the
    # serialization story.
    asyncio.create_task(_hot_reload_bg(state))

    return {
        "ok": True,
        "label": label,
        # Decoupled flow, we always assume reload will succeed
        # (we just wrote the config we're reloading). If it doesn't,
        # the daemon health pill catches it. Pre-decoupling these
        # two fields were derived from the await result.
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }
