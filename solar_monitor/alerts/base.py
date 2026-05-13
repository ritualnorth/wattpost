"""Alert primitives — rule schema, event payload, transport ABC.

Same shapes as what the cloud tier will consume; only the *evaluator*
differs between local and cloud.
"""
from __future__ import annotations

import abc
from typing import Any

import msgspec


class AlertRule(msgspec.Struct, kw_only=True):
    id: str
    name: str
    # Dotted path into the alert context: bank.soc_pct, bank.netW,
    # devices.battery_0.cell_drift_v, etc.
    metric: str
    # Comparison operator: lt | lte | gt | gte | eq | neq.
    op: str
    threshold: float
    # warn | alarm. Drives styling and ntfy priority.
    severity: str = "warn"
    # Don't re-fire the same rule within this window — prevents alert
    # storms on flapping metrics.
    cooldown_seconds: int = 1800
    # Transport ids to dispatch this rule to (must match an entry in
    # `notification_transports` in config.yaml).
    transports: list[str]


class AlertEvent(msgspec.Struct):
    rule_id: str
    name: str
    severity: str
    metric: str
    value: float
    threshold: float
    op: str
    ts: int


class NotificationTransport(abc.ABC):
    """One outbound channel — ntfy topic, Discord webhook, etc."""

    id: str

    async def start(self) -> None:  # noqa: D401 — keep simple lifecycle
        return None

    async def stop(self) -> None:
        return None

    @abc.abstractmethod
    async def send(self, event: AlertEvent) -> None:
        """Push one event to this channel. Must not raise on transient
        failures — log + return so the engine can continue dispatching to
        other channels."""
