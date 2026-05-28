"""Pluggable notification-transport registry.

Each transport module registers a factory keyed by its YAML `type` field.
"""
from __future__ import annotations

from typing import Callable

from .base import NotificationTransport

NOTIFICATION_TRANSPORTS: dict[str, Callable[[dict], NotificationTransport]] = {}


def register_notification_transport(name: str) -> Callable[
    [Callable[[dict], NotificationTransport]],
    Callable[[dict], NotificationTransport],
]:
    def deco(fn: Callable[[dict], NotificationTransport]) -> Callable[[dict], NotificationTransport]:
        if name in NOTIFICATION_TRANSPORTS:
            raise ValueError(f"notification transport {name!r} already registered")
        NOTIFICATION_TRANSPORTS[name] = fn
        return fn
    return deco
