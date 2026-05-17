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

from litestar import Litestar, Request, Response, get, post
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import File, Stream
from litestar.static_files import create_static_files_router

from ..config import Config
from ..scheduler import PollScheduler
from ..storage import Store
from .setup import (
    ble_status,
    ble_scan,
    usb_scan,
    add_transport,
    delete_setup_transport,
    list_setup_transports,
    known_devices,
    probe,
    add_device,
    delete_device,
)
from .alerts_admin import (
    create_rule, update_rule, delete_rule,
    create_transport, update_transport, delete_transport,
    update_quiet_hours,
)
from .forecast_admin import (
    get_pv_forecast, get_forecast_config, update_forecast_config,
    test_forecast_fetch, get_forecast_accuracy,
)
from .weather_admin import (
    get_current_weather, get_weather_config, update_weather_config,
    test_weather_fetch,
)
from .gps_admin import get_gps_status
from .cloud_admin import (
    get_cloud_config, update_cloud_config, pair_appliance,
    unpair_appliance, trigger_heartbeat,
)
from .exporters_admin import (
    get_mqtt_config, update_mqtt_config, test_mqtt,
)
from .outputs import (
    list_outputs,
    toggle_output,
    confirm_output_safety,
    list_output_schedules,
    create_output_schedule,
    update_output_schedule,
    delete_output_schedule,
)
from .system import (
    auth_status, diagnostics_bundle, rotate_kiosk_token,
    system_info, tailscale_status, tailscale_up, tailscale_down,
    tailscale_serve, update_state, update_check_now, update_apply, update_log,
    release_changelog, appliance_branding, rotate_web_password,
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


@get("/api/runtime-forecast")
async def runtime_forecast(state: State) -> dict[str, Any]:
    """Battery runtime prediction (#99).

    Two layers:

      * **Naive**: rolling 1-hour avg load → hours to SoC 10% at
        present rate. Stable; survives a transient kettle.
      * **Forecast-aware**: integrate next 48h of forecast PV
        against the avg load and walk the bank's SoC down each
        hour. Reports either an absolute depletion timestamp OR a
        "reserve days" number if the forecast says you stay above
        10% indefinitely.

    The frontend renders both lines under the Hero tile's "until
    empty" so customers see both the worst-case (naive) and the
    expected (forecast)."""
    store: Store = state["store"]

    # Pull current bank state from the latest table.
    latest = await store.get_latest()
    bank = latest.get("bank") or {}
    soc_pct = bank.get("soc_pct")
    cap_ah = bank.get("capacity_ah")
    voltage = bank.get("voltage_v") or 12.8
    if not isinstance(soc_pct, (int, float)) or not isinstance(cap_ah, (int, float)) or cap_ah <= 0:
        return {"ok": False, "reason": "no_bank"}

    bank_wh = float(cap_ah) * float(voltage)
    reserve_pct = 10.0  # don't predict past 10% SoC — LFP wants headroom
    usable_wh = bank_wh * max(0.0, float(soc_pct) - reserve_pct) / 100.0

    # Rolling 1-hour average load (negative when discharging).
    avg_w = await store.rolling_load_avg(3600)
    naive: dict[str, Any] = {"avg_load_w": None, "hours_to_empty": None}
    if avg_w is not None:
        naive["avg_load_w"] = round(avg_w, 1)
        if avg_w < -5:  # discharging at >5 W
            naive["hours_to_empty"] = round(usable_wh / abs(avg_w), 2)
        elif avg_w > 5:
            # Charging — would never empty at this rate
            naive["status"] = "charging"
        else:
            naive["status"] = "idle"

    # Forecast-aware: try to load the cached forecast and walk hourly.
    forecast_result: dict[str, Any] = {"available": False}
    try:
        cached = await store.kv_get("forecast:pv")
        if cached is not None:
            body, _ = cached
            payload = json.loads(body)
            points = payload.get("points") or []
            # points: [{"ts": unix, "watts": float}, ...] hourly
            # We only care about future points.
            now_ts = int(time.time())
            future = [(int(p["ts"]), float(p.get("pv_w") or 0))
                      for p in points if int(p.get("ts", 0)) > now_ts]
            future.sort()
            if future and avg_w is not None and avg_w < -5:
                # Walk hourly: net = pv_w - |load_w|.
                # Stop when SoC hits reserve_pct.
                soc_wh = usable_wh
                load_w = abs(avg_w)
                depletion_ts = None
                survived_horizon = True
                prev_ts = now_ts
                for ts, pv_w in future:
                    dt_h = (ts - prev_ts) / 3600.0
                    net_w = float(pv_w) - load_w
                    soc_wh += net_w * dt_h
                    if soc_wh <= 0:
                        # Linear interpolate within this hour.
                        # frac = (current_soc_before_step) / (net_w * dt_h * -1)
                        prev_soc = soc_wh - net_w * dt_h
                        if net_w * dt_h < 0:
                            frac = prev_soc / (-net_w * dt_h)
                            depletion_ts = int(prev_ts + frac * (ts - prev_ts))
                        else:
                            depletion_ts = ts
                        survived_horizon = False
                        break
                    prev_ts = ts
                forecast_result = {
                    "available": True,
                    "horizon_hours": round((future[-1][0] - now_ts) / 3600.0, 1),
                    "depletion_ts":  depletion_ts,
                    "hours_to_empty": (
                        round((depletion_ts - now_ts) / 3600.0, 1)
                        if depletion_ts else None
                    ),
                    "reserves_indefinite": survived_horizon,
                }
    except Exception:
        # Forecast lookup is best-effort; never fail the endpoint.
        # Forecast walk failures are non-fatal; swallow.
        pass

    return {
        "ok": True,
        "now": {
            "soc_pct":   round(float(soc_pct), 1),
            "capacity_ah": round(float(cap_ah), 1),
            "voltage_v": round(float(voltage), 2),
            "usable_wh": round(usable_wh, 1),
        },
        "naive": naive,
        "forecast": forecast_result,
    }


@get("/api/battery-health")
async def battery_health(state: State, days: int = 30) -> dict[str, Any]:
    """SoC residency histogram + cycle/lifetime numbers for the Battery
    Health tile (#109).

    Default window is 30 days — long enough to surface a real residency
    pattern, short enough to stay responsive on the 1-min/1-hour rollup
    tables. Caller can override via ?days=N (clamped 1-365)."""
    store: Store = state["store"]
    days = max(1, min(365, int(days)))
    now = int(time.time())
    return await store.battery_health_aggregate(now - days * 86400, now)


@get("/api/poll_run")
async def last_poll_run(state: State) -> dict[str, Any]:
    """Header status pill data source. Includes BLE-side health so the
    pill can flip to "no BLE" / "setup needed" without a separate
    polling loop in the UI."""
    store: Store = state["store"]
    scheduler: PollScheduler = state["scheduler"]
    config: Config = state["config"]
    # Count configured vs. open transports. Anything with a live
    # BleakClient that's_connected counts as open. Cheap; runs in
    # the scheduler's process so no DBus calls here.
    configured_transports = len(config.transports)
    open_transports = 0
    for tcfg in config.transports:
        tid = tcfg.get("id")
        t = scheduler.get_transport(tid) if tid else None
        client = getattr(t, "_client", None) if t else None
        if client and getattr(client, "is_connected", False):
            open_transports += 1
    return {
        "last_run":          await store.last_poll_run(),
        "scheduler_running": scheduler._task is not None and not scheduler._task.done(),
        "transports": {
            "configured": configured_transports,
            "open":       open_transports,
        },
    }


@get("/api/devices")
async def list_devices(state: State) -> dict[str, Any]:
    store: Store = state["store"]
    devs = await store.list_devices()
    latest = await store.get_latest()
    # Join in the transport id from the live config so the dashboard
    # can wire per-device delete buttons (which need transport +
    # slave_id to address the row). device_meta doesn't carry it,
    # so we look it up by (slave_id, label) match against config.
    config = state.get("config")
    by_label = {}
    if config is not None:
        for cfg_dev in getattr(config, "devices", []) or []:
            label = cfg_dev.get("label") if isinstance(cfg_dev, dict) else getattr(cfg_dev, "label", None)
            if label:
                by_label[label] = (cfg_dev.get("transport") if isinstance(cfg_dev, dict)
                                   else getattr(cfg_dev, "transport", None))
    return {
        "devices": [
            {**d,
             "transport": by_label.get(d["label"]),
             "latest": latest.get(d["label"], {})}
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


@get("/api/devices/{label:str}/efficiency")
async def device_efficiency(label: str, state: State) -> dict[str, Any]:
    """SoC-corrected charge efficiency for one battery pack over a
    range of trailing windows. The shorter windows surface early — a
    7d efficiency drop tells you something's going wrong before the
    lifetime number catches up. The `reliable` flag on each window
    tracks whether the pack saw enough throughput for the number to
    be trustworthy."""
    store: Store = state["store"]
    now = int(time.time())
    windows = {
        "7d":   now - 7  * 86400,
        "30d":  now - 30 * 86400,
        "90d":  now - 90 * 86400,
    }
    out: dict[str, Any] = {"device": label, "windows": {}}
    for name, since in windows.items():
        out["windows"][name] = await store.battery_efficiency(label, since_ts=since)
    out["windows"]["lifetime"] = await store.battery_efficiency(label, since_ts=None)
    return out


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
        sensitive = {"password", "secret", "token", "api_key",
                     "app_token", "user_key"}
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
    qh = config.quiet_hours
    snap["quiet_hours"] = (
        {"start_hour": qh.start_hour, "end_hour": qh.end_hour}
        if qh is not None else None
    )
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


@get("/kiosk", sync_to_thread=False)
def kiosk_index() -> File:
    """Public, chrome-free wall-display view of the SoC + flow.

    Serves the same SPA bundle as `/`; the boot script in app.js
    detects `location.pathname === '/kiosk'` and flips the SPA into
    kiosk mode before first render. Real server-side path (rather
    than the previous `/#/kiosk` hash route) so the local-auth
    middleware can whitelist this single URL without exposing the
    rest of the dashboard."""
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


@get("/login", sync_to_thread=False)
def login_page(request: Request) -> File | Response:
    """Static HTML login form. POSTs to /api/login → cookie + redirect.

    Special case: when the request reached us via the Cloudflare
    Tunnel (CF-Ray header present), the password form is a dead
    end — local-password sessions don't grant tunnel access by
    design (#137). Serve a different page that explains "sign in
    via wattpost.cloud and click Open" rather than letting the
    user fill in a password that issues a session their next click
    will 401 against."""
    from .. import web_auth as _wa
    if _wa.is_tunnel_origin(request.scope):
        page = _web_dir() / "login-tunnel.html"
        if page.exists():
            return File(path=page, media_type="text/html",
                        content_disposition_type="inline")
        # Fallback: plain text if the template is missing.
        return Response(
            content=(
                "<!doctype html><meta charset=utf-8>"
                "<title>Sign in via wattpost.cloud</title>"
                "<body style=\"font:14px system-ui;max-width:420px;"
                "margin:6rem auto;padding:0 1rem;color:#cdd6e0\">"
                "<h1>Direct tunnel access isn't supported</h1>"
                "<p>Sign in at <a href=\"https://wattpost.cloud\">"
                "wattpost.cloud</a> and click <b>Open</b> on this "
                "appliance to access it remotely.</p>"
                "</body>"
            ),
            media_type="text/html",
        )
    path = _web_dir() / "login.html"
    if not path.exists():
        raise NotFoundException("login.html missing")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


@get("/sso", sync_to_thread=False)
def sso_redirect(request: Request, state: State, token: str = "") -> Response:
    """Cloud→appliance SSO landing (#137). The cloud's dashboard
    mints a short-lived HMAC-signed token bound to (user, appliance,
    exp=60s) and redirects the user here. We verify the signature
    against the per-appliance `sso_secret` exchanged at pair time,
    issue a session cookie tagged origin=sso, and bounce to /.

    Failures fall through to /login so a stale link doesn't dead-end
    the user."""
    from .. import web_auth as _wa
    config: Config = state["config"]
    sso_secret = (config.cloud.sso_secret if config.cloud else "") or ""
    if not sso_secret:
        # Appliance hasn't heartbeated post-update yet; no key to
        # verify against. Send the user to /login as a fallback.
        return Response(
            content="",
            status_code=302,
            headers={"Location": "/login?next=/&sso_unavail=1"},
        )
    payload = _wa.consume_sso_token(token, sso_secret)
    if payload is None:
        return Response(
            content="",
            status_code=302,
            headers={"Location": "/login?next=/&sso_failed=1"},
        )
    session = _wa.issue_session(origin="sso")
    resp = Response(content="", status_code=302, headers={"Location": "/"})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME,
        value=session,
        max_age=_wa.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        # Tunnel is HTTPS-only at the CF edge; secure cookies survive
        # the round-trip back through cloudflared to the appliance.
        secure=True,
    )
    return resp


@post("/api/login", status_code=200)
async def do_login(data: dict, request: Request) -> Response:
    """Verify the supplied password, drop a session cookie. Returns
    the URL the caller should redirect to (the `next` query param if
    safe, else `/`). Demo-mode + no-password installs short-circuit
    above this in the middleware, so we don't have to handle them.

    Refuses tunnel-origin requests. Local-password sessions aren't
    valid via tunnel anyway (the middleware rejects them); accepting
    the password here would issue a useless session and confuse the
    user. The /login page itself shows a tunnel-specific message
    when it detects tunnel origin, so this is belt-and-braces."""
    from .. import web_auth as _wa
    if _wa.is_tunnel_origin(request.scope):
        return Response(
            {"ok": False,
             "detail": "direct tunnel sign-in not supported — "
                       "use wattpost.cloud and click Open"},
            status_code=403,
        )
    pw = (data or {}).get("password") or ""
    if not _wa.verify_password(pw):
        return Response({"ok": False, "detail": "wrong password"}, status_code=401)
    token = _wa.issue_session()
    # Validate `next` to only allow same-origin relative paths.
    nxt = (data or {}).get("next") or "/"
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = Response({"ok": True, "redirect": nxt})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME,
        value=token,
        max_age=_wa.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # appliance is HTTP on the LAN; cloud tunnel is HTTPS but the cookie is the local one
    )
    return resp


@post("/api/logout", status_code=200, sync_to_thread=False)
def do_logout(request: Request) -> Response:
    from .. import web_auth as _wa
    token = request.cookies.get(_wa.SESSION_COOKIE_NAME)
    if token:
        _wa.revoke_session(token)
    resp = Response({"ok": True})
    resp.delete_cookie(key=_wa.SESSION_COOKIE_NAME, path="/")
    return resp


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
    # Stash config_path on the scheduler too so the CloudService it
    # owns can persist mutations (sso_secret from heartbeat) without
    # needing access to app.state.
    scheduler.config_path = config_path

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

    # Demo-mode read-only middleware. When WATTPOST_DEMO=1 we 403 any
    # state-changing HTTP method except a small allowlist of paths the
    # dashboard needs to function (none currently — the local
    # dashboard's POSTs are all writes, so this is empty). Pure ASGI
    # middleware so we can match by request scope without going through
    # Litestar's route system.
    import os as _os
    _DEMO = _os.environ.get("WATTPOST_DEMO") == "1"

    class _ReadOnlyDemoMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or not _DEMO:
                await self.app(scope, receive, send)
                return
            method = scope.get("method", "GET").upper()
            if method in ("GET", "HEAD", "OPTIONS"):
                await self.app(scope, receive, send)
                return
            # Reject every write with a clear 403. The dashboard catches
            # this and shows a "read-only demo" toast.
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"detail":"This is a read-only demo. Buy a Pi to track real batteries."}',
            })

    # Local-auth middleware. Trust model in solar_monitor/web_auth.py:
    #   - demo mode: bypass entirely
    #   - no password set on the appliance: bypass (first-boot grace,
    #     or self-hosted dev who skipped install.sh)
    #   - source IP is loopback: trusted (the cloud tunnel proxies via
    #     localhost; LAN clients can't fake that)
    #   - anonymous paths (/kiosk, /web/static, /login, /api/login,
    #     /api/heartbeat): always allowed
    #   - GET on /api/* read endpoints: allowed when "read-only public"
    #     mode is on (default for v0 so kiosks-on-wifi keep working
    #     unchanged); the toggle moves to Settings → System later
    #   - everything else: needs a valid wp_local_session cookie
    #     → 401 (for /api/*) or redirect to /login (for HTML routes)
    from .. import web_auth as _web_auth
    # Allow GETs on any path while we ship the strict default. This
    # gives existing installs a soft landing — the password is set
    # silently, the UI keeps loading, and writes start prompting for
    # login. Operator can flip strict mode in Settings later.
    _READONLY_PUBLIC = True

    class _LocalAuthMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return
            # Bypass: demo mode (public read-only by design) OR real
            # loopback (curl from the Pi, SSH port-forward, daemon
            # talking to itself). is_loopback_source returns False
            # for tunnel-origin requests — see the CF-header sniff
            # in web_auth.is_loopback_source.
            #
            # "no password set" is NO LONGER a bypass condition. Until
            # 0.0.37 it was, on the assumption install.sh always ran;
            # Docker installs never ran install.sh and so were left
            # wide open. ensure_first_boot_password() now generates
            # one on every startup, so password_is_set() should always
            # be True by the time we accept requests. If it ever
            # isn't (permission error writing the hash file), we
            # fail-closed below.
            if _DEMO or _web_auth.is_loopback_source(scope):
                await self.app(scope, receive, send)
                return
            # Kiosk-token bypass (#kiosk-share). The public share URL
            # is `<slug>.wattpost.cloud/kiosk?key=<token>`. When the
            # ?key matches config.cloud.kiosk_token we let GET / HEAD
            # / OPTIONS requests through anonymously for the kiosk
            # page itself + the small set of data endpoints the kiosk
            # JS calls. Strict allow-list of paths to keep this from
            # being a back door for the whole API. The kiosk JS
            # appends ?key= to every fetch (see app.js wireKioskMode).
            _path = scope.get("path", "/")
            _method = scope.get("method", "GET").upper()
            _kiosk_paths = ("/kiosk", "/api/devices", "/api/poll_run",
                            "/api/today", "/api/bank/current", "/api/weather",
                            "/web/", "/static/")
            if _method in ("GET", "HEAD", "OPTIONS") and (
                _path == "/kiosk" or any(_path.startswith(p) for p in _kiosk_paths)
            ):
                _kiosk_tok = (config.cloud.kiosk_token if config.cloud else "") or ""
                if _kiosk_tok:
                    qs = scope.get("query_string", b"") or b""
                    # Tiny parse — querystring is short, fine to scan.
                    supplied = None
                    for part in qs.split(b"&"):
                        if part.startswith(b"key="):
                            supplied = part[4:].decode("ascii", errors="ignore")
                            break
                    import hmac as _hmac_mod
                    if supplied and _hmac_mod.compare_digest(supplied, _kiosk_tok):
                        await self.app(scope, receive, send)
                        return
            # Cloud broker (#139). When the cloud proxies a logged-in
            # user's request through to this appliance, it stamps the
            # request with X-WP-Broker-Auth = <ts>.<hmac> signed with
            # the per-appliance sso_secret. We verify against our
            # local copy of sso_secret. Valid header = the cloud has
            # already authenticated this user; we trust the request
            # exactly as if it had a valid SSO session. No session
            # cookie is issued — broker traffic is stateless per
            # request.
            broker_header: bytes | None = None
            for k, v in scope.get("headers", []):
                if k == b"x-wp-broker-auth":
                    broker_header = v
                    break
            if broker_header is not None:
                # `config` is captured by closure from build_app's
                # arguments. Its `.cloud` is mutated in place by the
                # heartbeat service when the cloud sends an sso_secret,
                # so this lookup always reflects the current value.
                _sso = (
                    config.cloud.sso_secret if config.cloud else ""
                ) or ""
                if _sso and _web_auth.verify_broker_auth(
                    broker_header.decode("latin-1", errors="ignore"), _sso,
                ):
                    await self.app(scope, receive, send)
                    return
            # No password file = misconfigured install. Fail-closed
            # to the login page (which itself surfaces a "password
            # not configured" message), NEVER let the request through.
            if not _web_auth.password_is_set():
                # /login + /api/login + static assets are always
                # allowed — the user needs SOMETHING to render.
                _p = scope.get("path", "/")
                if not _web_auth.is_anonymous_path(_p):
                    if _p.startswith("/api/"):
                        body = (b'{"detail":"local password not configured -- '
                                b'check /etc/wattpost/web-password.hash"}')
                        await send({
                            "type": "http.response.start", "status": 503,
                            "headers": [(b"content-type", b"application/json")],
                        })
                        await send({"type": "http.response.body", "body": body})
                    else:
                        await send({
                            "type": "http.response.start", "status": 302,
                            "headers": [(b"location", b"/login")],
                        })
                        await send({"type": "http.response.body", "body": b""})
                    return
                await self.app(scope, receive, send)
                return
            path = scope.get("path", "/")
            if _web_auth.is_anonymous_path(path):
                await self.app(scope, receive, send)
                return
            method = scope.get("method", "GET").upper()
            # READONLY_PUBLIC bypass: allow GET reads on LAN without a
            # session, so kiosks-on-wifi keep working unchanged. Does
            # NOT apply to tunnel-origin requests — a leaked tunnel
            # URL would otherwise leak every metric on the appliance
            # to anonymous viewers.
            tunnel = _web_auth.is_tunnel_origin(scope)
            if _READONLY_PUBLIC and method in ("GET", "HEAD", "OPTIONS") \
                    and not tunnel:
                await self.app(scope, receive, send)
                return
            # Look up the session cookie.
            cookie_header = b""
            for k, v in scope.get("headers", []):
                if k == b"cookie":
                    cookie_header = v
                    break
            token = None
            for part in cookie_header.decode("latin-1").split(";"):
                part = part.strip()
                if part.startswith(_web_auth.SESSION_COOKIE_NAME + "="):
                    token = part.split("=", 1)[1]
                    break
            # Tunnel-origin requests require an SSO-issued session;
            # local-password sessions only grant LAN access. Keeps the
            # local password as a fallback while making cloud-login
            # the actual perimeter for internet-facing traffic.
            if tunnel:
                ok = _web_auth.is_session_valid_for_tunnel(token)
            else:
                ok = _web_auth.is_session_valid(token)
            if ok:
                await self.app(scope, receive, send)
                return
            # Reject.
            if path.startswith("/api/"):
                body = b'{"detail":"login required","login_url":"/login"}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({"type": "http.response.body", "body": body})
            else:
                # HTML route → 302 to the login page, preserving the
                # original URL so we can bounce back after login.
                qs = scope.get("query_string", b"").decode("latin-1")
                full = path + (("?" + qs) if qs else "")
                loc = f"/login?next={full}".encode("latin-1")
                await send({
                    "type": "http.response.start",
                    "status": 302,
                    "headers": [(b"location", loc)],
                })
                await send({"type": "http.response.body", "body": b""})

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
            device_efficiency,
            load_heatmap,
            stream,
            list_alerts,
            test_alert,
            restart_daemon,
            system_logs,
            system_info,
            auth_status,
            diagnostics_bundle,
            rotate_kiosk_token,
            update_state,
            update_check_now,
            rotate_web_password,
            update_apply,
            update_log,
            release_changelog,
            appliance_branding,
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
            update_quiet_hours,
            get_pv_forecast,
            get_forecast_config,
            update_forecast_config,
            test_forecast_fetch,
            get_forecast_accuracy,
            get_current_weather,
            get_gps_status,
            get_weather_config,
            update_weather_config,
            test_weather_fetch,
            get_cloud_config,
            update_cloud_config,
            pair_appliance,
            unpair_appliance,
            trigger_heartbeat,
            get_mqtt_config,
            update_mqtt_config,
            test_mqtt,
            ble_status,
            ble_scan,
            usb_scan,
            add_transport,
            delete_setup_transport,
            list_setup_transports,
            known_devices,
            probe,
            add_device,
            delete_device,
            list_outputs,
            toggle_output,
            confirm_output_safety,
            list_output_schedules,
            create_output_schedule,
            update_output_schedule,
            delete_output_schedule,
            index,
            kiosk_index,
            login_page,
            do_login,
            do_logout,
            sso_redirect,
            static_router,
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        # Middleware order matters: read-only-demo first so demo
        # writes 403 early; local-auth second so non-demo installs
        # get login-gated. Both no-op when their condition isn't
        # met.
        middleware=(
            ([_ReadOnlyDemoMiddleware] if _DEMO else []) + [_LocalAuthMiddleware]
        ),
        cors_config=CORSConfig(allow_origins=["*"]),
        debug=False,
    )
