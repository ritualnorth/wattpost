"""Local alert engine.

Importing this package brings in the built-in transports (ntfy / Discord
/ webhook) for side-effect registration so they're available by the
time the engine starts.
"""
from .base import AlertEvent, AlertRule, NotificationTransport
from .engine import AlertEngine, build_alert_context
from .registry import NOTIFICATION_TRANSPORTS, register_notification_transport
from . import transports  # noqa: F401 — registers built-in transports

__all__ = [
    "AlertEvent",
    "AlertRule",
    "AlertEngine",
    "NotificationTransport",
    "NOTIFICATION_TRANSPORTS",
    "build_alert_context",
    "register_notification_transport",
]
