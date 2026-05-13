"""Solcast PV forecast provider.

Free hobbyist tier: 10 API calls/day, two residential sites per account.
User registers their rooftop at solcast.com (panel size, tilt, azimuth,
location) and gets:
  - an API key  (~36 character bearer token)
  - a resource_id  (UUID identifying the registered site)

The forecast endpoint returns 30-minute-resolution points up to 7 days
ahead. We normalise kW → W in this class so downstream code (history
overlay, dashboard tile) works in the same unit as the live
`pv_power_w` metric.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from .base import ForecastProvider, PvForecast, PvForecastPoint

log = logging.getLogger(__name__)

BASE_URL = "https://api.solcast.com.au"
TIMEOUT_S = 30.0


class SolcastProvider(ForecastProvider):
    name = "solcast"

    def __init__(self, api_key: str, resource_id: str) -> None:
        if not api_key:
            raise ValueError("solcast: api_key is required")
        if not resource_id:
            raise ValueError("solcast: resource_id is required")
        self.api_key = api_key
        self.resource_id = resource_id

    async def fetch(self) -> PvForecast:
        url = f"{BASE_URL}/rooftop_sites/{self.resource_id}/forecasts"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        # Ask explicitly for 168 hours (7 days) at 30-min resolution.
        # Solcast's default response length varies by tier and account
        # state — sometimes only 3 days come back without `hours` set,
        # which would silently shrink the daily-outlook strip. The
        # hobbyist tier caps at 168 anyway, so this is the safe max.
        params = {"format": "json", "hours": "168", "period": "PT30M"}
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(url, headers=headers, params=params)
        if r.status_code == 401:
            raise RuntimeError(
                "Solcast rejected the API key (HTTP 401). Double-check "
                "the key matches the resource_id's account."
            )
        if r.status_code == 404:
            raise RuntimeError(
                f"Solcast didn't recognise resource_id {self.resource_id!r} "
                "(HTTP 404). Find your site UUID at "
                "https://toolkit.solcast.com.au/rooftop-sites."
            )
        if r.status_code == 429:
            raise RuntimeError(
                "Solcast rate-limited the request (HTTP 429). Hobbyist "
                "tier is 10 calls/day; the daemon defaults to 8."
            )
        r.raise_for_status()

        payload = r.json()
        raw_points = payload.get("forecasts") or []
        points: list[PvForecastPoint] = []
        for p in raw_points:
            ts = _parse_period_end(p.get("period_end"))
            if ts is None:
                continue
            est = p.get("pv_estimate")
            if est is None:
                continue
            points.append(PvForecastPoint(
                ts=ts,
                pv_w=float(est) * 1000.0,
                pv_w_p10=_kw_to_w(p.get("pv_estimate10")),
                pv_w_p90=_kw_to_w(p.get("pv_estimate90")),
            ))
        return PvForecast(
            provider=self.name,
            fetched_at=int(time.time()),
            points=points,
        )


def _kw_to_w(v: float | None) -> float | None:
    if v is None:
        return None
    return float(v) * 1000.0


def _parse_period_end(s: str | None) -> int | None:
    """Solcast period_end is an ISO-8601 string like '2026-05-14T03:30:00Z'.
    Returns unix seconds (UTC), or None on parse failure."""
    if not s:
        return None
    try:
        # Python 3.11+ fromisoformat accepts the trailing 'Z' since 3.11.
        # Strip it defensively for older interpreters.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def build(cfg) -> SolcastProvider:
    """Factory used by the forecast service: hand it the
    ForecastCfg-shaped struct (or just any object with .api_key and
    .resource_id attributes) and you get a configured provider."""
    return SolcastProvider(api_key=cfg.api_key, resource_id=cfg.resource_id)
