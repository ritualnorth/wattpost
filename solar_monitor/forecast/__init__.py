"""PV forecast integrations.

Solcast for now; tomorrow.io / forecast.solar slot in alongside without
touching the consumer side (Settings UI, history chart overlay, cache
key) because everything goes through the `ForecastProvider` interface
and the normalised `PvForecast` shape.
"""
from .base import ForecastProvider, PvForecast, PvForecastPoint  # noqa: F401
from .service import ForecastService  # noqa: F401
from . import solcast  # noqa: F401  -- import for side-effect registration
