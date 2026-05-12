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

import msgspec
import yaml
from litestar import get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from ..config import Config
from ..modbus import build_read_holding, expected_read_response_len, verify_response
from ..scheduler import PollScheduler
from ..vendors import VENDORS

log = logging.getLogger(__name__)


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


@post("/api/setup/probe")
async def probe(data: ProbeRequest, state: State) -> dict[str, Any]:
    """Sweep slave IDs on a live transport. For each ID, read a model-name
    register; if it answers with plausible ASCII, record vendor/kind/model.
    The transport's own lock serialises against the scheduler's polls."""
    scheduler: PollScheduler = state["scheduler"]
    t = scheduler.get_transport(data.transport)
    if t is None:
        raise NotFoundException(f"transport {data.transport!r} not open")

    ids = tuple(data.slave_ids) if data.slave_ids else DEFAULT_PROBE_IDS
    results: list[dict[str, Any]] = []
    for sid in ids:
        if not (1 <= sid <= 247):
            results.append({"slave_id": sid, "alive": False, "error": "id out of range"})
            continue
        alive = False
        vendor: str | None = None
        kind: str | None = None
        model: str | None = None
        err: str | None = None

        for v, suggested_kind, register, count in _MODEL_PROBES:
            try:
                frame = build_read_holding(sid, register, count)
                resp = await t.request(
                    frame, expected_read_response_len(count), timeout=1.2
                )
                verify_response(resp, sid)
            except Exception as e:
                err = type(e).__name__
                continue

            payload = resp[3:3 + count * 2]
            if not _likely_ascii(payload):
                err = "non-ascii response"
                continue

            text = _clean_ascii(payload)
            alive = True
            vendor = v
            model = text
            if v == "renogy":
                kind = _classify_renogy(text) or suggested_kind
            else:
                kind = suggested_kind
            err = None
            break

        results.append({
            "slave_id": sid,
            "alive": alive,
            "vendor": vendor,
            "kind": kind,
            "model": model,
            "error": err,
        })
        # Small breather between probes so we don't starve the live poll.
        await asyncio.sleep(0.05)

    return {"transport": data.transport, "results": results}


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

    return {
        "ok": True,
        "label": label,
        "restart_required": True,
        "backup_path": str(backup),
    }
