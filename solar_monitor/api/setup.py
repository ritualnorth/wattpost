"""Setup wizard endpoints.

The SPA's Setup route uses these to scan for new devices on an already-open
transport, identify them by reading the vendor-specific model register, and
append a validated device entry to config.yaml. The live daemon keeps
polling on its own loop while a probe runs — the transport's request lock
serialises so the two callers don't collide.

A daemon restart is required for new devices to start polling. The endpoint
returns a flag the SPA uses to show the restart prompt.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import json

import msgspec
import yaml
from litestar import delete, get, post
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

    Failures are logged but not surfaced to the original caller —
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
    daemon — flow ends with "polling started" instead of "now click
    Restart daemon".

    Strategy:
      1. Re-read config.yaml from disk.
      2. await scheduler.stop()  — waits for any in-flight poll.
      3. Construct a new PollScheduler with the same Store (so the
         database connection survives and history is uninterrupted).
      4. await new_scheduler.start().
      5. Swap into app.state. Subsequent endpoints see the new
         scheduler + config.

    Returns a dict so the caller can include reload status / errors
    in their own response. Never raises — if reload fails, we keep
    the old scheduler stopped and log the failure; the dashboard
    surfaces it via the daemon-stopped state on the next poll.
    """
    from ..config import load_config
    from ..scheduler import PollScheduler
    config_path: str = state.get("config_path", "config.yaml")

    try:
        new_config = load_config(config_path)
    except Exception as e:
        log.exception("hot-reload: config parse failed (%s) — keeping old scheduler", e)
        return {"reloaded": False, "error": f"config parse: {e}"}

    old_scheduler: PollScheduler = state["scheduler"]
    store = state["store"]
    interval = old_scheduler.interval_seconds
    maintenance = getattr(old_scheduler, "maintenance_interval_seconds", 600)

    try:
        await old_scheduler.stop()
    except Exception as e:
        log.exception("hot-reload: old scheduler stop raised (%s) — continuing anyway", e)

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
        # place so health endpoints can read its dead state — better
        # than silently swallowing the start failure.
        return {"reloaded": False, "error": f"start failed: {e}"}

    state["scheduler"] = new_scheduler
    state["config"]    = new_config
    log.info("hot-reload: scheduler swapped — %d transports, %d devices, %d alerts",
             len(new_config.transports), len(new_config.devices), len(new_config.alerts))
    return {"reloaded": True}


# Slave IDs we try by default — covers Renogy factory conventions:
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
# usable ASCII.
_MODEL_PROBES: list[tuple[str, str, int, int]] = [
    # (vendor, suggested_kind, register, word_count)
    ("renogy", "smart_battery",    5122, 8),
    ("renogy", "charge_controller",  12, 8),
]

# Per-vendor heuristics that map a model string → recommended device kind.
def _classify_renogy(model: str) -> str | None:
    m = (model or "").upper()
    if m.startswith("RBT") or "LFP" in m:
        return "smart_battery"
    if any(s in m for s in ("ROVER", "WANDER", "ADVENTUR", "RNG-CTRL", "RNG-")):
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


@get("/api/setup/ble_status")
async def ble_status() -> dict[str, Any]:
    """List BLE adapters visible to the daemon. Used by the Setup
    wizard to surface "Bluetooth is reaching the container / Pi"
    vs "no radio detected — check the dongle".

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


class BleScanRequest(msgspec.Struct):
    seconds: int = 8


@post("/api/setup/ble_scan")
async def ble_scan(data: BleScanRequest) -> dict[str, Any]:
    """Scan BLE advertisements for `seconds`. Returns a list of every
    advertising device the host can see — MAC, name (if advertised),
    RSSI. The UI dedupes and shows them so the user can pick their
    Renogy BT-2 dongle by name pattern (`BT-TH-…`) or by MAC printed
    on the dongle itself.

    Clamped to 2..30 seconds. Eight is the default — long enough that
    a slow-advertising dongle shows up, short enough that the user
    doesn't think the page froze.
    """
    secs = max(2, min(30, int(data.seconds or 8)))
    try:
        from bleak import BleakScanner
    except ImportError:
        raise HTTPException(status_code=500, detail="bleak not available")

    log.info("ble_scan: discover for %ds", secs)
    try:
        devices = await BleakScanner.discover(timeout=secs)
    except Exception as e:
        # Most failures here are bluez transport problems: adapter
        # powered off, DBus passthrough broken in Docker, etc.
        # Surface the message — it's the most useful debugging hint.
        raise HTTPException(
            status_code=500,
            detail=f"BLE scan failed: {e}. Check the BLE adapter "
                   f"status in step 0 of the wizard.",
        )
    out = []
    for d in devices:
        out.append({
            "address": (d.address or "").upper(),
            "name":    d.name or None,
            "rssi":    getattr(d, "rssi", None),
        })
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
    log.info("ble_scan: %d devices", len(out))
    return {"devices": out, "scanned_seconds": secs}


class AddTransportRequest(msgspec.Struct):
    address: str           # BT MAC of the dongle
    label: str | None = None
    type: str = "ble_modbus"   # Renogy BT-2 default; only kind today


@post("/api/setup/transports/add")
async def add_transport(data: AddTransportRequest, state: State) -> dict[str, Any]:
    """Append a new BLE transport to config.yaml. UI-driven replacement
    for editing yaml by hand. Daemon restart required for the new
    transport to start polling — the response carries that flag so
    the SPA shows the "restart daemon" banner."""
    config_path: str = state.get("config_path", "config.yaml")
    path = Path(config_path)

    # ---- validation ----
    mac = (data.address or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
        raise HTTPException(
            status_code=400,
            detail="address must be a Bluetooth MAC (e.g. CC:45:A5:83:B7:42)",
        )
    if data.type != "ble_modbus":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported transport type {data.type!r} — only "
                   f"'ble_modbus' is supported in the UI today",
        )

    # Read current yaml from disk (not the boot-time `state["config"]`)
    # so duplicate detection sees any transports added since boot via
    # this same endpoint. Yaml is the source of truth.
    raw = yaml.safe_load(path.read_text()) or {}
    current_transports = raw.get("transports") or []

    # Reject duplicates so we don't end up with two transports racing
    # for the same BT-2 dongle.
    for t in current_transports:
        if (t.get("address") or "").upper() == mac:
            raise HTTPException(
                status_code=409,
                detail=f"address {mac} is already configured as transport "
                       f"{t.get('id')!r}",
            )

    # Generate a stable id from the MAC suffix. ble_b7_42 is human-
    # readable when there's more than one dongle on the same site.
    suffix = mac.replace(":", "").lower()[-4:]
    new_id = f"ble_{suffix[:2]}_{suffix[2:]}"
    # Bump if there's a collision (different MAC, same suffix).
    existing_ids = {t.get("id") for t in current_transports}
    base = new_id; n = 2
    while new_id in existing_ids:
        new_id = f"{base}_{n}"
        n += 1

    label = (data.label or "").strip() or f"BLE dongle {mac[-5:]}"

    # ---- write ----
    raw.setdefault("transports", []).append({
        "id":      new_id,
        "type":    data.type,
        "address": mac,
        "label":   label,
    })

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("setup wizard: added transport %s type=%s address=%s label=%s",
             new_id, data.type, mac, label)

    # Background hot-reload — see _hot_reload_bg. Save returns
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
    """Return configured transports with their live open/closed state."""
    scheduler: PollScheduler = state["scheduler"]
    config: Config = state["config"]
    out: list[dict[str, Any]] = []
    for tcfg in config.transports:
        tid = tcfg.get("id")
        t = scheduler.get_transport(tid) if tid else None
        client = getattr(t, "_client", None) if t else None
        out.append({
            "id": tid,
            "type": tcfg.get("type"),
            "address": tcfg.get("address"),
            "open": bool(client and getattr(client, "is_connected", False)),
        })
    return {"transports": out}


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


@delete("/api/setup/transports/{transport_id:str}", status_code=200)
async def delete_setup_transport(
    transport_id: str, state: State,
) -> dict[str, Any]:
    """Remove a transport AND every device that referenced it, then
    hot-reload so the BLE connection closes immediately. Cascade is
    deliberate — a transport with orphan devices wouldn't poll
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
    order — first one that returns plausible ASCII wins."""
    if not (1 <= sid <= 247):
        return {"slave_id": sid, "alive": False, "vendor": None,
                "kind": None, "model": None, "error": "id out of range"}
    err: str | None = None
    for v, suggested_kind, register, count in _MODEL_PROBES:
        try:
            frame = build_read_holding(sid, register, count)
            # 2.5 s timeout — first probe after a fresh BLE connect
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
    probe — so the wizard UI can show "Probing #16 → found Rover
    RVR40" live instead of staring at a spinner for 60s while the
    full sweep finishes. Last record is a `{"done": true, ...}`
    summary.

    Reopens the transport at scan start so an idle-dropped BLE link
    gets reconnected automatically — the user shouldn't have to
    restart the daemon to scan a second time.

    The transport's own lock serialises against the scheduler's polls."""
    scheduler: PollScheduler = state["scheduler"]
    t = scheduler.get_transport(data.transport)
    if t is None:
        raise NotFoundException(f"transport {data.transport!r} not open")

    ids = tuple(data.slave_ids) if data.slave_ids else DEFAULT_PROBE_IDS

    async def gen():
        # Reopen the link if it's dropped — idle BLE connections can
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
    flag the SPA uses to show a "restart required" banner — the running
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

    # Schedule the hot-reload to run in the background — it can take
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
        # Decoupled flow — we always assume reload will succeed
        # (we just wrote the config we're reloading). If it doesn't,
        # the daemon health pill catches it. Pre-decoupling these
        # two fields were derived from the await result.
        "restart_required": False,
        "reloaded":         True,
        "reload_error":     None,
        "backup_path":      str(backup),
    }
