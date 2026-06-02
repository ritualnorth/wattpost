"""Synthetic PV forecast provider, for demo.wattpost.io.

Produces a Solcast-shaped `PvForecast` (7 days × 48 half-hour points
each) without ever hitting an external API. Bell-curve PV peaked at
solar noon with day-to-day variation so the dashboard's tomorrow tile,
7-day strip, and history-overlay all have something believable to
draw.

Registered in forecast/service.py under the `"synthetic"` provider key.
The demo container's config.yaml selects it; nothing else uses it.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone

from .base import ForecastProvider, PvForecast, PvForecastPoint


# Tunables, should produce numbers that look like a real ~3-4 kW
# residential array. Adjust if the demo's actual synthetic battery
# bank changes scale.
PEAK_KW          = 3.6   # kW, peak instantaneous power on a clear day
DAYLIGHT_HOURS   = 12.0  # window over which the half-sine spans
DAYS_AHEAD       = 7
PERIOD_MINUTES   = 30


class SyntheticForecastProvider(ForecastProvider):
    name = "synthetic"

    def __init__(self) -> None:
        # Locked seed so the demo's forecast is consistent across
        # restarts within the same calendar day, visitors who reload
        # don't see wildly different numbers.
        self._seed_for_today = self._daily_seed()

    @staticmethod
    def _daily_seed() -> int:
        # YYYYMMDD as the seed → same forecast all day, fresh tomorrow.
        return int(datetime.now(timezone.utc).strftime("%Y%m%d"))

    async def fetch(self) -> PvForecast:
        # Reseed each call in case the date rolled over.
        self._seed_for_today = self._daily_seed()
        rng = random.Random(self._seed_for_today)

        # Each day picks a "cloudiness" between 0.45 (very cloudy) and
        # 1.0 (clear) so the 7-day strip has visible variation.
        day_factors = [rng.uniform(0.45, 1.0) for _ in range(DAYS_AHEAD)]

        # Period start: align to the next half-hour boundary so the
        # first point lines up with "now". Solcast's API is similarly
        # snapped.
        now = datetime.now(timezone.utc)
        snap = now.replace(second=0, microsecond=0, minute=(now.minute // 30) * 30)

        points: list[PvForecastPoint] = []
        sunrise_h = 12.0 - DAYLIGHT_HOURS / 2.0   # 06:00 local-ish
        sunset_h  = 12.0 + DAYLIGHT_HOURS / 2.0   # 18:00 local-ish

        for offset in range(DAYS_AHEAD * 48):
            period_end = snap + timedelta(minutes=PERIOD_MINUTES * (offset + 1))
            ts = int(period_end.timestamp())
            # local hour-of-day for the period midpoint
            mid = period_end - timedelta(minutes=PERIOD_MINUTES / 2)
            hod = (mid.hour + mid.minute / 60.0)
            day_idx = min(DAYS_AHEAD - 1, offset // 48)

            if hod < sunrise_h or hod > sunset_h:
                kw = 0.0
            else:
                # Half-sine intensity 0..1 across the daylight window.
                progress = (hod - sunrise_h) / (sunset_h - sunrise_h)
                intensity = math.sin(progress * math.pi)
                kw = PEAK_KW * intensity * day_factors[day_idx]
                # Mild per-period jitter so the curve isn't pixel-perfect.
                kw *= rng.uniform(0.95, 1.05)

            w = kw * 1000.0
            # P10/P90 band: ±20% on the central estimate. Real Solcast
            # widens at peak; this is a passable approximation for a demo.
            p10 = max(0.0, w * 0.78)
            p90 = w * 1.18
            points.append(PvForecastPoint(
                ts=ts, pv_w=round(w, 1),
                pv_w_p10=round(p10, 1), pv_w_p90=round(p90, 1),
            ))

        return PvForecast(
            provider="synthetic",
            fetched_at=int(time.time()),
            points=points,
        )


def build(cfg) -> SyntheticForecastProvider:
    """Factory matching the contract in forecast/service.py.
    `cfg` is ignored, the synthetic provider has no config knobs."""
    return SyntheticForecastProvider()
