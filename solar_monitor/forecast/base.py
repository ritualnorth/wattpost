"""Forecast provider interface + normalised output shape.

Every provider returns the same `PvForecast` so the API endpoint, the
SQLite cache blob, and the History-chart overlay never have to care
which third-party API the user wired up.
"""
from __future__ import annotations

import abc
import msgspec


class PvForecastPoint(msgspec.Struct):
    # Unix seconds at the END of the 30-min forecast period (matches
    # Solcast's `period_end` semantics; we keep their convention rather
    # than reinventing).
    ts: int
    # Watts. Provider native units (Solcast: kW) are normalised to W in
    # the provider class so consumers always see one unit.
    pv_w: float
    # P10 / P90 confidence interval, also in W. Both optional,
    # forecast.solar doesn't return them.
    pv_w_p10: float | None = None
    pv_w_p90: float | None = None


class PvForecast(msgspec.Struct):
    provider: str
    # Unix seconds when the daemon completed the fetch. Used by the UI
    # to show "cached 17 minutes ago".
    fetched_at: int
    points: list[PvForecastPoint]


class ForecastProvider(abc.ABC):
    """Provider-agnostic interface. One instance per configured
    `forecast` block; `fetch()` is called by the background service
    on its poll cadence."""

    name: str

    @abc.abstractmethod
    async def fetch(self) -> PvForecast:
        """Return the latest PV forecast. May raise on transient errors
       , the service is responsible for retry + logging; we don't try
        to recover at this layer."""
