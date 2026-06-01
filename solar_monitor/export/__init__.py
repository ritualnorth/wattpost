"""Pluggable exporters that fan poll results out to external systems.

Same registration pattern as transports + vendors: each exporter declares a
type-string, the orchestrator builds them from YAML, and the scheduler
dispatches each poll result to every enabled exporter.

Adding a new export sink (InfluxDB, webhook, Discord) = drop a module here
that imports from `.base` and decorates a factory with @register_exporter.
"""
from .base import Exporter, ExporterError
from .registry import EXPORTERS, register_exporter

try:
    from . import mqtt  # noqa: F401
except ImportError:
    pass

# Prometheus exporter (#14). Pure-stdlib, so import shouldn't fail, but
# guard it the same way as mqtt for consistency.
try:
    from . import prometheus  # noqa: F401
except ImportError:
    pass

__all__ = [
    "Exporter",
    "ExporterError",
    "EXPORTERS",
    "register_exporter",
]
