"""REST endpoints for the wall-display kiosk defaults (#28).

  GET   /api/kiosk/config   — the appliance-side kiosk defaults (skin +
                              default-on). Read by the dashboard on load so
                              the local/wall display picks up whatever was
                              set, from the LAN OR via the cloud session.
  PATCH /api/kiosk/config   — set skin / default. Persists to config.yaml
                              and updates the in-memory Config.

These live on the APPLIANCE (not per-browser localStorage), so setting the
kiosk from a cloud session actually reaches the local screen — which the
old per-browser toggle couldn't do. A per-browser override still wins for
that one browser.
"""
from __future__ import annotations

import logging
from typing import Any

import msgspec
from litestar import get, patch, Request
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..config import KioskCfg

log = logging.getLogger(__name__)

_VALID_SKINS = ("halo", "ember", "command")


def _cfg(config) -> KioskCfg:
    k = getattr(config, "kiosk", None)
    return k if k is not None else KioskCfg()


@get("/api/kiosk/config")
async def get_kiosk_config(state: State, request: Request) -> dict[str, Any]:
    """Appliance kiosk defaults: {default: bool, skin: str}.

    When the request carries a kiosk link token (?token=…) that pins a theme,
    that link's skin wins — so each wall display can run a different skin off
    the one box. Otherwise the appliance-wide default applies."""
    k = _cfg(state["config"])
    skin = (k.skin or "halo").lower()
    if skin not in _VALID_SKINS:
        skin = "halo"
    tok = request.query_params.get("token")
    if tok:
        from .. import web_auth as _wa
        link_skin = (_wa.kiosk_token_skin(tok) or "").lower()
        if link_skin in _VALID_SKINS:
            skin = link_skin
    return {"default": bool(k.default), "skin": skin}


class _KioskUpdate(msgspec.Struct, kw_only=True):
    default: bool | None = None
    skin: str | None = None


@patch("/api/kiosk/config")
async def update_kiosk_config(data: _KioskUpdate, state: State) -> dict[str, Any]:
    """Set the kiosk default / skin. Persists to config.yaml + updates the
    in-memory Config so the change is live without a restart."""
    config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    if config.kiosk is None:
        config.kiosk = KioskCfg()

    changes: dict[str, Any] = {}
    if data.skin is not None:
        skin = data.skin.lower()
        if skin not in _VALID_SKINS:
            raise HTTPException(
                status_code=400, detail=f"skin must be one of {_VALID_SKINS}",
            )
        config.kiosk.skin = skin
        changes["skin"] = skin
    if data.default is not None:
        config.kiosk.default = bool(data.default)
        changes["default"] = bool(data.default)

    if changes:
        from .cloud_admin import _save_config
        def _mutate(raw):
            blob = raw.get("kiosk") or {}
            blob.update(changes)
            raw["kiosk"] = blob
            return raw
        _save_config(config_path, _mutate)
        log.info("kiosk: %s", changes)
    return {"ok": True, "default": bool(config.kiosk.default), "skin": config.kiosk.skin}
