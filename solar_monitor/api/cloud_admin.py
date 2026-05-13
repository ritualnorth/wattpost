"""Appliance-side cloud admin endpoints.

These run *on the appliance*, accessible from the local dashboard.
They let the user pair the appliance to their wattpost.io account
and manage the per-appliance config (endpoint URL + bearer token).

  GET  /api/cloud/config      → masked credentials
  PUT  /api/cloud/config      → write/clear (endpoint, heartbeat_minutes)
  POST /api/cloud/pair        → exchange pairing code with the cloud
  POST /api/cloud/heartbeat   → trigger a one-shot heartbeat now
                                (useful from the Settings UI's Test button)
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import httpx
import msgspec
import yaml
from litestar import get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..config import CloudCfg, Config

log = logging.getLogger(__name__)


# ---------- payloads ----------

class CloudConfigPayload(msgspec.Struct, kw_only=True):
    endpoint:          str = "https://app.wattpost.io"
    heartbeat_minutes: int = 5


class PairPayload(msgspec.Struct, kw_only=True):
    code:     str
    endpoint: str = "https://app.wattpost.io"


# ---------- helpers ----------

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


# ---------- routes ----------

@get("/api/cloud/config")
async def get_cloud_config(state: State) -> dict[str, Any]:
    config: Config = state["config"]
    c = config.cloud
    if c is None or not c.bearer_token:
        return {
            "configured":        False,
            "endpoint":          c.endpoint if c else "https://app.wattpost.io",
            "heartbeat_minutes": c.heartbeat_minutes if c else 5,
            "appliance_id":      None,
            "label":             "",
        }
    return {
        "configured":        True,
        "endpoint":          c.endpoint,
        "heartbeat_minutes": c.heartbeat_minutes,
        "appliance_id":      c.appliance_id,
        "label":             c.label,
        # Token is intentionally masked — the UI never gets the real one.
        "bearer_token":      "****",
    }


@put("/api/cloud/config")
async def update_cloud_config(
    data: CloudConfigPayload, state: State,
) -> dict[str, Any]:
    """Tune the endpoint URL / cadence without re-pairing. Bearer
    token is preserved if already set; cleared only by /unpair."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    if data.heartbeat_minutes < 1 or data.heartbeat_minutes > 60:
        raise HTTPException(
            status_code=400,
            detail="heartbeat_minutes must be between 1 and 60",
        )
    if not data.endpoint.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="endpoint must be http(s) URL",
        )
    existing = config.cloud
    new_c = CloudCfg(
        endpoint=data.endpoint,
        bearer_token=existing.bearer_token if existing else "",
        appliance_id=existing.appliance_id if existing else None,
        label=existing.label if existing else "",
        heartbeat_minutes=data.heartbeat_minutes,
    )
    config.cloud = new_c

    def _mutate(raw):
        raw["cloud"] = _serialize_cloud(new_c)
        return raw
    _save_config(config_path, _mutate)
    log.info("cloud config updated (endpoint=%s, every %dm)",
             new_c.endpoint, new_c.heartbeat_minutes)
    return {"ok": True, "restart_required": True}


@post("/api/cloud/pair")
async def pair_appliance(
    data: PairPayload, state: State,
) -> dict[str, Any]:
    """Exchange a one-shot pairing code for a long-lived bearer token.
    On success, persists the token into config.yaml so the next daemon
    restart's heartbeat loop picks it up automatically."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    code = (data.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    url = f"{data.endpoint.rstrip('/')}/api/pair/exchange"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json={"code": code})
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"could not reach {data.endpoint}: {e}",
        )

    # Cloud's @post default is 201 Created, not 200 — accept any 2xx.
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        raise HTTPException(status_code=502, detail=f"pairing failed: {detail}")

    body = r.json()
    new_c = CloudCfg(
        endpoint=data.endpoint,
        bearer_token=body["bearer_token"],
        appliance_id=body.get("appliance_id"),
        label=body.get("label") or "",
        heartbeat_minutes=(config.cloud.heartbeat_minutes if config.cloud else 5),
    )
    config.cloud = new_c

    def _mutate(raw):
        raw["cloud"] = _serialize_cloud(new_c)
        return raw
    _save_config(config_path, _mutate)
    log.info("paired with cloud (appliance_id=%s)", new_c.appliance_id)
    return {
        "ok": True,
        "appliance_id": new_c.appliance_id,
        "label": new_c.label,
        "restart_required": True,
    }


@post("/api/cloud/unpair", status_code=200)
async def unpair_appliance(state: State) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    if config.cloud is None:
        return {"ok": True, "configured": False}
    config.cloud = None

    def _mutate(raw):
        raw.pop("cloud", None)
        return raw
    _save_config(config_path, _mutate)
    log.info("unpaired from cloud")
    return {"ok": True, "configured": False, "restart_required": True}


@post("/api/cloud/heartbeat", status_code=202)
async def trigger_heartbeat(state: State) -> dict[str, Any]:
    """Force an immediate heartbeat — Settings UI's "Send now" button.
    Returns true/false depending on whether the cloud accepted it."""
    scheduler = state["scheduler"]
    svc = getattr(scheduler, "_cloud", None)
    if svc is None:
        raise HTTPException(
            status_code=400,
            detail="cloud heartbeat service not running. Pair first.",
        )
    ok = await svc.heartbeat_once()
    return {"ok": ok}


def _serialize_cloud(c: CloudCfg) -> dict[str, Any]:
    return {
        "endpoint":          c.endpoint,
        "bearer_token":      c.bearer_token,
        "appliance_id":      c.appliance_id,
        "label":             c.label,
        "heartbeat_minutes": c.heartbeat_minutes,
    }
