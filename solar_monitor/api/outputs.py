"""REST endpoints for controllable outputs + schedules (#104 + #117).

Wired surface:
    GET    /api/outputs                              list every output
    GET    /api/outputs?device=<label>               list for one device
    POST   /api/outputs/<id>/toggle                  flip state
    POST   /api/outputs/<id>/confirm                 pass safety gate
    GET    /api/outputs/<id>/schedules               list rules
    POST   /api/outputs/<id>/schedules               create rule
    PUT    /api/outputs/<id>/schedules/<sid>         edit rule
    DELETE /api/outputs/<id>/schedules/<sid>         remove rule
"""
from __future__ import annotations

import logging
from typing import Any

import msgspec
from litestar import delete, get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..scheduler import PollScheduler

log = logging.getLogger(__name__)


@get("/api/outputs")
async def list_outputs(state: State, device: str | None = None) -> dict[str, Any]:
    scheduler: PollScheduler = state["scheduler"]
    store = state["store"]
    return {"outputs": await store.list_outputs(device)}


class ToggleRequest(msgspec.Struct):
    on: bool


@post("/api/outputs/{output_id:str}/toggle")
async def toggle_output(
    output_id: str, data: ToggleRequest, state: State,
) -> dict[str, Any]:
    scheduler: PollScheduler = state["scheduler"]
    store = state["store"]
    row = await store.get_output(output_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"output {output_id!r} not found")
    if not row.get("safety_confirmed"):
        raise HTTPException(
            status_code=409,
            detail="safety gate not passed — POST /api/outputs/<id>/confirm first",
        )
    try:
        result = await scheduler.outputs.toggle(output_id, data.on, by="user")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not result.get("ok"):
        # 502 — the underlying transport/adapter reported the write
        # didn't land. Keep the detail in the body so the UI can show
        # the real reason rather than a generic toast.
        raise HTTPException(status_code=502, detail=result)
    return result


@post("/api/outputs/{output_id:str}/confirm")
async def confirm_output_safety(output_id: str, state: State) -> dict[str, Any]:
    store = state["store"]
    row = await store.get_output(output_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"output {output_id!r} not found")
    await store.confirm_output_safety(output_id)
    log.info("outputs: %s safety gate confirmed", output_id)
    return {"ok": True, "output": await store.get_output(output_id)}


# ---------- schedules (#117) ----------

class ScheduleRequest(msgspec.Struct):
    """All fields optional on create — server defaults action="on",
    trigger_kind="time", days_mask=127, enabled=True. PUT can send any
    subset of fields to patch."""
    action:       str | None = None       # "on" | "off"
    trigger_kind: str | None = None       # "time" | "sunrise" | "sunset"
    trigger_time: str | None = None       # "HH:MM" when kind=time
    offset_min:   int | None = None       # minutes ± sunrise/sunset
    days_mask:    int | None = None       # 0-127 (MTWTFSS bits)
    enabled:      bool | None = None


def _validate_schedule_fields(
    *, action: str | None, trigger_kind: str | None,
    trigger_time: str | None, offset_min: int | None,
    days_mask: int | None,
) -> None:
    if action is not None and action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="action must be 'on' or 'off'")
    if trigger_kind is not None and trigger_kind not in ("time", "sunrise", "sunset"):
        raise HTTPException(
            status_code=400,
            detail="trigger_kind must be 'time', 'sunrise', or 'sunset'",
        )
    if trigger_kind == "time" and trigger_time:
        # Strict HH:MM check — the scheduler's parser is lenient but
        # nice to fail fast at API boundary.
        if ":" not in trigger_time:
            raise HTTPException(status_code=400, detail="trigger_time must be 'HH:MM'")
        try:
            h, m = trigger_time.split(":", 1)
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=400, detail="trigger_time must be 'HH:MM'")
    if offset_min is not None and not (-720 <= offset_min <= 720):
        # ±12h cap — beyond that the user probably meant a different
        # trigger kind. Hard fence keeps the scheduler maths sane.
        raise HTTPException(
            status_code=400,
            detail="offset_min must be in [-720, 720] (±12h)",
        )
    if days_mask is not None and not (0 <= days_mask <= 127):
        raise HTTPException(
            status_code=400,
            detail="days_mask must be in [0, 127] (7-bit MTWTFSS)",
        )


@get("/api/outputs/{output_id:str}/schedules")
async def list_output_schedules(output_id: str, state: State) -> dict[str, Any]:
    store = state["store"]
    if await store.get_output(output_id) is None:
        raise HTTPException(status_code=404, detail=f"output {output_id!r} not found")
    return {"schedules": await store.list_schedules(output_id)}


@post("/api/outputs/{output_id:str}/schedules")
async def create_output_schedule(
    output_id: str, data: ScheduleRequest, state: State,
) -> dict[str, Any]:
    store = state["store"]
    if await store.get_output(output_id) is None:
        raise HTTPException(status_code=404, detail=f"output {output_id!r} not found")
    # Server defaults for any missing field. Action defaults to "on"
    # because the most common first rule users make is "turn it on
    # at <time>"; the off-companion gets paired in a follow-up.
    action       = data.action or "on"
    trigger_kind = data.trigger_kind or "time"
    trigger_time = data.trigger_time
    offset_min   = data.offset_min or 0
    days_mask    = data.days_mask if data.days_mask is not None else 127
    enabled      = True if data.enabled is None else bool(data.enabled)
    _validate_schedule_fields(
        action=action, trigger_kind=trigger_kind,
        trigger_time=trigger_time, offset_min=offset_min,
        days_mask=days_mask,
    )
    sched_id = await store.create_schedule(
        output_id=output_id, action=action, trigger_kind=trigger_kind,
        trigger_time=trigger_time, offset_min=offset_min,
        days_mask=days_mask, enabled=enabled,
    )
    log.info("schedules: created %d for output %s", sched_id, output_id)
    return {"ok": True, "schedule": await store.get_schedule(sched_id)}


@put("/api/outputs/{output_id:str}/schedules/{schedule_id:int}")
async def update_output_schedule(
    output_id: str, schedule_id: int, data: ScheduleRequest, state: State,
) -> dict[str, Any]:
    store = state["store"]
    existing = await store.get_schedule(schedule_id)
    if existing is None or existing["output_id"] != output_id:
        raise HTTPException(
            status_code=404,
            detail=f"schedule {schedule_id} not found under output {output_id!r}",
        )
    _validate_schedule_fields(
        action=data.action, trigger_kind=data.trigger_kind,
        trigger_time=data.trigger_time, offset_min=data.offset_min,
        days_mask=data.days_mask,
    )
    # Apply only the fields the client actually sent. msgspec.Struct
    # with `None` defaults makes this easy — None means "don't change".
    fields: dict[str, Any] = {}
    if data.action       is not None: fields["action"]       = data.action
    if data.trigger_kind is not None: fields["trigger_kind"] = data.trigger_kind
    if data.trigger_time is not None: fields["trigger_time"] = data.trigger_time
    if data.offset_min   is not None: fields["offset_min"]   = data.offset_min
    if data.days_mask    is not None: fields["days_mask"]    = data.days_mask
    if data.enabled      is not None: fields["enabled"]      = data.enabled
    await store.update_schedule(schedule_id, **fields)
    return {"ok": True, "schedule": await store.get_schedule(schedule_id)}


@delete("/api/outputs/{output_id:str}/schedules/{schedule_id:int}", status_code=200)
async def delete_output_schedule(
    output_id: str, schedule_id: int, state: State,
) -> dict[str, Any]:
    store = state["store"]
    existing = await store.get_schedule(schedule_id)
    if existing is None or existing["output_id"] != output_id:
        raise HTTPException(
            status_code=404,
            detail=f"schedule {schedule_id} not found under output {output_id!r}",
        )
    await store.delete_schedule(schedule_id)
    log.info("schedules: deleted %d", schedule_id)
    return {"ok": True, "deleted": schedule_id}
