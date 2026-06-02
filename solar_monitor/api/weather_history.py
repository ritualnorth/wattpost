"""Weather history for the Energy chart overlay (#251).

The Energy chart at the top of /history shows energy curves over a
chosen range (today / 24h / 7d / 30d). This endpoint returns a
matching weather series so the frontend can overlay cloud cover
behind the chart, letting the user see "solar dropped because it
clouded over" without leaving the page.

Source: Open-Meteo's forecast API supports `past_days=N` (1-92)
returning hourly cloud cover + shortwave radiation. No API key,
no auth, free. For now we only surface cloud cover, it reads
intuitively to non-technical customers ("70% overcast at 2pm")
where W/m² doesn't.

The series is aligned onto the same `ts` grid the energy endpoint
returned by linear interpolation between hourly samples (Open-Meteo
hourly is the densest resolution we can get without paying).

If no lat/lon is configured, or the upstream fetch fails, we return
an empty series rather than 500, the chart still renders, the
overlay just doesn't appear. The weather overlay is a nice-to-have,
not a hard dependency.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from litestar import get
from litestar.datastructures import State

from ..config import Config
from ..storage.sqlite import Store

log = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10.0
# 15 min cache per (lat,lon,since,bucket), keeps chart re-renders
# snappy without hammering Open-Meteo if the user toggles ranges.
CACHE_TTL_S = 15 * 60


@get("/api/weather/history")
async def weather_history(
    state: State,
    since: int | None = None,
    until: int | None = None,
    bucket: int | None = None,
) -> dict[str, Any]:
    """Cloud-cover series aligned to (since, until, bucket).

    Returns:
        {
          "since_ts": int, "until_ts": int, "bucket_seconds": int,
          "configured": bool,    # false if no lat/lon set
          "available":  bool,    # false if upstream failed
          "series": {
            "ts":              [int, ...],
            "cloud_cover_pct": [float | None, ...],
          },
        }
    """
    now_ts = int(time.time())
    if until is None:
        until = now_ts
    if since is None:
        local = time.localtime(now_ts)
        since = int(time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        ))
    bucket_s = int(bucket) if bucket and int(bucket) > 0 else 300

    config: Config = state["config"]
    store: Store = state["store"]
    wcfg = config.weather

    grid_ts = _grid(since, until, bucket_s)
    empty = {
        "since_ts": since, "until_ts": until, "bucket_seconds": bucket_s,
        "configured": False, "available": False,
        "series": {"ts": grid_ts, "cloud_cover_pct": [None] * len(grid_ts)},
    }
    if wcfg is None or wcfg.lat is None or wcfg.lon is None:
        return empty

    cache_key = f"weather:history:{wcfg.lat:.3f}:{wcfg.lon:.3f}:{since}:{until}:{bucket_s}"
    cached = await store.kv_get(cache_key)
    if cached is not None:
        body, updated_at = cached
        if now_ts - updated_at < CACHE_TTL_S:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass

    hourly_ts, hourly_cc = await _fetch_open_meteo_hourly(wcfg.lat, wcfg.lon, since, until)
    if not hourly_ts:
        return {**empty, "configured": True}

    cloud = _interp(grid_ts, hourly_ts, hourly_cc)
    out = {
        "since_ts": since, "until_ts": until, "bucket_seconds": bucket_s,
        "configured": True, "available": True,
        "series": {"ts": grid_ts, "cloud_cover_pct": cloud},
    }
    try:
        await store.kv_set(cache_key, json.dumps(out))
    except Exception as e:
        log.warning("weather history cache write failed: %s", e)
    return out


def _grid(since: int, until: int, bucket_s: int) -> list[int]:
    start = since - (since % bucket_s)
    return list(range(start, until + 1, bucket_s))


async def _fetch_open_meteo_hourly(
    lat: float, lon: float, since: int, until: int,
) -> tuple[list[int], list[float | None]]:
    """Hit Open-Meteo for hourly cloud_cover across [since, until].

    past_days covers everything earlier than today; forecast_days=1
    covers any portion of the window after midnight today. We
    over-fetch by a day on each side so the interpolation has
    sample points just outside the window, the chart edges
    interpolate correctly instead of going flat.
    """
    now_ts = int(time.time())
    midnight_today = int(time.mktime(time.localtime(now_ts)[:3] + (0, 0, 0, 0, 0, -1)))
    days_back = max(1, min(92, ((midnight_today - since) // 86400) + 2))
    forecast_days = 1 if until >= midnight_today else 0
    params = {
        "latitude":      f"{lat:.4f}",
        "longitude":     f"{lon:.4f}",
        "hourly":        "cloud_cover",
        "past_days":     str(int(days_back)),
        "forecast_days": str(int(forecast_days)),
        "timezone":      "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(BASE_URL, params=params)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        log.info("weather history upstream failed: %s", e)
        return [], []

    hourly = body.get("hourly") or {}
    times = hourly.get("time") or []
    ccs   = hourly.get("cloud_cover") or []
    out_ts: list[int] = []
    out_cc: list[float | None] = []
    for i, t in enumerate(times):
        ts = _parse_iso(t)
        if ts is None:
            continue
        out_ts.append(ts)
        out_cc.append(None if i >= len(ccs) or ccs[i] is None else float(ccs[i]))
    return out_ts, out_cc


def _interp(
    grid_ts: list[int],
    src_ts: list[int],
    src_v: list[float | None],
) -> list[float | None]:
    """Linear-interpolate the (src_ts, src_v) hourly series onto
    grid_ts. Buckets outside the source's range get None. Buckets
    that fall in a gap (a None on either side) get None too, better
    to draw a hole than fake a value."""
    if not src_ts:
        return [None] * len(grid_ts)
    out: list[float | None] = []
    j = 0
    for t in grid_ts:
        while j + 1 < len(src_ts) and src_ts[j + 1] <= t:
            j += 1
        if t < src_ts[0] or t > src_ts[-1]:
            out.append(None)
            continue
        if j + 1 >= len(src_ts):
            out.append(src_v[j])
            continue
        t0, t1 = src_ts[j], src_ts[j + 1]
        v0, v1 = src_v[j], src_v[j + 1]
        # Exact sample-point landings always return the real reading,
        # even if the neighbouring sample is a gap.
        if t == t0:
            out.append(v0)
            continue
        if t == t1:
            out.append(v1)
            continue
        if v0 is None or v1 is None:
            out.append(None)
            continue
        if t1 == t0:
            out.append(v0)
            continue
        frac = (t - t0) / (t1 - t0)
        out.append(v0 + (v1 - v0) * frac)
    return out


def _parse_iso(s: str | None) -> int | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None
