"""Alert primitives, rule schema, event payload, transport ABC.

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
    # Don't re-fire the same rule within this window, prevents alert
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


_METRIC_LABELS = {
    "bank.soc_pct":        ("State of charge", "%"),
    "bank.voltage_v":      ("Bank voltage",     "V"),
    "bank.netW":           ("Net power",        "W"),
    "bank.net_w":          ("Net power",        "W"),
    "bank.temperature_c":  ("Bank temperature", "°C"),
    "bank.cell_drift_v":   ("Cell drift",       "V"),
    "bank.min_cell_v":     ("Lowest cell",      "V"),
    "bank.max_cell_v":     ("Highest cell",     "V"),
}
_OP_WORDS = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥", "eq": "=", "neq": "≠"}


def humanise_metric(dotted: str) -> str:
    """Return a human label for a metric dotted-path, falling back to
    the raw path for vendor-specific metrics we don't have copy for.
    Example: `bank.soc_pct` → "State of charge"."""
    if dotted in _METRIC_LABELS:
        return _METRIC_LABELS[dotted][0]
    # Per-device metric (devices.<id>.<metric>), pull the trailing
    # piece and humanise the name a bit so it reads less code-like.
    tail = dotted.rsplit(".", 1)[-1]
    return tail.replace("_", " ").capitalize() or dotted


def metric_unit(dotted: str) -> str:
    """Display unit for a metric (always with a leading space when
    non-empty, so callers can `f'{value}{unit}'` without thinking)."""
    if dotted in _METRIC_LABELS:
        u = _METRIC_LABELS[dotted][1]
        return u if u in ("%", "°C") else (" " + u)
    if dotted.endswith("_pct"):     return "%"
    if dotted.endswith(("_w", "_W")): return " W"
    if dotted.endswith(("_v", "_V")): return " V"
    if dotted.endswith(("_a", "_A")): return " A"
    if dotted.endswith(("_c", "_C")): return "°C"
    if dotted.endswith(("_min",)):  return " min"
    return ""


def fmt_value(dotted: str, value: float) -> str:
    """Value + unit, rounded sensibly for the metric. SoC + currents
    to one decimal; voltage to two; watts and minutes to whole nums.
    """
    u = metric_unit(dotted)
    if dotted.endswith(("_v", "_V")) or dotted == "bank.voltage_v":
        return f"{value:.2f}{u}"
    if dotted.endswith(("_w", "_W")) or "power" in dotted:
        return f"{round(value)}{u}"
    if dotted in ("bank.soc_pct",) or dotted.endswith("_pct"):
        return f"{value:.1f}{u}"
    if dotted.endswith(("_a", "_A")):
        return f"{value:.1f}{u}"
    # Default: 1 dp when fractional, else integer.
    if value == int(value):
        return f"{int(value)}{u}"
    return f"{value:.2f}{u}"


def humanise_op(op: str) -> str:
    return _OP_WORDS.get(op, op)


def render_alert_summary(event: "AlertEvent") -> str:
    """One-line "{metric} is {value} (threshold {op} {threshold})"
    summary used by every transport that wants a single string body."""
    return (
        f"{humanise_metric(event.metric)} is "
        f"{fmt_value(event.metric, event.value)} "
        f"(threshold {humanise_op(event.op)} "
        f"{fmt_value(event.metric, event.threshold)})"
    )


class NotificationTransport(abc.ABC):
    """One outbound channel, ntfy topic, Discord webhook, etc."""

    id: str

    async def start(self) -> None:  # noqa: D401, keep simple lifecycle
        return None

    async def stop(self) -> None:
        return None

    @abc.abstractmethod
    async def send(self, event: AlertEvent) -> None:
        """Push one event to this channel. Must not raise on transient
        failures, log + return so the engine can continue dispatching to
        other channels."""
