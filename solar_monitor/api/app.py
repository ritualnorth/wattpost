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
import time
from pathlib import Path
from typing import Any, AsyncIterator

from litestar import Litestar, get
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import File, Stream
from litestar.static_files import create_static_files_router

from ..config import Config
from ..scheduler import PollScheduler
from ..storage import Store


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


@get("/", sync_to_thread=False)
def index() -> File:
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing — was the package built correctly?")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


def build_app(config: Config, db_path: str, interval_seconds: int = 60) -> Litestar:
    store = Store(db_path)
    scheduler = PollScheduler(config, store, interval_seconds=interval_seconds)

    async def on_startup(app: Litestar) -> None:
        await store.open()
        await scheduler.start()
        app.state["store"] = store
        app.state["scheduler"] = scheduler
        app.state["config"] = config

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
            device_lifetime,
            load_heatmap,
            stream,
            index,
            static_router,
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        cors_config=CORSConfig(allow_origins=["*"]),
        debug=False,
    )
