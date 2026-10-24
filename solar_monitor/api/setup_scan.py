"""Setup wizard: device discovery scans (BLE, USB serial, HID, LAN peers) and model classification."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

import json

import msgspec
from litestar import get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException


log = logging.getLogger(__name__)


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
        disc_was_running = False
        try:
            from ..transport import ble_discovery
            disc_was_running = await ble_discovery.scanner().pause()
        except Exception:
            log.debug("ble_scan: discovery scanner pause skipped (not in use)")
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
            if disc_was_running:
                try:
                    from ..transport import ble_discovery
                    await ble_discovery.scanner().resume()
                except Exception:
                    log.warning("ble_scan: discovery scanner resume failed")
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
