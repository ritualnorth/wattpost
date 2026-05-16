"""Registry of per-vendor output adapters.

Adapters call `register_adapter(...)` at import time; the outputs
service calls `discover_outputs_for_device(...)` to fan out across
adapters and collect every output a device exposes. Adapters are
keyed by `device.kind` (the existing device-classification field —
`charge_controller`, `smart_battery`, `bms`, etc.) so a single
device kind can have multiple adapters cooperating.
"""
from __future__ import annotations

from typing import Any

from .base import ControllableOutput, OutputAdapter

# kind -> list of adapters that handle it
_REGISTRY: dict[str, list[OutputAdapter]] = {}


def register_adapter(adapter: OutputAdapter) -> None:
    for kind in adapter.handles_kinds:
        _REGISTRY.setdefault(kind, []).append(adapter)


def get_adapter_for(kind: str, vendor: str | None = None) -> OutputAdapter | None:
    """Find the adapter for a given (kind, vendor). When vendor is
    None we return the first adapter registered for that kind —
    works for one-adapter-per-kind cases (Rover-load), useful in
    the write path where we already know which output we're flipping
    from its id."""
    candidates = _REGISTRY.get(kind, [])
    if not candidates:
        return None
    if vendor is None:
        return candidates[0]
    for a in candidates:
        if a.vendor == vendor:
            return a
    return candidates[0]


def discover_outputs_for_device(device: dict[str, Any]) -> list[tuple[OutputAdapter, ControllableOutput]]:
    """Run every registered adapter for this device's kind, collect
    the outputs they discover, paired with the adapter that produced
    each one (so the caller can look up the writer later)."""
    kind = device.get("kind") or device.get("_kind")
    if not kind:
        return []
    out: list[tuple[OutputAdapter, ControllableOutput]] = []
    for adapter in _REGISTRY.get(kind, []):
        for o in adapter.discover(device):
            out.append((adapter, o))
    return out


def all_adapters() -> list[OutputAdapter]:
    """Used for diagnostics + tests. Order is registration order."""
    seen: set[int] = set()
    out: list[OutputAdapter] = []
    for adapters in _REGISTRY.values():
        for a in adapters:
            if id(a) in seen:
                continue
            seen.add(id(a))
            out.append(a)
    return out
