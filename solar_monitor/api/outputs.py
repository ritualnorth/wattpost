"""REST endpoints for controllable outputs (#104).

Wired surface:
    GET  /api/outputs                  — list every registered output
    GET  /api/outputs?device=<label>   — list for one device
    POST /api/outputs/<id>/toggle      — flip state (body: {"on": bool})
    POST /api/outputs/<id>/confirm     — pass the one-shot safety gate

Schedules (Phase B) come in a follow-up commit; this file stays
focused on the instant toggle path.
"""
from __future__ import annotations

import logging
from typing import Any

import msgspec
from litestar import get, post
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
