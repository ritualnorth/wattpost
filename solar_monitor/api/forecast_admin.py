"""HTTP endpoints for the PV forecast integration.

Splits into three concerns:
  GET  /api/forecast/pv          → cached forecast for the History overlay
  GET  /api/forecast/config      → masked credentials for the Settings UI
  PUT  /api/forecast/config      → write new credentials, restart_required
  POST /api/forecast/test        → one-shot fetch with the *posted* creds
                                    (so the Test button works before save)
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from ..config import Config, ForecastCfg
from ..forecast.service import CACHE_KEY
from ..forecast.solcast import SolcastProvider
from ..scheduler import PollScheduler
from ..storage.sqlite import Store

log = logging.getLogger(__name__)


# ---------- payloads ----------

class ForecastConfigPayload(msgspec.Struct, kw_only=True):
    provider: str = "solcast"
    api_key: str | None = None      # null = clear / disable
    resource_id: str | None = None
    poll_hours: int = 3


# ---------- helpers ----------

def _save_config(config_path: str, mutator) -> None:
    """Same atomic .bak + .tmp rename pattern alerts_admin uses."""
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

@get("/api/forecast/pv")
async def get_pv_forecast(state: State) -> dict[str, Any]:
    """Return the latest cached forecast or a 204-shaped empty payload.
    The UI treats `points: []` as "not configured / not fetched yet"
    so the History overlay code can render cleanly without a forecast."""
    store: Store = state["store"]
    cached = await store.kv_get(CACHE_KEY)
    if cached is None:
        return {"points": [], "fetched_at": None, "provider": None}
    body, updated_at = cached
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # Cache corrupt — return empty rather than 500. Next poll
        # overwrites it.
        return {"points": [], "fetched_at": None, "provider": None}
    payload.setdefault("fetched_at", updated_at)
    return payload


@get("/api/forecast/config")
async def get_forecast_config(state: State) -> dict[str, Any]:
    """Masked view of the credentials for the Settings UI. Never
    returns the raw api_key — the field comes back as `****` when
    set, "" when unset, and the UI handles "leave blank to keep
    existing" the same way the alert transport editor does."""
    config: Config = state["config"]
    fc = config.forecast
    if fc is None:
        return {"configured": False, "provider": "solcast",
                "api_key": "", "resource_id": "", "poll_hours": 3}
    return {
        "configured":  True,
        "provider":    fc.provider,
        "api_key":     "****" if fc.api_key else "",
        "resource_id": fc.resource_id,
        "poll_hours":  fc.poll_hours,
    }


@put("/api/forecast/config")
async def update_forecast_config(
    data: ForecastConfigPayload, state: State,
) -> dict[str, Any]:
    """Write or clear the `forecast:` block. Null/empty api_key clears
    the integration. Engine reads config at boot so this returns
    `restart_required: true`."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    clearing = not (data.api_key and data.resource_id)
    if clearing:
        config.forecast = None

        def _mutate(raw):
            raw.pop("forecast", None)
            return raw
        _save_config(config_path, _mutate)
        log.info("forecast integration cleared")
        return {"ok": True, "configured": False, "restart_required": True}

    # Preserve existing api_key when the UI sends "****" sentinel
    # (Settings form leaves the password input blank to keep the
    # current value).
    existing = config.forecast
    api_key = data.api_key
    if api_key == "****" and existing is not None:
        api_key = existing.api_key

    if data.poll_hours < 1 or data.poll_hours > 24:
        raise HTTPException(
            status_code=400,
            detail="poll_hours must be in [1, 24]; Solcast hobbyist "
                   "limits make 3-6 the practical range.",
        )

    new_fc = ForecastCfg(
        provider=data.provider,
        api_key=api_key,
        resource_id=data.resource_id,
        poll_hours=data.poll_hours,
    )
    config.forecast = new_fc

    def _mutate(raw):
        raw["forecast"] = {
            "provider":    new_fc.provider,
            "api_key":     new_fc.api_key,
            "resource_id": new_fc.resource_id,
            "poll_hours":  new_fc.poll_hours,
        }
        return raw
    _save_config(config_path, _mutate)
    log.info("forecast integration configured (%s, every %dh)",
             new_fc.provider, new_fc.poll_hours)
    return {"ok": True, "configured": True, "restart_required": True}


@post("/api/forecast/test")
async def test_forecast_fetch(
    data: ForecastConfigPayload, state: State,
) -> dict[str, Any]:
    """One-shot fetch with the supplied credentials. Used by the
    Settings UI's "Test" button so the user can validate keys before
    saving them to config.yaml. Doesn't touch the live service or the
    cache — purely a credential check."""
    if data.provider != "solcast":
        raise HTTPException(
            status_code=400, detail=f"unknown provider {data.provider!r}",
        )

    config: Config = state["config"]
    api_key = data.api_key
    # Same "****" → keep-existing semantic as the PUT.
    if api_key == "****" and config.forecast is not None:
        api_key = config.forecast.api_key
    if not api_key or not data.resource_id:
        raise HTTPException(
            status_code=400,
            detail="Both api_key and resource_id are required.",
        )

    provider = SolcastProvider(api_key=api_key, resource_id=data.resource_id)
    try:
        fc = await provider.fetch()
    except RuntimeError as e:
        # Provider raises RuntimeError with a friendly message on the
        # well-known failure modes (401, 404, 429). Surface those as-is.
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Solcast unreachable: {e}")

    # Headline: time + power of the next forecast peak, so the UI can
    # show "✓ next peak 4.2 kW at 14:00 tomorrow".
    peak_ts: int | None = None
    peak_w: float = 0.0
    import time as _time
    now = int(_time.time())
    for p in fc.points:
        if p.ts < now:
            continue
        if p.pv_w > peak_w:
            peak_w = p.pv_w
            peak_ts = p.ts

    return {
        "ok":       True,
        "points":   len(fc.points),
        "peak_ts":  peak_ts,
        "peak_w":   round(peak_w, 1) if peak_ts is not None else None,
    }