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

import json
from ..config import Config, SolarPauseCfg
from ..storage import Store
from .base import ControllableOutput, OutputAdapter, WriteResult
from .registry import discover_outputs_for_device, get_adapter_for
from . import schedules as _schedules
from . import solar_pause as _solar_pause
from . import smart_plug as _smart_plug

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
        preserves runtime state via the storage layer's UPSERT.

        Smart plugs (#163 followup) are registered alongside Modbus-
        discovered outputs. Their adapters live outside the device-
        snapshot model because plugs aren't devices we poll; we read
        them directly from config.smart_plugs."""
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
        # Smart plugs. Each config entry becomes one ControllableOutput
        # with id "plug.<slug>". Slugs are derived from the name; if
        # two plugs share a name the second one's id is auto-suffixed.
        seen_slugs: set[str] = set()
        for plug_cfg in getattr(self.config, "smart_plugs", []) or []:
            try:
                adapter = _smart_plug.build_adapter({
                    "kind":     plug_cfg.kind,
                    "host":     plug_cfg.host,
                    "name":     plug_cfg.name,
                    "user":     plug_cfg.user,
                    "password": plug_cfg.password,
                })
            except ValueError as e:
                log.warning("outputs: skipping smart_plug %r: %s",
                            plug_cfg.name, e)
                continue
            base = _slugify(plug_cfg.name) or "plug"
            slug = base
            i = 2
            while slug in seen_slugs:
                slug = f"{base}{i}"
                i += 1
            seen_slugs.add(slug)
            output_id = f"plug.{slug}"
            output = adapter.build_output(output_id)
            discovered[output_id] = (adapter, output)
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
        Called by the scheduler after every poll cycle.

        Smart plugs aren't in the snapshot so we read them out-of-band
        via the adapter's own HTTP probe. Best-effort: a plug that's
        offline keeps its last-known state until the next tick."""
        if not self._known:
            return
        latest = await self.store.get_latest()
        now = int(time.time())
        for output_id, (adapter, output) in self._known.items():
            if output.kind == "smart_plug":
                # Direct HTTP read against the plug. ~3 s timeout on
                # the urlopen so a dead plug doesn't stall the
                # service-tick for every other output.
                state = None
                try:
                    state = await adapter.read_state()
                except Exception:
                    log.exception("outputs: read_state failed for %s", output_id)
                if state is not None:
                    await self.store.update_output_state(output_id, state, now)
                continue
            snap = latest.get(output.device_label)
            if snap is None:
                continue
            state = adapter.read_state_from_snapshot(output, snap)
            if state is None:
                continue
            await self.store.update_output_state(output_id, state, now)

    async def evaluate_solar_pause(self) -> dict[str, Any]:
        """Tick the solar-pause controller (#163). Returns a dict with
        the decision + reason + applied flag, suitable for direct JSON
        response. No-op + cheap when the rule is disabled."""
        cfg: SolarPauseCfg | None = getattr(self.config, "solar_pause", None)
        if cfg is None or not cfg.enabled or not cfg.charger_output_id:
            return {"applied": False, "decision": "unchanged",
                    "reason": "rule disabled or no charger configured"}
        err = cfg.validate() if hasattr(cfg, "validate") else None
        if err:
            return {"applied": False, "decision": "unchanged",
                    "reason": f"config invalid: {err}"}

        bank = await self._read_bank_snapshot()
        if bank is None:
            return {"applied": False, "decision": "unchanged",
                    "reason": "no bank reading yet"}
        charger = await self._read_charger_snapshot(cfg.charger_output_id)
        if charger is None:
            return {"applied": False, "decision": "unchanged",
                    "reason": f"unknown output {cfg.charger_output_id!r}"}

        # The pure controller lives in solar_pause; pass the typed
        # config so the dataclass keeps the contract narrow.
        rule_cfg = _solar_pause.PauseCfg(
            enabled=cfg.enabled, charger_output_id=cfg.charger_output_id,
            target_soc=cfg.target_soc, recover_soc=cfg.recover_soc,
            hard_floor_soc=cfg.hard_floor_soc,
            pv_surplus_w=cfg.pv_surplus_w,
            cooldown_minutes=cfg.cooldown_minutes,
        )
        decision = _solar_pause.decide(
            rule_cfg, bank, charger, now_ts=int(time.time()),
        )
        out: dict[str, Any] = {
            "applied": False,
            "decision": decision.action,
            "reason": decision.reason,
            "bank_soc_pct": bank.soc_pct,
            "bank_net_w":   bank.net_w,
            "pv_w":         bank.pv_active_w,
            "charger_on":   charger.on,
        }
        if decision.action in ("force_on", "force_off"):
            try:
                result = await self.toggle(
                    cfg.charger_output_id,
                    on=(decision.action == "force_on"),
                    by="auto:solar_pause",
                )
                out["applied"] = bool(result.get("ok"))
                out["write_detail"] = result.get("detail")
            except Exception as e:
                log.exception("solar_pause: toggle crashed")
                out["applied"] = False
                out["write_detail"] = f"{type(e).__name__}: {e}"
        return out

    async def _read_bank_snapshot(self) -> "_solar_pause.BankSnapshot | None":
        """Reduce the latest device snapshot to (soc, net_w, pv_w).
        Mirrors the JS aggregateBank() reconciliation: shunt wins
        over BMS for system metrics; PV sums every charge_controller
        plus dcdc on the input side."""
        latest = await self.store.get_latest()
        if not latest:
            return None
        # Find a shunt first; fall back to summing the BMS pack snapshots.
        shunt = next((s for s in latest.values()
                      if s.get("_kind") == "shunt"), None)
        soc = None
        net_w = 0.0
        if shunt is not None:
            soc = shunt.get("soc_pct")
            v = float(shunt.get("voltage_v") or 0)
            i = float(shunt.get("current_a") or 0)
            net_w = (shunt.get("power_w") if shunt.get("power_w") is not None
                     else v * i)
        else:
            batts = [s for s in latest.values()
                     if s.get("_kind") == "smart_battery"]
            if not batts:
                return None
            cap = sum(float(b.get("capacity_ah") or 0) for b in batts)
            rem = sum(float(b.get("remaining_charge_ah") or 0) for b in batts)
            soc = (rem / cap * 100) if cap > 0 else None
            mean_v = sum(float(b.get("voltage_v") or 0) for b in batts) / len(batts)
            sum_i  = sum(float(b.get("current_a") or 0) for b in batts)
            net_w = mean_v * sum_i
        if soc is None:
            return None
        # PV input across MPPTs + DC-DC chargers' solar-side power.
        pv_w = 0.0
        for snap in latest.values():
            if snap.get("_kind") in ("charge_controller", "dcdc"):
                pv_w += float(snap.get("pv_power_w") or snap.get("power_w") or 0)
        return _solar_pause.BankSnapshot(
            soc_pct=float(soc), net_w=float(net_w), pv_active_w=float(pv_w),
        )

    async def _read_charger_snapshot(
        self, output_id: str,
    ) -> "_solar_pause.ChargerSnapshot | None":
        """Pull the most recent state + command record off the output
        row. last_command_json carries `at` + `by`; we partition it
        into the auto / manual timestamps the controller needs."""
        row = await self.store.get_output(output_id)
        if row is None:
            return None
        on = bool(row.get("state"))
        last_auto = 0
        last_manual = 0
        cmd = row.get("last_command_json")
        if cmd:
            try:
                cmd = json.loads(cmd) if isinstance(cmd, str) else cmd
                ts = int(cmd.get("at") or 0)
                by = cmd.get("by") or ""
                if by.startswith("auto"):
                    last_auto = ts
                else:
                    last_manual = ts
            except Exception:
                pass
        return _solar_pause.ChargerSnapshot(
            on=on,
            last_auto_change_at=last_auto,
            last_manual_toggle_at=last_manual,
        )

    async def fire_schedules_if_due(self) -> int:
        """Tick the schedule engine. Called by the scheduler on every
        poll cycle; cheap when no schedules are configured (single
        empty SELECT). Returns the number fired this tick."""
        try:
            weather = await _schedules.load_weather_cache(self.store)
            return await _schedules.fire_due_schedules(
                store=self.store, outputs_service=self,
                weather_cache=weather, now_ts=int(time.time()),
            )
        except Exception:
            log.exception("schedule tick failed")
            return 0

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

        # Smart plugs talk HTTP directly to a LAN host. They share the
        # same toggle() entry point as Modbus outputs but skip the
        # transport-resolution dance — there's no shared BLE link, no
        # slave id; the adapter knows its own host.
        is_plug = output.kind == "smart_plug"
        if is_plug:
            transport = None
            slave_id = 0
        else:
            # Resolve the transport + slave_id from live config so we
            # hit the same BLE link the poller uses (shared lock).
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


def _slugify(s: str) -> str:
    """Lowercase + ASCII-only + hyphen-separated slug for use as the
    second half of a `plug.<slug>` output id. Drops anything outside
    [a-z0-9-]; collapses repeats. Empty string in -> empty string out
    (caller falls back to "plug")."""
    out = []
    prev_dash = True
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    s2 = "".join(out).strip("-")
    return s2
