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

from ..fsutil import atomic_write_text as _atomic_write_text
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
from .setup_scan import _clean_ascii, _classify_renogy, _likely_ascii

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
    _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
            _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
    _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
    _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
    _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
    _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))
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
