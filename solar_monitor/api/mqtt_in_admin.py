"""REST endpoint for the MQTT-IN ingest service (#256).

Single status endpoint. Settings panel reads it for: broker
host/port, connection state (connecting/connected/reconnecting/
stopped), last error string, count of routes and merged devices.
"""
from __future__ import annotations

from typing import Any

from litestar import get
from litestar.datastructures import State

from ..scheduler import PollScheduler


@get("/api/mqtt_in/status")
async def get_mqtt_in_status(state: State) -> dict[str, Any]:
    """Service status for the Settings panel. Reports
    `{configured: false}` when the user hasn't added a `mqtt_in:`
    block to config.yaml (or has it set `enabled: false`)."""
    scheduler: PollScheduler = state["scheduler"]
    svc = scheduler.mqtt_in
    if svc is None:
        return {"configured": False}
    payload = svc.status()
    payload["configured"] = True
    return payload
