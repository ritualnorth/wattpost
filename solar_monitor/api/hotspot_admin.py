"""HTTP endpoints for the appliance-as-WiFi-AP (Pillar 3, scaffold).

Surface:
  GET  /api/hotspot/status   service + live AP state for the panel
  PUT  /api/hotspot/config   set/clear the `hotspot:` config block
  POST /api/hotspot/on       bring the AP up now (manual control)
  POST /api/hotspot/off      bring the AP down now (manual control)

Manual on/off act on the live HotspotService the scheduler holds; they
return 409 until a `hotspot:` block exists (PUT /config creates it and
hot-reloads so the service materialises without a restart). Same
save-config + background hot-reload pattern as weather_admin.

Auto-handoff (handoff.py) and the captive portal (captive_portal flag +
api/captive.py) are wired in separately.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..config import Config, HotspotCfg
from ..scheduler import PollScheduler
from .setup import _hot_reload_bg

log = logging.getLogger(__name__)


class HotspotConfigPayload(msgspec.Struct, kw_only=True):
    # All optional so the panel can PATCH-style send only what changed;
    # absent fields fall back to the current value (or the struct
    # default when first creating the block).
    enabled: bool | None = None
    auto_handoff: bool | None = None
    captive_portal: bool | None = None
    ssid: str | None = None
    # Empty string => open network; non-empty must be 8..63 chars (WPA2).
    # `None` means "leave unchanged" so the panel never has to re-send
    # (and re-expose) the existing passphrase.
    password: str | None = None
    band: str | None = None
    channel: int | None = None
    interface: str | None = None


def _save_config(config_path: str, mutator) -> None:
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw = mutator(raw)
    if raw is None:
        raise RuntimeError("config mutator returned None")
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)


@get("/api/hotspot/status")
async def get_hotspot_status(state: State) -> dict[str, Any]:
    """Service + live AP state. `{configured: false}` when there's no
    `hotspot:` block yet, the panel shows the enable form in that case."""
    scheduler: PollScheduler = state["scheduler"]
    svc = scheduler.hotspot
    if svc is None:
        return {"configured": False, "nmcli_available": shutil.which("nmcli") is not None}
    payload = await svc.status()
    payload["configured"] = True
    return payload


@put("/api/hotspot/config")
async def update_hotspot_config(
    data: HotspotConfigPayload, state: State,
) -> dict[str, Any]:
    """Create/update (or, with enabled=false + cleared, keep) the
    `hotspot:` block. Merges onto the current config so the panel can
    send partial updates. Hot-reloads so the scheduler picks up the new
    service without a restart."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    cur = config.hotspot or HotspotCfg()
    ssid = data.ssid if data.ssid is not None else cur.ssid
    band = data.band if data.band is not None else cur.band
    channel = data.channel if data.channel is not None else cur.channel
    interface = data.interface if data.interface is not None else cur.interface
    enabled = data.enabled if data.enabled is not None else cur.enabled
    auto_handoff = data.auto_handoff if data.auto_handoff is not None else cur.auto_handoff
    captive_portal = data.captive_portal if data.captive_portal is not None else cur.captive_portal
    # password: None => keep existing; "" => explicitly open the network.
    password = data.password if data.password is not None else cur.password

    if not ssid or len(ssid) > 32:
        raise HTTPException(status_code=400, detail="ssid must be 1..32 chars")
    if password and not (8 <= len(password) <= 63):
        raise HTTPException(
            status_code=400,
            detail="password must be 8..63 chars (WPA2) or empty (open network)",
        )
    if band not in ("bg", "a"):
        raise HTTPException(status_code=400, detail="band must be 'bg' (2.4GHz) or 'a' (5GHz)")
    if not (1 <= channel <= 196):
        raise HTTPException(status_code=400, detail="channel out of range")

    new = HotspotCfg(
        enabled=enabled, auto_handoff=auto_handoff, captive_portal=captive_portal,
        ssid=ssid, password=password,
        band=band, channel=channel, interface=interface,
        connection_name=cur.connection_name,
    )
    config.hotspot = new

    def _mutate(raw):
        raw["hotspot"] = {
            "enabled":         new.enabled,
            "auto_handoff":    new.auto_handoff,
            "captive_portal":  new.captive_portal,
            "ssid":            new.ssid,
            "password":        new.password,
            "band":            new.band,
            "channel":         new.channel,
            "interface":       new.interface,
            "connection_name": new.connection_name,
        }
        return raw

    _save_config(config_path, _mutate)
    log.info("hotspot configured (ssid=%s band=%s ch=%d enabled=%s auto_handoff=%s)",
             new.ssid, new.band, new.channel, new.enabled, new.auto_handoff)
    # Hot-reload rebuilds the scheduler's HotspotService from the new
    # block. If enabled=true it auto-brings-up on the reload's start().
    asyncio.create_task(_hot_reload_bg(state))
    return {"ok": True, "configured": True, "restart_required": False}


@post("/api/hotspot/on")
async def hotspot_on(state: State) -> dict[str, Any]:
    """Bring the AP up now, independent of the `enabled` flag."""
    scheduler: PollScheduler = state["scheduler"]
    svc = scheduler.hotspot
    if svc is None:
        raise HTTPException(
            status_code=409,
            detail="hotspot not configured; PUT /api/hotspot/config first",
        )
    # nmcli/radio absent is a host *precondition*, not a server fault.
    # Return 409 so it doesn't trip the app's 5xx traceback logger.
    if not svc.is_available(svc.cfg):
        raise HTTPException(
            status_code=409,
            detail="NetworkManager (nmcli) not available on host; see docs/hotspot.md",
        )
    result = await svc.activate()
    if not result.get("ok"):
        # nmcli was present but the bring-up genuinely failed — a 502
        # (and its logged traceback) is warranted here.
        raise HTTPException(status_code=502, detail=str(result.get("error") or "activation failed"))
    return {"ok": True, **await svc.status()}


@post("/api/hotspot/off")
async def hotspot_off(state: State) -> dict[str, Any]:
    """Bring the AP down now."""
    scheduler: PollScheduler = state["scheduler"]
    svc = scheduler.hotspot
    if svc is None:
        raise HTTPException(
            status_code=409,
            detail="hotspot not configured; PUT /api/hotspot/config first",
        )
    if not svc.is_available(svc.cfg):
        raise HTTPException(
            status_code=409,
            detail="NetworkManager (nmcli) not available on host; see docs/hotspot.md",
        )
    result = await svc.deactivate()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=str(result.get("error") or "deactivation failed"))
    return {"ok": True, **await svc.status()}
