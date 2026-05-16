"""Outputs service — glues storage, adapters, and the live config together.

Responsibilities:
  * On startup, walk the device snapshot, ask each adapter what
    outputs the device exposes, and register them in SQLite.
  * After every poll cycle, re-read the latest device snapshot and
    update each output's `state` + `state_at` from the snapshot.
    This is how the dashboard's toggle reflects measured truth.
  * Service the `toggle()` write path: look up the device's config
    (transport + slave_id), dispatch through the right adapter,
    record the command + result, schedule a follow-up state refresh.

The service holds references to live infrastructure (scheduler,
config, store) rather than re-resolving them on every call —
matches the pattern AlertEngine / ForecastService follow elsewhere
in the daemon.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..config import Config
from ..storage import Store
from .base import ControllableOutput, OutputAdapter, WriteResult
from .registry import discover_outputs_for_device, get_adapter_for

log = logging.getLogger(__name__)


class OutputsService:
    def __init__(self, *, config: Config, store: Store, scheduler) -> None:
        self.config = config
        self.store = store
        self.scheduler = scheduler
        # Cache discovered (adapter, ControllableOutput) pairs keyed by
        # output_id. Refreshed at startup and whenever discover_all
        # runs. Used by the write path to dispatch without re-walking
        # adapters every call.
        self._known: dict[str, tuple[OutputAdapter, ControllableOutput]] = {}

    async def discover_all(self) -> None:
        """Walk the current device snapshot and register every output
        any adapter wants to expose. Idempotent — re-discovery
        preserves runtime state via the storage layer's UPSERT."""
        latest = await self.store.get_latest()
        discovered: dict[str, tuple[OutputAdapter, ControllableOutput]] = {}
        for device_label, snap in latest.items():
            device = {
                "label": device_label,
                "kind":   snap.get("_kind"),
                "vendor": snap.get("_vendor"),
                "model":  snap.get("model"),
                "latest": snap,
            }
            for adapter, output in discover_outputs_for_device(device):
                discovered[output.id] = (adapter, output)
                await self.store.upsert_output(
                    id=output.id,
                    device_label=output.device_label,
                    name=output.name,
                    kind=output.kind,
                    capabilities=list(output.capabilities),
                )
        self._known = discovered
        log.info("outputs: discovered %d controllable output(s): %s",
                 len(discovered), sorted(discovered.keys()) or "(none)")

    async def apply_snapshot(self) -> None:
        """Refresh each output's state from the latest device snapshot.
        Called by the scheduler after every poll cycle."""
        if not self._known:
            return
        latest = await self.store.get_latest()
        now = int(time.time())
        for output_id, (adapter, output) in self._known.items():
            snap = latest.get(output.device_label)
            if snap is None:
                continue
            state = adapter.read_state_from_snapshot(output, snap)
            if state is None:
                continue
            await self.store.update_output_state(output_id, state, now)

    async def toggle(
        self, output_id: str, on: bool, *, by: str,
    ) -> dict[str, Any]:
        """Apply a state change. Returns a dict suitable for direct
        JSON response — includes the WriteResult plus the resolved
        output row so the caller doesn't need a second round-trip."""
        if output_id not in self._known:
            # Stale UI — re-discover and try once more before giving up.
            await self.discover_all()
            if output_id not in self._known:
                raise KeyError(f"unknown output {output_id!r}")
        adapter, output = self._known[output_id]

        # Resolve the transport + slave_id from live config so we hit
        # the same BLE link the poller uses (shared lock).
        transport_id, slave_id = self._resolve_device(output.device_label)
        transport = self.scheduler.get_transport(transport_id)
        if transport is None:
            raise RuntimeError(
                f"transport {transport_id!r} not running — has the daemon "
                f"finished its first poll cycle?"
            )

        action = "on" if on else "off"
        now = int(time.time())
        try:
            result: WriteResult = await adapter.write(
                output, on, transport=transport, slave_id=slave_id,
            )
        except Exception as e:
            log.exception("outputs.toggle: adapter.write crashed")
            result = WriteResult(ok=False, confirmed_state=None,
                                 detail=f"{type(e).__name__}: {e}")

        result_str = "ok" if result.ok else f"fail:{result.detail or 'unknown'}"
        await self.store.record_output_command(
            output_id, action=action, at=now, by=by, result=result_str,
        )
        if result.confirmed_state is not None:
            await self.store.update_output_state(
                output_id, result.confirmed_state, now,
            )
        row = await self.store.get_output(output_id)
        return {
            "ok":               result.ok,
            "confirmed_state":  result.confirmed_state,
            "detail":           result.detail,
            "output":           row,
        }

    def _resolve_device(self, device_label: str) -> tuple[str, int]:
        """Find the configured (transport_id, slave_id) for a device
        by label. Config is the source of truth for live wiring;
        device_meta in storage doesn't carry the transport_id."""
        for d in self.config.devices:
            if d.label == device_label:
                return d.transport, d.slave_id
        raise KeyError(f"device {device_label!r} not found in config")
