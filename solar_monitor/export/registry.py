"""Exporter registry — mirrors transport + vendor pattern."""
from __future__ import annotations

from typing import Callable

from .base import Exporter

ExporterFactory = Callable[[dict], Exporter]

EXPORTERS: dict[str, ExporterFactory] = {}


def register_exporter(type_name: str) -> Callable[[ExporterFactory], ExporterFactory]:
    def _wrap(factory: ExporterFactory) -> ExporterFactory:
        if type_name in EXPORTERS:
            raise ValueError(f"Exporter type {type_name!r} already registered")
        EXPORTERS[type_name] = factory
        return factory
    return _wrap
