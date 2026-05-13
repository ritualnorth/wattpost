"""Litestar app factory.

Owns the lifecycle of the storage + scheduler: opens the DB on startup,
spawns the poll loop, exposes REST endpoints, and shuts everything down
cleanly on signals.

This is *the* product surface. The CLI's `serve` subcommand wires this up
and runs uvicorn.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

from litestar import Litestar, get, post
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import File, Stream
from litestar.static_files import create_static_files_router

from ..config import Config
from ..scheduler import PollScheduler
from ..storage import Store
from .setup import (
    list_setup_transports,
    known_devices,
    probe,
    add_device,
)
from .alerts_admin import (
    create_rule, update_rule, delete_rule,
    create_transport, update_transport, delete_transport,
)
from .system import (
    system_info, tailscale_status, tailscale_up, tailscale_down,
    tailscale_serve,
)


def _web_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


@get("/api/health", sync_to_thread=False)
def health() -> dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@get("/api/today")
async def today(state: State) -> dict[str, Any]:
    """Energy aggregates for the current calendar day (local time)."""
    store: Store = state["store"]
    now = int(time.time())
    local = time.localtime(now)
    midnight = int(time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    ))
    return await store.today_aggregate(midnight, now)


@get("/api/poll_run")
async def last_poll_run(state: State) -> dict[str, Any]:
    store: Store = state["store"]
    scheduler: PollScheduler = state["scheduler"]
    return {
        "last_run": await store.last_poll_run(),
        "scheduler_running": scheduler._task is not None and not scheduler._task.done(),
    }


@get("/api/devices")
async def list_devices(state: State) -> dict[str, Any]:
    store: Store = state["store"]
    devs = await store.list_devices()
    latest = await store.get_latest()
    return {
        "devices": [
            {**d, "latest": latest.get(d["label"], {})}
            for d in devs
        ]
    }


@get("/api/devices/{label:str}/latest")
async def device_latest(label: str, state: State) -> dict[str, Any]:
    store: Store = state["store"]
    latest = await store.get_latest()
    if label not in latest:
        raise NotFoundException(f"unknown device {label!r}")
    return {"label": label, "latest": latest[label]}


@get("/api/devices/{label:str}/lifetime")
async def device_lifetime(label: str, state: State) -> dict[str, Any]:
    """Coulomb-counted lifetime Ah in/out + equivalent cycle count."""
    store: Store = state["store"]
    return await store.battery_lifetime_stats(label)


@get("/api/load_heatmap")
async def load_heatmap(state: State, days: int = 30) -> dict[str, Any]:
    """Hour-of-day × day-of-week mean discharge wattage over the last N days."""
    store: Store = state["store"]
    now = int(time.time())
    since = now - days * 86400
    return await store.load_heatmap(since, now)


@get("/api/devices/{label:str}/history")
async def device_history(
    label: str,
    state: State,
    metric: str,
    since: int | None = None,
    until: int | None = None,
    bucket: int | None = None,
) -> dict[str, Any]:
    store: Store = state["store"]
    now = int(time.time())
    since = since if since is not None else now - 24 * 3600
    until = until if until is not None else now
    payload = await store.get_history(label, metric, since, until, bucket_seconds=bucket)
    return {
        "label": label,
        "metric": metric,
        "since": since,
        "until": until,
        "bucket_seconds": bucket,
        **payload,
        # legacy alias for existing callers
        "count": payload["stats"]["count"],
    }


@get("/api/system/logs")
async def system_logs(n: int = 200) -> dict[str, Any]:
    """Return the most recent N log lines for Settings → Diagnostics.
    Backed by an in-memory ring buffer (`solar_monitor.diagnostics`) so
    we don't depend on journalctl or a log-file path."""
    from ..diagnostics import LOG_RING
    lines = LOG_RING.lines()
    if n > 0:
        lines = lines[-n:]
    return {"lines": lines}


@post("/api/system/restart", status_code=202)
async def restart_daemon(state: State) -> dict[str, Any]:
    """Gracefully restart the daemon by re-exec()-ing the current
    process. The HTTP response goes out before exec replaces us, so the
    SPA sees a clean 202 → starts polling /api/health → notices the
    daemon is back ~5 s later.

    Before exec we shut the scheduler down properly — that disconnects
    the BLE transports cleanly so BlueZ doesn't hold a phantom
    connection to the BT-2 across the process swap (otherwise the new
    process gets "device not advertising" for ~60 s and the dashboard
    fills up with "device errors on last poll" warnings).

    Works whether or not a supervisor (systemd) is around — os.execv
    replaces the current process image in place, no orphan-process
    cleanup required."""
    scheduler: PollScheduler = state["scheduler"]

    async def _delayed_exec() -> None:
        # Let the HTTP response flush back to the client first.
        await asyncio.sleep(0.4)
        try:
            await scheduler.stop()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.create_task(_delayed_exec())
    return {"ok": True, "message": "restart scheduled"}


@get("/api/alerts")
async def list_alerts(state: State) -> dict[str, Any]:
    """Rules + transports state for the Settings → Alerts panel.
    Returns each rule's metric/op/threshold/cooldown + when it last
    fired and what the last value was.

    Transport list is rebuilt from the current Config (not the engine's
    boot-time snapshot) so transports added via the admin endpoints
    show up immediately — flagged `alive: false` until the daemon
    restarts and picks them up.
    """
    scheduler: PollScheduler = state["scheduler"]
    config: Config = state["config"]
    snap = scheduler._alerts.snapshot_state()
    live_ids = set(scheduler._alerts.transport_ids)

    def _sanitise(cfg: dict) -> dict:
        sensitive = {"password", "secret", "token", "api_key"}
        return {
            k: ("****" if k.lower() in sensitive else v)
            for k, v in cfg.items()
        }

    snap["transports"] = [
        {
            "id": t.get("id"),
            "type": t.get("type"),
            "alive": t.get("id") in live_ids,
            "config": _sanitise({k: v for k, v in t.items() if k not in ("id", "type")}),
        }
        for t in config.notification_transports
    ]
    return snap


@post("/api/alerts/{rule_id:str}/test")
async def test_alert(rule_id: str, state: State) -> dict[str, Any]:
    """Force a rule to fire via all its configured transports, so the
    user can confirm ntfy/Discord/etc. are wired up before something
    real goes wrong."""
    scheduler: PollScheduler = state["scheduler"]
    event = await scheduler._alerts.test_fire(rule_id)
    if event is None:
        raise NotFoundException(f"unknown alert rule {rule_id!r}")
    return {
        "ok": True,
        "rule_id": event.rule_id,
        "transports": next(
            (r.transports for r in scheduler._alerts.rules if r.id == rule_id),
            [],
        ),
    }


@get("/api/stream")
async def stream(state: State) -> Stream:
    """Server-Sent Events: pushes a full snapshot to the SPA on connect,
    then again after every successful poll cycle. The client opens an
    EventSource and replaces its 5s polling tick with this stream.

    Snapshot payload is the union of /api/devices + /api/poll_run +
    /api/today so we hand the SPA everything it currently fetches in
    three round-trips, in one frame."""
    scheduler: PollScheduler = state["scheduler"]

    async def gen() -> AsyncIterator[bytes]:
        q = scheduler.subscribe()
        try:
            # Initial frame so the page never sees a blank dashboard.
            try:
                first = await scheduler.build_snapshot()
                yield f"data: {json.dumps(first)}\n\n".encode("utf-8")
            except Exception:
                # Don't tear down the stream just because the first snapshot
                # failed — keep the connection alive and let the next poll
                # fire the first real event.
                pass

            # Stream subsequent broadcasts. A 25s heartbeat keeps the
            # connection warm through reverse proxies (Cloudflare, nginx)
            # that idle-close silent connections.
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            scheduler.unsubscribe(q)

    return Stream(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@get("/api/devices/{label:str}/history.csv")
async def device_history_csv(
    label: str,
    state: State,
    metric: str,
    since: int | None = None,
    until: int | None = None,
    bucket: int | None = None,
) -> Stream:
    """Same args as device_history but emits CSV that Excel / Numbers /
    Pandas all parse cleanly. ISO-8601 timestamps with a numeric epoch
    column alongside for users who want to do arithmetic on it. min /
    max columns appear when the underlying rollup has them."""
    store: Store = state["store"]
    now = int(time.time())
    since = since if since is not None else now - 24 * 3600
    until = until if until is not None else now
    payload = await store.get_history(label, metric, since, until, bucket_seconds=bucket)

    ts_list = payload.get("ts") or []
    values  = payload.get("values") or []
    mins    = payload.get("min") or []
    maxs    = payload.get("max") or []
    has_band = len(mins) == len(ts_list) and len(maxs) == len(ts_list) and ts_list

    def _csv_cell(v: Any) -> str:
        if v is None:
            return ""
        return str(v)

    async def gen() -> AsyncIterator[bytes]:
        header = "timestamp,epoch,value"
        if has_band:
            header += ",min,max"
        yield (header + "\n").encode("utf-8")
        for i, t in enumerate(ts_list):
            try:
                iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(int(t)))
            except Exception:
                iso = ""
            row = f"{iso},{int(t)},{_csv_cell(values[i] if i < len(values) else None)}"
            if has_band:
                row += f",{_csv_cell(mins[i] if i < len(mins) else None)}"
                row += f",{_csv_cell(maxs[i] if i < len(maxs) else None)}"
            yield (row + "\n").encode("utf-8")

    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    safe_metric = "".join(c if c.isalnum() or c in "-_" else "_" for c in metric)
    filename = f"{safe_label}_{safe_metric}_{since}_{until}.csv"
    return Stream(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@get("/", sync_to_thread=False)
def index() -> File:
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing — was the package built correctly?")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


@get("/sw.js", sync_to_thread=False)
def service_worker() -> File:
    """Service worker must be served from the path it claims scope over
    — at /sw.js it can intercept the whole site, at /web/sw.js it could
    only see /web/* requests. So we mirror the file at the root.
    Cache-Control: no-cache so a deployed SW update propagates next
    refresh instead of being held by the browser for a week."""
    path = _web_dir() / "sw.js"
    return File(
        path=path, media_type="application/javascript",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-cache"},
    )


@get("/manifest.webmanifest", sync_to_thread=False)
def manifest() -> File:
    """Mirror the manifest at root too so PWA install prompts find it
    without the /web/ prefix (some Android Chrome versions are picky)."""
    path = _web_dir() / "manifest.webmanifest"
    return File(
        path=path,
        media_type="application/manifest+json",
        content_disposition_type="inline",
    )


def build_app(
    config: Config,
    db_path: str,
    interval_seconds: int = 60,
    config_path: str = "config.yaml",
) -> Litestar:
    store = Store(db_path)
    scheduler = PollScheduler(config, store, interval_seconds=interval_seconds)

    async def on_startup(app: Litestar) -> None:
        # uvicorn finishes its own logging dictConfig before this hook
        # fires, so attaching our ring buffer here means it survives the
        # reset and starts capturing daemon output.
        from ..diagnostics import install as install_log_ring
        install_log_ring()
        await store.open()
        await scheduler.start()
        app.state["store"] = store
        app.state["scheduler"] = scheduler
        app.state["config"] = config
        app.state["config_path"] = config_path

    async def on_shutdown(app: Litestar) -> None:
        await scheduler.stop()
        await store.close()

    web_dir = _web_dir()
    # Make /web/* serve our static assets (uPlot, JS, CSS). The root path
    # is handled by the index() route above.
    static_router = create_static_files_router(
        path="/web",
        directories=[web_dir],
        html_mode=False,
    )

    return Litestar(
        route_handlers=[
            health,
            last_poll_run,
            today,
            list_devices,
            device_latest,
            device_history,
            device_history_csv,
            device_lifetime,
            load_heatmap,
            stream,
            list_alerts,
            test_alert,
            restart_daemon,
            system_logs,
            system_info,
            tailscale_status,
            tailscale_up,
            tailscale_down,
            tailscale_serve,
            service_worker,
            manifest,
            create_rule,
            update_rule,
            delete_rule,
            create_transport,
            update_transport,
            delete_transport,
            list_setup_transports,
            known_devices,
            probe,
            add_device,
            index,
            static_router,
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        cors_config=CORSConfig(allow_origins=["*"]),
        debug=False,
    )
