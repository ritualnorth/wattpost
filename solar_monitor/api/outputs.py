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


# ---------- solar-aware pause (#163) ----------

class SolarPausePatch(msgspec.Struct):
    enabled:           bool | None  = None
    charger_output_id: str  | None  = None
    target_soc:        float | None = None
    recover_soc:       float | None = None
    hard_floor_soc:    float | None = None
    pv_surplus_w:      float | None = None
    cooldown_minutes:  int  | None  = None


@get("/api/outputs/solar_pause")
async def get_solar_pause(state: State) -> dict[str, Any]:
    scheduler: PollScheduler = state["scheduler"]
    cfg = getattr(scheduler.config, "solar_pause", None)
    out = {
        "enabled":           getattr(cfg, "enabled", False),
        "charger_output_id": getattr(cfg, "charger_output_id", None),
        "target_soc":        getattr(cfg, "target_soc", 80.0),
        "recover_soc":       getattr(cfg, "recover_soc", 50.0),
        "hard_floor_soc":    getattr(cfg, "hard_floor_soc", 30.0),
        "pv_surplus_w":      getattr(cfg, "pv_surplus_w", 50.0),
        "cooldown_minutes":  getattr(cfg, "cooldown_minutes", 30),
    }
    out["status"] = await scheduler.outputs.evaluate_solar_pause()
    out["status"].setdefault("applied", False)
    return out


@put("/api/outputs/solar_pause")
async def patch_solar_pause(
    data: SolarPausePatch, state: State,
) -> dict[str, Any]:
    """Persist + hot-apply solar-pause settings (#163).

    Validates the threshold ordering before touching disk so an
    inconsistent payload never lands in config.yaml — a partial
    write here previously locked users into a config that the
    daemon refused to load."""
    import yaml as _yaml
    from pathlib import Path as _Path
    from ..config import SolarPauseCfg

    scheduler: PollScheduler = state["scheduler"]
    existing = getattr(scheduler.config, "solar_pause", None)
    merged_kwargs: dict[str, Any] = {
        "enabled":           getattr(existing, "enabled", False),
        "charger_output_id": getattr(existing, "charger_output_id", None),
        "target_soc":        getattr(existing, "target_soc", 80.0),
        "recover_soc":       getattr(existing, "recover_soc", 50.0),
        "hard_floor_soc":    getattr(existing, "hard_floor_soc", 30.0),
        "pv_surplus_w":      getattr(existing, "pv_surplus_w", 50.0),
        "cooldown_minutes":  getattr(existing, "cooldown_minutes", 30),
    }
    if data.enabled           is not None: merged_kwargs["enabled"]           = data.enabled
    if data.charger_output_id is not None: merged_kwargs["charger_output_id"] = data.charger_output_id
    if data.target_soc        is not None: merged_kwargs["target_soc"]        = data.target_soc
    if data.recover_soc       is not None: merged_kwargs["recover_soc"]       = data.recover_soc
    if data.hard_floor_soc    is not None: merged_kwargs["hard_floor_soc"]    = data.hard_floor_soc
    if data.pv_surplus_w      is not None: merged_kwargs["pv_surplus_w"]      = data.pv_surplus_w
    if data.cooldown_minutes  is not None: merged_kwargs["cooldown_minutes"]  = data.cooldown_minutes

    new_cfg = SolarPauseCfg(**merged_kwargs)
    # Re-use the controller's own validator so the daemon and the API
    # agree on what's acceptable. Disabled rules skip validation so
    # users can park half-finished configs without 400s.
    if merged_kwargs["enabled"]:
        from ..outputs import solar_pause as _sp
        rule_cfg = _sp.PauseCfg(**merged_kwargs)
        err = rule_cfg.validate()
        if err:
            raise HTTPException(status_code=400, detail=err)

    config_path: str = state.get("config_path", "config.yaml")
    path = _Path(config_path)
    raw = _yaml.safe_load(path.read_text()) or {}
    raw["solar_pause"] = {
        "enabled":           new_cfg.enabled,
        "charger_output_id": new_cfg.charger_output_id,
        "target_soc":        new_cfg.target_soc,
        "recover_soc":       new_cfg.recover_soc,
        "hard_floor_soc":    new_cfg.hard_floor_soc,
        "pv_surplus_w":      new_cfg.pv_surplus_w,
        "cooldown_minutes":  new_cfg.cooldown_minutes,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    scheduler.config.solar_pause = new_cfg
    log.info("solar_pause: settings updated (enabled=%s, target=%s, recover=%s)",
             new_cfg.enabled, new_cfg.target_soc, new_cfg.recover_soc)
    return await get_solar_pause(state)
