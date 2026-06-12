"""REST endpoints for the USB GPS service (#125).

Three concerns:
  GET  /api/gps            → live status (latest fix, age, satellites)
  GET  /api/gps/config     → current `gps:` block for the Settings form
  PUT  /api/gps/config     → enable/disable + write the `gps:` block

Enable is opt-in: location stays default-off (see the privacy panel),
and turning a receiver on is an explicit user action here or in the
setup wizard. Config writes hot-reload the scheduler the same way the
forecast/weather editors do, so a freshly-plugged VK-162 starts
acquiring without a daemon restart.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import get, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..config import Config, GpsCfg
from ..scheduler import PollScheduler
from .setup import _hot_reload_bg

log = logging.getLogger(__name__)

# Baud rates a USB-CDC GPS receiver realistically speaks. u-blox modules
# default to 9600; we allow the common faster rates for receivers that
# have been reconfigured, but reject arbitrary integers so a typo can't
# wedge the port at a rate nothing will ever sync to.
_ALLOWED_BAUD = (4800, 9600, 19200, 38400, 57600, 115200)


class GpsConfigPayload(msgspec.Struct, kw_only=True):
    enabled: bool = False
    port: str | None = None
    baudrate: int = 9600


def _save_config(config_path: str, mutator) -> None:
    """Atomic .bak + .tmp rename, same pattern as forecast/alerts admin."""
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


def gps_config_view(config: Config) -> dict[str, Any]:
    """Pure view of the `gps:` block for the Settings form."""
    g = config.gps
    if g is None:
        return {"configured": False, "port": None, "baudrate": 9600}
    return {"configured": True, "port": g.port, "baudrate": g.baudrate}


@get("/api/gps")
async def get_gps_status(state: State) -> dict[str, Any]:
    """Report the GPS service's current state.

    Returns `{configured: false}` when the daemon was built without
    a `gps:` config block; otherwise the standard status payload
    (latest_fix, fix age, last applied lat/lon, etc.). The UI uses
    this to render the Settings → Location panel + show stale-fix
    warnings."""
    scheduler: PollScheduler = state["scheduler"]
    if scheduler.gps is None:
        return {"configured": False}
    payload = scheduler.gps.get_status()
    payload["configured"] = True
    return payload


@get("/api/gps/config")
async def get_gps_config(state: State) -> dict[str, Any]:
    return gps_config_view(state["config"])


@put("/api/gps/config")
async def update_gps_config(
    data: GpsConfigPayload, state: State,
) -> dict[str, Any]:
    """Enable or disable the USB GPS receiver.

    `enabled=true` requires a `port` (e.g. /dev/ttyACM0); it writes the
    `gps:` block and hot-reloads so the receiver starts acquiring.
    `enabled=false` clears the block and stops the service. Existing
    `min_move_km` / `refresh_after_s` tunables are preserved across an
    edit so power users who hand-set them in YAML don't lose them."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if not data.enabled:
        config.gps = None

        def _mutate(raw):
            raw.pop("gps", None)
            return raw
        _save_config(config_path, _mutate)
        log.info("gps receiver disabled")
        asyncio.create_task(_hot_reload_bg(state))
        return {"ok": True, "configured": False, "restart_required": False}

    port = (data.port or "").strip()
    if not port.startswith("/dev/tty"):
        raise HTTPException(
            status_code=400,
            detail="port must be a serial-device path like /dev/ttyACM0",
        )
    if data.baudrate not in _ALLOWED_BAUD:
        raise HTTPException(
            status_code=400,
            detail=f"baudrate must be one of {_ALLOWED_BAUD}; "
                   "most USB GPS receivers (incl. u-blox / VK-162) use 9600.",
        )

    # Preserve hand-tuned thresholds across an edit.
    existing = config.gps
    new_gps = GpsCfg(
        port=port,
        baudrate=data.baudrate,
        min_move_km=existing.min_move_km if existing else 5.0,
        refresh_after_s=existing.refresh_after_s if existing else 1800,
    )
    config.gps = new_gps

    def _mutate(raw):
        block: dict[str, Any] = {"port": new_gps.port, "baudrate": new_gps.baudrate}
        # Only persist the tunables when they differ from defaults, keeps
        # the YAML clean for the common case.
        if new_gps.min_move_km != 5.0:
            block["min_move_km"] = new_gps.min_move_km
        if new_gps.refresh_after_s != 1800:
            block["refresh_after_s"] = new_gps.refresh_after_s
        raw["gps"] = block
        return raw
    _save_config(config_path, _mutate)
    log.info("gps receiver configured (%s @ %d baud)", new_gps.port, new_gps.baudrate)
    asyncio.create_task(_hot_reload_bg(state))
    return {"ok": True, "configured": True, "restart_required": False}
