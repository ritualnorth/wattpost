"""Exporter abstract base.

An Exporter receives every poll result from the scheduler and forwards it
to an external system (MQTT broker, InfluxDB, webhook, etc).

Exporters must:
  - never block the scheduler (operate via their own queue / async tasks)
  - tolerate the sink being unavailable (drop, retry, or buffer, their call)
  - be safely startable + stoppable; the scheduler owns lifecycle
"""
from __future__ import annotations

import abc
from typing import Any


class ExporterError(Exception):
    """Base for exporter failures (non-fatal; logged, not raised)."""


class Exporter(abc.ABC):
    """One destination that receives poll results."""

    id: str  # stable identifier, used in logs

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect / initialise. Idempotent."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Tear down cleanly. Idempotent."""

    @abc.abstractmethod
    async def export(self, result: dict[str, Any]) -> None:
        """Receive one full poll result. Should return quickly, buffer
        internally if downstream is slow."""

    async def __aenter__(self) -> "Exporter":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()
