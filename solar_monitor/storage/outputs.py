"""Controllable outputs and their schedules (mixed into Store)."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class OutputsMixin:
    """Output + schedule persistence. Uses self._db."""

    # ---------- controllable outputs (#104) ----------

    async def upsert_output(
        self, *, id: str, device_label: str, name: str, kind: str,
        capabilities: list[str],
    ) -> None:
        """Register or update an output definition. Idempotent on (id),
        re-discovery on daemon restart preserves state + safety_confirmed.
        Capabilities are overwritten because the adapter is the source
        of truth for what an output can do."""
        if self._db is None:
            raise RuntimeError("Store not open")
        caps_json = json.dumps(sorted(set(capabilities)))
        await self._db.execute(
            "INSERT INTO controllable_outputs (id, device_label, name, kind, capabilities_json) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  device_label = excluded.device_label, "
            "  name = excluded.name, "
            "  kind = excluded.kind, "
            "  capabilities_json = excluded.capabilities_json",
            (id, device_label, name, kind, caps_json),
        )
        await self._db.commit()

    async def list_outputs(self, device_label: str | None = None) -> list[dict[str, Any]]:
        """List controllable outputs, optionally filtered to one device."""
        if self._db is None:
            raise RuntimeError("Store not open")
        sql = (
            "SELECT id, device_label, name, kind, state, state_at, "
            "       last_command_json, safety_confirmed, capabilities_json "
            "FROM controllable_outputs"
        )
        args: tuple = ()
        if device_label is not None:
            sql += " WHERE device_label = ?"
            args = (device_label,)
        sql += " ORDER BY device_label, name"
        out: list[dict[str, Any]] = []
        async with self._db.execute(sql, args) as cur:
            async for row in cur:
                out.append(_row_to_output(row))
        return out

    async def get_output(self, output_id: str) -> dict[str, Any] | None:
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "SELECT id, device_label, name, kind, state, state_at, "
            "       last_command_json, safety_confirmed, capabilities_json "
            "FROM controllable_outputs WHERE id = ?",
            (output_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_output(row) if row else None

    async def update_output_state(
        self, output_id: str, state: int | None, state_at: int,
    ) -> None:
        """Write the latest read-back state. Called by the poller after
        each device snapshot reflects the output's truth value."""
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "UPDATE controllable_outputs SET state = ?, state_at = ? WHERE id = ?",
            (state, state_at, output_id),
        )
        await self._db.commit()

    async def record_output_command(
        self, output_id: str, *, action: str, at: int, by: str, result: str,
    ) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        payload = json.dumps({"action": action, "at": at, "by": by, "result": result})
        await self._db.execute(
            "UPDATE controllable_outputs SET last_command_json = ? WHERE id = ?",
            (payload, output_id),
        )
        await self._db.commit()

    async def confirm_output_safety(self, output_id: str) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "UPDATE controllable_outputs SET safety_confirmed = 1 WHERE id = ?",
            (output_id,),
        )
        await self._db.commit()

    # ---------- output schedules (#117) ----------

    async def list_schedules(self, output_id: str | None = None) -> list[dict[str, Any]]:
        """List schedules, optionally filtered to one output. The
        scheduler tick passes None to walk every schedule."""
        if self._db is None:
            raise RuntimeError("Store not open")
        sql = (
            "SELECT id, output_id, action, trigger_kind, trigger_time, "
            "       offset_min, days_mask, enabled, last_run_at, last_run_result "
            "FROM output_schedules"
        )
        args: tuple = ()
        if output_id is not None:
            sql += " WHERE output_id = ?"
            args = (output_id,)
        sql += " ORDER BY output_id, trigger_kind, trigger_time"
        out: list[dict[str, Any]] = []
        async with self._db.execute(sql, args) as cur:
            async for row in cur:
                out.append(_row_to_schedule(row))
        return out

    async def get_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "SELECT id, output_id, action, trigger_kind, trigger_time, "
            "       offset_min, days_mask, enabled, last_run_at, last_run_result "
            "FROM output_schedules WHERE id = ?",
            (schedule_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_schedule(row) if row else None

    async def create_schedule(
        self, *, output_id: str, action: str, trigger_kind: str,
        trigger_time: str | None, offset_min: int, days_mask: int,
        enabled: bool,
    ) -> int:
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "INSERT INTO output_schedules "
            "  (output_id, action, trigger_kind, trigger_time, "
            "   offset_min, days_mask, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (output_id, action, trigger_kind, trigger_time,
             offset_min, days_mask, 1 if enabled else 0),
        ) as cur:
            sched_id = cur.lastrowid
        await self._db.commit()
        return int(sched_id)

    async def update_schedule(self, schedule_id: int, **fields: Any) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        allowed = {
            "action", "trigger_kind", "trigger_time", "offset_min",
            "days_mask", "enabled",
        }
        sets = []
        args: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            args.append(v)
        if not sets:
            return
        args.append(schedule_id)
        await self._db.execute(
            f"UPDATE output_schedules SET {', '.join(sets)} WHERE id = ?",
            tuple(args),
        )
        await self._db.commit()

    async def delete_schedule(self, schedule_id: int) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "DELETE FROM output_schedules WHERE id = ?", (schedule_id,),
        )
        await self._db.commit()

    async def mark_schedule_run(
        self, schedule_id: int, at_ts: int, result: str,
    ) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "UPDATE output_schedules SET last_run_at = ?, last_run_result = ? "
            "WHERE id = ?",
            (at_ts, result, schedule_id),
        )
        await self._db.commit()


def _row_to_schedule(row: tuple) -> dict[str, Any]:
    (id_, output_id, action, trigger_kind, trigger_time,
     offset_min, days_mask, enabled, last_run_at, last_run_result) = row
    return {
        "id":            int(id_),
        "output_id":     output_id,
        "action":        action,
        "trigger_kind":  trigger_kind,
        "trigger_time":  trigger_time,
        "offset_min":    int(offset_min) if offset_min is not None else 0,
        "days_mask":     int(days_mask) if days_mask is not None else 127,
        "enabled":       bool(enabled),
        "last_run_at":   int(last_run_at) if last_run_at is not None else None,
        "last_run_result": last_run_result,
    }


def _row_to_output(row: tuple) -> dict[str, Any]:
    id_, dev, name, kind, state, state_at, cmd_json, safety, caps_json = row
    try:
        caps = json.loads(caps_json) if caps_json else []
    except json.JSONDecodeError:
        caps = []
    try:
        last_cmd = json.loads(cmd_json) if cmd_json else None
    except json.JSONDecodeError:
        last_cmd = None
    return {
        "id": id_,
        "device_label": dev,
        "name": name,
        "kind": kind,
        "state": int(state) if state is not None else None,
        "state_at": int(state_at) if state_at is not None else None,
        "last_command": last_cmd,
        "safety_confirmed": bool(safety),
        "capabilities": caps,
    }
