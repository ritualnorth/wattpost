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
    endpoint:          str = "https://wattpost.cloud"
    heartbeat_minutes: int = 5


class PairPayload(msgspec.Struct, kw_only=True):
    code:     str
    endpoint: str = "https://wattpost.cloud"


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

def cloud_config_view(config: Config) -> dict[str, Any]:
    """Pure config→dict view, reused by /api/system/integrations (#18).
    Token stays masked."""
    c = config.cloud
    if c is None or not c.bearer_token:
        return {
            "configured":        False,
            "endpoint":          c.endpoint if c else "https://wattpost.cloud",
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
        # Token is intentionally masked, the UI never gets the real one.
        "bearer_token":      "****",
        # Tunnel surface: hostname is shown on Integrations so the
        # operator can copy/click it; tunnel_enabled is true once a
        # token has been issued at pair time (CF creds were configured
        # on the cloud), false for older/legacy pairings that need a
        # re-pair to pick up a token.
        "tunnel_enabled":    bool(c.tunnel_token),
        "tunnel_hostname":   c.tunnel_hostname or None,
    }


@get("/api/cloud/config")
async def get_cloud_config(state: State) -> dict[str, Any]:
    return cloud_config_view(state["config"])


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
    # IMPORTANT: preserve every existing field on edit. Earlier
    # versions of this handler only carried bearer_token + appliance_id
    # + label across, which silently wiped tunnel_token,
    # tunnel_hostname, and sso_secret every time the user clicked
    # Save in Settings → Cloud. Seen after a v0.0.38 pull:
    # heartbeat populated sso_secret, then Settings-save reset it,
    # then SSO redirects failed with 401 because the appliance had
    # no key to verify against.
    # `replace` keeps every field we aren't changing, including ones added
    # after this handler was written. (Hand-copying fields here is how
    # sso_secret, and later the telemetry settings, got silently reset.)
    new_c = msgspec.structs.replace(
        existing if existing is not None else CloudCfg(),
        endpoint=data.endpoint,
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
        # Same follow_redirects rationale as the heartbeat client,
        # a user typing https://wattpost.io into the pairing form
        # still works because Caddy 308s /api/* to wattpost.cloud.
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True,
        ) as client:
            r = await client.post(url, json={"code": code})
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"could not reach {data.endpoint}: {e}",
        )

    # Cloud's @post default is 201 Created, not 200, accept any 2xx.
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        raise HTTPException(status_code=502, detail=f"pairing failed: {detail}")

    body = r.json()
    # Start from the existing config so a re-pair keeps everything it does not
    # own (heartbeat cadence, telemetry settings, kiosk token so share URLs
    # survive). Only the credentials the cloud just issued are replaced.
    new_c = msgspec.structs.replace(
        config.cloud if config.cloud else CloudCfg(),
        endpoint=data.endpoint,
        bearer_token=body["bearer_token"],
        appliance_id=body.get("appliance_id"),
        label=body.get("label") or "",
        tunnel_token=body.get("tunnel_token") or "",
        tunnel_hostname=body.get("tunnel_hostname") or "",
        sso_secret=body.get("sso_secret") or "",
    )
    config.cloud = new_c

    def _mutate(raw):
        raw["cloud"] = _serialize_cloud(new_c)
        return raw
    _save_config(config_path, _mutate)
    log.info("paired with cloud (appliance_id=%s)", new_c.appliance_id)

    # Hot-start the cloud + tunnel services in the live daemon so the
    # first heartbeat fires NOW, not after a manual restart. Previously
    # the only way to "complete" pairing was to restart wattpost,
    # confusing UX since the user already clicked Save. We still flag
    # restart_required so the UI nudges the user, but the appliance is
    # already showing up online by then.
    scheduler = state.get("scheduler") if hasattr(state, "get") else state["scheduler"]
    hot_started = await _hot_start_cloud(scheduler, new_c)

    return {
        "ok": True,
        "appliance_id": new_c.appliance_id,
        "label": new_c.label,
        # Only force a restart prompt when in-process start failed,
        # otherwise the daemon is fully paired right now.
        "restart_required": not hot_started,
    }


async def _hot_start_cloud(scheduler, cfg: CloudCfg) -> bool:
    """Spin up CloudService + TunnelService against the live scheduler
    after a successful pair. Returns True iff at least the heartbeat
    service is up; False (with a logged exception) tells the caller to
    still ask the user to restart.

    Imports are local so this module can be loaded by tests that don't
    pull in the full scheduler graph."""
    try:
        from ..cloud.service import CloudService
        from ..tunnel.service import TunnelService
    except Exception:
        log.exception("hot-start: failed to import service modules")
        return False
    if scheduler is None:
        log.warning("hot-start: scheduler not in app state, skipping")
        return False
    # Heartbeat service.
    try:
        old = getattr(scheduler, "_cloud", None)
        if old is not None:
            try:
                await old.stop()
            except Exception:
                log.warning("hot-start: stopping old cloud svc raised", exc_info=True)
        # Pass the parent Config (not the bare CloudCfg) so
        # CloudService resolves `self.cfg` via property and stays
        # in sync if Settings → Cloud → Save later rebinds
        # config.cloud to a fresh struct. See #148.
        parent_config = getattr(scheduler, "config", None)
        new_svc = CloudService(parent_config or cfg, scheduler)
        scheduler._cloud = new_svc
        await new_svc.start()
    except Exception:
        log.exception("hot-start: cloud heartbeat service failed to start")
        return False
    # Tunnel, best-effort; success here is bonus, not required.
    try:
        if TunnelService.is_available(cfg):
            old_t = getattr(scheduler, "_tunnel", None)
            if old_t is not None:
                try:
                    await old_t.stop()
                except Exception:
                    log.warning("hot-start: stopping old tunnel raised", exc_info=True)
            t = TunnelService(cfg)
            scheduler._tunnel = t
            await t.start()
    except Exception:
        log.exception("hot-start: tunnel service failed to start (non-fatal)")
    log.info("hot-start: cloud heartbeat live without daemon restart")
    return True


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
    """Force an immediate heartbeat, Settings UI's "Send now" button.
    Returns true/false depending on whether the cloud accepted it."""
    scheduler = state["scheduler"]
    svc = getattr(scheduler, "_cloud", None)
    if svc is None:
        # Daemon started before pair (or pair didn't hot-start for some
        # reason). If config has a bearer token now, bring the service
        # up rather than telling the user to "pair first", which is
        # misleading and what was hitting people who paired without
        # restarting the daemon.
        config: Config = state["config"]
        if config.cloud is not None and config.cloud.bearer_token:
            ok_started = await _hot_start_cloud(scheduler, config.cloud)
            if not ok_started:
                raise HTTPException(
                    status_code=500,
                    detail="couldn't start heartbeat service, check daemon logs",
                )
            svc = getattr(scheduler, "_cloud", None)
        if svc is None:
            raise HTTPException(
                status_code=400,
                detail="cloud heartbeat service not running. Pair first.",
            )
    ok = await svc.heartbeat_once()
    return {"ok": ok}


def persist_cloud_cfg(cfg: CloudCfg, config_path: str | None = None) -> None:
    """Background-write helper. Looks up the active config.yaml path
    (the daemon's --config arg) and rewrites it with the serialised
    `cfg`. Used by the cloud heartbeat service when the cloud pushes
    a new sso_secret to a not-yet-migrated appliance.

    `config_path` overrides the path lookup; defaults to the
    environment variable WATTPOST_CONFIG which the systemd unit /
    Docker entrypoint set."""
    import os
    path = config_path or os.environ.get("WATTPOST_CONFIG") or "config.yaml"
    def _mutate(raw):
        raw["cloud"] = _serialize_cloud(cfg)
        return raw
    _save_config(path, _mutate)


_CLOUD_ALWAYS = ("endpoint", "bearer_token", "appliance_id", "label", "heartbeat_minutes")


def _serialize_cloud(c: CloudCfg) -> dict[str, Any]:
    """Every CloudCfg field, so a field added later is persisted without
    anyone remembering to list it here. Fields still at their default are
    left out (keeps config.yaml free of empty placeholders) except the core
    pairing fields, which are always written."""
    defaults = msgspec.structs.asdict(CloudCfg())
    return {
        k: v for k, v in msgspec.structs.asdict(c).items()
        if k in _CLOUD_ALWAYS or v != defaults[k]
    }
