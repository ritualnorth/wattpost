"""REST endpoint for the USB GPS service (#125).

Single status endpoint for now, the dashboard's Settings →
Location panel reads from here to surface current fix + age. No
config CRUD yet: enable by adding a `gps:` block to config.yaml,
restart the daemon. Wizard-driven configuration follows in a
later commit once we know the real-world UX shape from the first
customers wiring up a VK-162.
"""
from __future__ import annotations

from typing import Any

from litestar import get
from litestar.datastructures import State

from ..scheduler import PollScheduler


@get("/api/gps")
async def get_gps_status(state: State) -> dict[str, Any]:
    """Report the GPS service's current state.

    Returns `{configured: false}` when the daemon was built without
    a `gps:` config block; otherwise the standard status payload
    (latest_fix, fix age, last applied lat/lon, etc.). The UI uses
    this to render the Settings → Location panel + show stale-fix
    warnings."""
    scheduler: PollScheduler = state["scheduler"]
    if scheduler.gps is None:
        return {"configured": False}
    payload = scheduler.gps.get_status()
    payload["configured"] = True
    return payload
