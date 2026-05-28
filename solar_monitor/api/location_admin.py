"""REST endpoints for the appliance Location panel (#263/#264).

Two surfaces:

  GET  /api/location/status  , current best location + share mode,
                                 used by the appliance dashboard
                                 "where am I" map tile and by the
                                 Settings → Location panel.

  PATCH /api/location/share  , flip share_with_cloud between off /
                                 approx / precise. Writes to
                                 config.yaml and the in-memory Config
                                 so the next heartbeat picks it up
                                 without a daemon restart.

The map tile reads `current_location` (the LOCAL view, always
truthful), not the cloud-gated view. Show-the-customer-their-own-
location is never gated; only TRANSMISSION is gated. See
[[location-opt-in]] memory for the principle.
"""
from __future__ import annotations

import logging
from typing import Any

import msgspec
from litestar import get, patch
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from .. import location as _loc
from ..config import LocationCfg

log = logging.getLogger(__name__)

_VALID_MODES = ("off", "approx", "precise")


@get("/api/location/status")
async def get_location_status(state: State) -> dict[str, Any]:
    """Local-view location (NEVER gated by share-with-cloud) +
    the current share mode the user has set.

    Shape:
      {
        "current":  {lat, lon, source, fix_age_s} | null,
        "share_with_cloud": "off" | "approx" | "precise",
        "approx_grid_km":   float,
      }
    """
    config = state["config"]
    scheduler = state["scheduler"]
    current = _loc.current_location(scheduler, config)
    loc_cfg = getattr(config, "location", None)
    mode = (getattr(loc_cfg, "share_with_cloud", "off") or "off").lower()
    if mode not in _VALID_MODES:
        mode = "off"
    return {
        "current":          current,
        "share_with_cloud": mode,
        "approx_grid_km":   float(getattr(loc_cfg, "approx_grid_km", 10.0) or 10.0),
    }


class _ShareUpdate(msgspec.Struct, kw_only=True):
    share_with_cloud: str  # "off" | "approx" | "precise"


@patch("/api/location/share")
async def update_location_share(
    data: _ShareUpdate, state: State,
) -> dict[str, Any]:
    """Set the share-with-cloud mode. Persists to config.yaml +
    updates the in-memory Config so the next heartbeat respects it
    without a restart. Idempotent."""
    new_mode = (data.share_with_cloud or "").lower()
    if new_mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"share_with_cloud must be one of {_VALID_MODES}",
        )

    config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if config.location is None:
        config.location = LocationCfg(share_with_cloud=new_mode)
    else:
        config.location.share_with_cloud = new_mode

    # Persist via the same _save_config helper the rest of the
    # admin endpoints use; mutator preserves every other field.
    from .cloud_admin import _save_config
    def _mutate(raw):
        loc_blob = raw.get("location") or {}
        loc_blob["share_with_cloud"] = new_mode
        # Preserve any user-tuned grid size.
        if "approx_grid_km" not in loc_blob:
            loc_blob["approx_grid_km"] = config.location.approx_grid_km
        raw["location"] = loc_blob
        return raw
    _save_config(config_path, _mutate)
    log.info("location: share_with_cloud → %s", new_mode)
    return {
        "ok": True,
        "share_with_cloud": new_mode,
    }
