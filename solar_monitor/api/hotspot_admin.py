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
import json
import logging
import shutil
import time
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
    # Apply in place rather than rebuilding the whole scheduler. The old
    # path ran _hot_reload, which stops + reconstructs the PollScheduler —
    # that tears down and reconnects EVERY BLE transport (10-30s of lost
    # polling) just to change a WiFi flag, and left status() reading the
    # pre-reload service for the ~5s the rebuild took, so a UI toggle
    # appeared to bounce back. Setting the live service's cfg is instant,
    # and the auto-handoff monitor wraps the same instance + reads .cfg
    # live, so both views stay consistent immediately.
    scheduler: PollScheduler = state["scheduler"]
    svc = scheduler.hotspot
    if svc is not None:
        svc.cfg = new
        asyncio.create_task(_bg_apply(scheduler, svc, new))
    else:
        # No live service to update (the scheduler always builds one, so
        # this is defensive) — fall back to a full reload to materialise it.
        asyncio.create_task(_hot_reload_bg(state))
    return {"ok": True, "configured": True, "restart_required": False}


async def _bg_apply(scheduler: PollScheduler, svc: Any, new: HotspotCfg) -> None:
    """Reconcile the live hotspot to a new cfg without a scheduler rebuild
    (which would tear down every BLE transport). Runs in the background so
    the config PUT returns immediately; `svc.cfg` is already updated
    synchronously by the caller, so status() is correct the instant the
    PUT returns regardless of this task's timing.

    Two effects to reproduce from the old full hot-reload:
      1. The auto-handoff monitor's *loop* is started once (at boot) from
         should_run(); a runtime auto_handoff/onboarding/enabled toggle
         must restart it or the change wouldn't take until a daemon
         restart. stop()+start() re-evaluates should_run() against the new
         cfg (stop() is safe when not running; start() no-ops when it
         shouldn't run). Without this, turning auto_handoff ON at runtime
         silently did nothing.
      2. Mirror HotspotService.start(): auto-raise only when enabled.
         enabled=false leaves a running AP alone (NM holds it), exactly as
         the old rebuild's start() did; when enabled we (re-)activate so a
         live AP picks up a changed SSID/band/psk."""
    handoff = getattr(scheduler, "hotspot_handoff", None)
    if handoff is not None:
        try:
            await handoff.stop()
            await handoff.start()
        except Exception:
            log.exception("hotspot: handoff-monitor reconcile after config save failed")
    if new.enabled and svc.is_available(new):
        try:
            await svc.activate()
        except Exception:
            log.exception("hotspot: background activate after config save failed")


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


# ---- WiFi station provisioning (join a home LAN) ------------------------
# The inverse of the hotspot: scan for nearby networks and join one, so a
# box in AP mode (or flashed without Imager WiFi) can be put on the LAN from
# the dashboard. Privileged nmcli work runs in wattpost-helperd; here we
# just validate, call the socket, and shape the response.


class WifiJoinPayload(msgspec.Struct, kw_only=True):
    ssid: str
    # Omit / empty for an open network; WPA needs 8..63 chars.
    password: str | None = None
    # Optional static IPv4. Omit static_ip for DHCP. When set, prefix
    # defaults to 24; gateway + dns (comma/space-separated) optional.
    static_ip: str | None = None
    prefix: int | None = None
    gateway: str | None = None
    dns: str | None = None


async def _hotspot_active(state: State) -> bool:
    """True when this box is currently beaconing its own AP. A single
    radio can't beacon and scan at once, so when this is True a live scan
    finds nothing and the client should use the bounce flow below."""
    scheduler = state.get("scheduler") if hasattr(state, "get") else state["scheduler"]
    svc = getattr(scheduler, "hotspot", None)
    if svc is None:
        return False
    try:
        return bool((await svc.status()).get("active"))
    except Exception:
        return False


@get("/api/network/wifi/scan")
async def wifi_scan(state: State) -> dict[str, Any]:
    """List visible WiFi networks for the join picker. Returns
    `supported: false` on installs with no host network control (Docker,
    dev), so the UI can hide the panel rather than show an error.

    `hotspot_active` tells the client whether the radio is busy as an AP;
    if so a live scan can't run and the UI should offer the AP-bounce
    scan (POST .../scan/ap-bounce) instead."""
    from .. import helper_client
    if not helper_client.is_available():
        return {"supported": False, "networks": [], "error": None, "hotspot_active": False}
    hotspot_active = await _hotspot_active(state)
    if hotspot_active:
        # Radio is beaconing the AP; a live scan would block ~3s and find
        # nothing. Signal the client to use the bounce flow instead.
        return {"supported": True, "networks": [], "error": None, "hotspot_active": True}
    r = helper_client.call("wifi_scan")
    if not r.get("ok"):
        # Surface the reason in-band (a 5xx detail would be masked) so the
        # panel can say e.g. "can't scan while the hotspot is active".
        return {"supported": True, "networks": [],
                "error": (r.get("err") or "scan failed").strip(),
                "hotspot_active": hotspot_active}
    try:
        networks = json.loads(r.get("out") or "[]")
    except (ValueError, TypeError):
        networks = []
    return {"supported": True, "networks": networks, "error": None,
            "hotspot_active": hotspot_active}


# --- AP-mode scan (single radio: drop AP → scan → restore AP) ------------
# When the box is in hotspot mode the radio can't scan and beacon at once,
# so a fresh scan means briefly tearing the AP down. The client that asked
# is usually connected THROUGH that AP, so we can't just bounce it inside
# the request (the response would never arrive). Instead: kick a background
# bounce, return immediately, and let the client poll for the cached result
# once its device has rejoined the AP. The AP restore is bulletproofed in a
# finally — it's the box's only link on an off-grid site.
_AP_SCAN: dict[str, Any] = {"state": "idle", "ts": 0.0, "networks": [], "error": None}


async def _ap_bounce_scan(state: State) -> None:
    from .. import helper_client
    scheduler = state.get("scheduler") if hasattr(state, "get") else state["scheduler"]
    svc = getattr(scheduler, "hotspot", None)
    was_active = False
    try:
        if svc is not None:
            try:
                was_active = bool((await svc.status()).get("active"))
            except Exception:
                was_active = False
        if was_active:
            await svc.deactivate()
            await asyncio.sleep(1.0)  # let NM release the radio
        # helper_client.call blocks on a socket; keep it off the loop.
        r = await asyncio.to_thread(helper_client.call, "wifi_scan")
        if r.get("ok"):
            try:
                _AP_SCAN["networks"] = json.loads(r.get("out") or "[]")
            except (ValueError, TypeError):
                _AP_SCAN["networks"] = []
            _AP_SCAN["error"] = None
        else:
            _AP_SCAN["error"] = (r.get("err") or "scan failed").strip()
        _AP_SCAN["ts"] = time.time()
    except Exception as e:
        _AP_SCAN["error"] = str(e)
        _AP_SCAN["ts"] = time.time()
    finally:
        if was_active and svc is not None:
            try:
                await svc.activate()
            except Exception:
                log.exception("wifi scan: FAILED to restore hotspot after bounce — "
                              "box may be unreachable until the AP is brought back up")
        _AP_SCAN["state"] = "error" if _AP_SCAN["error"] else "done"


@post("/api/network/wifi/scan/ap-bounce", status_code=202)
async def wifi_scan_ap_bounce(state: State) -> dict[str, Any]:
    """Start a background AP-bounce scan. Returns immediately (202) while
    the AP is still up so this response reaches a client connected through
    it; the bounce then runs and the client polls the GET below."""
    from .. import helper_client
    if not helper_client.is_available():
        raise HTTPException(
            status_code=400,
            detail="WiFi scan isn't available on this install (no host network control).",
        )
    if _AP_SCAN["state"] == "running":
        return {"started": False, "state": "running"}
    _AP_SCAN["state"] = "running"
    _AP_SCAN["error"] = None
    asyncio.create_task(_ap_bounce_scan(state))
    return {"started": True, "state": "running"}


@get("/api/network/wifi/scan/ap-bounce")
async def wifi_scan_ap_bounce_status() -> dict[str, Any]:
    """Poll the background AP-bounce scan. `state` is idle/running/done/
    error; `networks` + `error` carry the result once done."""
    age = int(time.time() - _AP_SCAN["ts"]) if _AP_SCAN["ts"] else None
    return {
        "state":    _AP_SCAN["state"],
        "networks": _AP_SCAN["networks"],
        "error":    _AP_SCAN["error"],
        "age_s":    age,
    }


# --- network address (set a static IP on the live connection) ------------
# Distinct from joining a WiFi network: this pins the box's *current*
# connection (ethernet, or already-joined WiFi) to a fixed address, or puts
# it back on DHCP. Powers the Network panel.


class NetIpv4Payload(msgspec.Struct, kw_only=True):
    connection: str          # nmcli connection name (from /api/network/status)
    method: str              # "auto" (DHCP) | "manual" (static)
    address: str | None = None
    prefix: int | None = None
    gateway: str | None = None
    dns: str | None = None   # comma/space-separated


@get("/api/network/status")
async def network_status() -> dict[str, Any]:
    """Active network connections + their IPv4 config for the Network panel.
    `supported: false` where there's no host network control (Docker/dev)."""
    from .. import helper_client
    if not helper_client.is_available():
        return {"supported": False, "connections": [], "error": None}
    r = helper_client.call("net_status")
    if not r.get("ok"):
        return {"supported": True, "connections": [],
                "error": (r.get("err") or "couldn't read network status").strip()}
    try:
        conns = json.loads(r.get("out") or "[]")
    except (ValueError, TypeError):
        conns = []
    return {"supported": True, "connections": conns, "error": None}


@post("/api/network/ipv4")
async def network_set_ipv4(data: NetIpv4Payload) -> dict[str, Any]:
    """Set a connection's IPv4 to DHCP or a static address. Re-applying can
    briefly move the box to a new address, so the reply points the user at
    wattpost.local to reconnect."""
    from .. import helper_client
    if not helper_client.is_available():
        raise HTTPException(
            status_code=400,
            detail="Network configuration isn't available on this install.",
        )
    if data.method not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="method must be 'auto' or 'manual'")
    if not (data.connection or "").strip():
        raise HTTPException(status_code=400, detail="connection is required")
    ipv4: dict[str, Any] = {"method": data.method}
    if data.method == "manual":
        if not (data.address or "").strip():
            raise HTTPException(status_code=400, detail="a static IP address is required")
        ipv4.update({
            "address": data.address.strip(),
            "prefix": data.prefix or 24,
            "gateway": (data.gateway or "").strip(),
            "dns": (data.dns or "").strip(),
        })
    r = helper_client.call("net_set_ipv4", connection=data.connection.strip(), ipv4=ipv4)
    if not r.get("ok"):
        # 4xx so the actionable detail (bad IP, etc.) reaches the client.
        raise HTTPException(
            status_code=400,
            detail=(r.get("err") or "Couldn't apply network settings.").strip(),
        )
    log.info("network: set %s ipv4 method=%s", data.connection, data.method)
    return {
        "ok": True,
        "applying": True,
        "message": "Applying — if the address changed, reconnect at http://wattpost.local",
    }


@post("/api/network/wifi/join")
async def wifi_join(data: WifiJoinPayload) -> dict[str, Any]:
    """Join a WiFi network. The PSK goes to the helper, which writes it to a
    0600 NM keyfile — it never lands in a command line or in our logs."""
    from .. import helper_client
    if not helper_client.is_available():
        raise HTTPException(
            status_code=400,
            detail="WiFi join isn't available on this install (no host network control).",
        )
    ssid = (data.ssid or "").strip()
    if not ssid or len(ssid) > 32:
        raise HTTPException(status_code=400, detail="SSID must be 1–32 characters.")
    psk = data.password or ""
    if psk and not (8 <= len(psk) <= 63):
        raise HTTPException(status_code=400, detail="WPA password must be 8–63 characters.")
    kwargs: dict[str, Any] = {"ssid": ssid, "psk": psk}
    if data.static_ip:
        kwargs["ipv4"] = {
            "method": "manual",
            "address": data.static_ip.strip(),
            "prefix": data.prefix or 24,
            "gateway": (data.gateway or "").strip(),
            "dns": (data.dns or "").strip(),
        }
    r = helper_client.call("wifi_join", **kwargs)
    if not r.get("ok"):
        # 4xx so the actionable detail reaches the client (Litestar masks 5xx
        # detail). The helper never echoes the PSK back.
        raise HTTPException(
            status_code=400,
            detail=(r.get("err") or "Couldn't connect — check the password and try again.").strip(),
        )
    log.info("wifi: joined network ssid=%s (secured=%s, static=%s)", ssid, bool(psk), bool(data.static_ip))
    return {"ok": True, "ssid": ssid}
