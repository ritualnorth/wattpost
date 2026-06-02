"""Transport registry.

Every transport implementation registers itself here by type-string. The
orchestrator looks up implementations by the `type:` field in YAML config.
"""
from __future__ import annotations

from typing import Callable

from .base import Transport

TransportFactory = Callable[[dict], Transport]

TRANSPORTS: dict[str, TransportFactory] = {}


def register_transport(type_name: str) -> Callable[[TransportFactory], TransportFactory]:
    """Decorator: register a Transport factory under a type string."""

    def _wrap(factory: TransportFactory) -> TransportFactory:
        if type_name in TRANSPORTS:
            raise ValueError(f"Transport type {type_name!r} already registered")
        TRANSPORTS[type_name] = factory
        return factory

    return _wrap
