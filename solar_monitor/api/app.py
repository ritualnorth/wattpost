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

from litestar import Litestar, Request, Response, get, patch, post
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException
from litestar.response import File, Stream
from litestar.static_files import create_static_files_router

from ..config import Config
from ..scheduler import PollScheduler
from ..storage import Store
from .setup import (
    ble_status,
    discovered,
    ble_scan,
    ble_diagnose,
    usb_scan,
    hid_scan,
    add_transport,
    edit_setup_transport,
    delete_setup_transport,
    list_setup_transports,
    known_devices,
    probe,
    add_device,
    delete_device,
    delete_device_by_label,
)
from .alerts_admin import (
    create_rule, update_rule, delete_rule,
    create_transport, update_transport, delete_transport,
    update_quiet_hours,
)
from .forecast_admin import (
    get_pv_forecast, get_forecast_config, update_forecast_config,
    test_forecast_fetch, get_forecast_accuracy, forecast_config_view,
)
from .weather_admin import (
    get_current_weather, get_weather_config, update_weather_config,
    test_weather_fetch, weather_config_view,
)
from .weather_history import weather_history
from .gps_admin import get_gps_status, get_gps_config, update_gps_config
from .mqtt_in_admin import get_mqtt_in_status
from .hotspot_admin import (
    get_hotspot_status, update_hotspot_config, hotspot_on, hotspot_off,
    wifi_scan, wifi_join, wifi_scan_ap_bounce, wifi_scan_ap_bounce_status,
    network_status, network_set_ipv4,
)
from .captive import (
    android_204, apple_detect, win_connecttest, win_ncsi, ubuntu_canonical,
)
from .location_admin import get_location_status, update_location_share
from .kiosk_admin import get_kiosk_config, update_kiosk_config
from .energy import energy_today
from .cloud_admin import (
    get_cloud_config, update_cloud_config, pair_appliance,
    unpair_appliance, trigger_heartbeat, cloud_config_view,
)
from .exporters_admin import (
    get_mqtt_config, update_mqtt_config, test_mqtt, mqtt_config_view,
    get_prometheus_config, update_prometheus_config, prometheus_config_view,
)
from .outputs import (
    list_outputs,
    toggle_output,
    confirm_output_safety,
    list_output_schedules,
    create_output_schedule,
    update_output_schedule,
    delete_output_schedule,
    get_solar_pause,
    patch_solar_pause,
)
from .system import (
    auth_status, diagnostics_bundle, system_info,
    update_state, update_check_now, set_update_channel, update_apply, update_log,
    slot_state, slot_rollback,
    release_changelog, appliance_branding, rotate_web_password,
    kiosk_tokens_list, kiosk_tokens_create, kiosk_tokens_revoke,
    netsec_status, netsec_set_firewall, netsec_set_ssh,
    get_history_settings, patch_history_settings, device_health, host_history,
    reset_to_defaults,
)
from .backup import (
    export_backup, import_backup,
    backup_schedule, backup_run_now, backup_download_one, backup_delete_one,
    backup_cloud_list, backup_cloud_restore, backup_cloud_toggle,
    discovery_state, discovery_toggle,
    local_telemetry_state, local_telemetry_toggle,
)
from ..backup import BackupService
from .auth_oidc import auth_callback, auth_lan_login, oidc_available

import logging
# Module logger. Several handlers (patch_device_setting, _audit, the
# settings-write path) referenced `log` without it ever being defined at
# module scope — a latent NameError that only fired on their error/log
# branches. Defining it here fixes all of them.
log = logging.getLogger(__name__)


def _web_dir() -> Path:
    # Deliberately NOT .resolve(): on the Pi the daemon runs from the
    # /opt/wattpost symlink, so __file__ is /opt/wattpost/venv/.../app.py.
    # .resolve() would canonicalise that symlink down to the concrete slot
    # the daemon imported from (/opt/wattpost-slots/<x>), pinning the web
    # directory — and the static-files router caches it at startup — to that
    # slot. After an atomic slot swap the symlink moves but the resolved path
    # doesn't, so /web/* keeps serving the OLD slot's assets until a restart
    # (stale dashboard). Leaving the symlink unresolved means the OS follows
    # the live symlink on every file open, so a swap is reflected immediately.
    # No-op on Docker/dev where there's no slot symlink in the path.
    return Path(__file__).parent.parent / "web"


@get("/api/health", sync_to_thread=False)
def health() -> dict[str, Any]:
    # The `service` field is the discrimination key for the setup
    # wizard's LAN peer scan (#184): another WattPost on the same
    # subnet that's holding a Renogy BT-2 dongle will respond here
    # with the same string, and the wizard surfaces a hint so the
    # user doesn't burn a day debugging "why won't this dongle pair".
    # Keep `service` exactly "wattpost", the scanner string-matches.
    from .. import __version__
    return {
        "ok": True,
        "ts": int(time.time()),
        "service": "wattpost",
        "version": __version__,
    }


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


@get("/api/today/soc-envelope")
async def today_soc_envelope(state: State) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.soc_envelope(state["store"])


@get("/api/system/integrations")
async def get_integrations(state: State) -> dict[str, Any]:
    """One-shot view of all four Settings → Integrations configs
    (#18). The panel used to fire four parallel requests
    (/api/{forecast,weather,cloud,exporters/mqtt}/config); with
    Promise.all one slow or failed request failed the whole panel.
    Folding them into a single round-trip removes that. Each sub-view is the same
    pure config→dict the individual endpoints still serve, so the
    shapes are identical and the per-integration GET/PUT routes stay
    for the form save/clear flows."""
    config: Config = state["config"]
    return {
        "forecast":   forecast_config_view(config),
        "weather":    weather_config_view(config),
        "cloud":      cloud_config_view(config),
        "mqtt":       mqtt_config_view(config),
        "prometheus": prometheus_config_view(config),
    }


@get("/api/runtime-forecast")
async def runtime_forecast(state: State) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.runtime_forecast(state["store"])


@get("/api/battery-health")
async def battery_health(state: State, days: int = 30) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.battery_health(state["store"], days)


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


def _join_device_transport(config, devices: list[dict]) -> list[dict]:
    """Join the config transport id onto each device dict (by label), so
    the dashboard's per-device delete button has transport + slave_id to
    address the config row. BOTH /api/devices and /api/snapshot need this
    — the device cards render off whichever arrives, and a missing
    transport blocks the delete button (#device-delete). Idempotent: a
    device that already carries a transport is left alone."""
    by_label = {}
    if config is not None:
        for cfg_dev in getattr(config, "devices", []) or []:
            label = cfg_dev.get("label") if isinstance(cfg_dev, dict) else getattr(cfg_dev, "label", None)
            if label:
                by_label[label] = (cfg_dev.get("transport") if isinstance(cfg_dev, dict)
                                   else getattr(cfg_dev, "transport", None))
    out = []
    for d in devices:
        if isinstance(d, dict) and d.get("transport") is None:
            d = {**d, "transport": by_label.get(d.get("label"))}
        out.append(d)
    return out


@get("/api/devices")
async def list_devices(state: State) -> dict[str, Any]:
    store: Store = state["store"]
    devs = await store.list_devices()
    latest = await store.get_latest()
    config = state.get("config")
    merged = [{**d, "latest": latest.get(d["label"], {})} for d in devs]
    return {"devices": _join_device_transport(config, merged)}


@post("/api/devices/{label:str}/display-name", status_code=200)
async def set_device_display_name(
    label: str, request: Request, state: State,
) -> dict[str, Any]:
    """Set or clear the user-facing display name for a device. The
    underlying device label is the immutable storage key (history,
    samples, alerts, exporters all reference it), this only changes
    what the dashboard shows. POST `{"display_name": "..."}` to set,
    or `{"display_name": ""}` (or null) to clear and fall back to the
    original label.
    """
    store: Store = state["store"]
    body = await request.json()
    name = body.get("display_name") if isinstance(body, dict) else None
    # Coerce a non-string display_name (the store does .strip() on it) so a
    # bogus {"display_name": 5} fails cleanly rather than 500ing.
    if name is not None and not isinstance(name, str):
        name = str(name)
    devs = {d["label"]: d for d in await store.list_devices()}
    if label not in devs:
        raise NotFoundException(f"unknown device {label!r}")
    await store.set_device_display_name(label, name)
    return {"label": label, "display_name": (name or "").strip() or None}


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


@get("/api/devices/{label:str}/charger-stats")
async def device_charger_stats(label: str, state: State) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.charger_stats(state["store"], label)


def _resolve_device_settings(state, label):
    """Shared resolver: returns (cfg_dev, driver_instance, settings_list)
    or raises NotFoundException. Used by both GET and PATCH so the
    two endpoints stay in lockstep about what's editable."""
    from ..vendors.registry import VENDORS
    config = state.get("config") if hasattr(state, "get") else state["config"]
    cfg_dev = None
    for d in (getattr(config, "devices", None) or []):
        if d.label == label:
            cfg_dev = d
            break
    if cfg_dev is None:
        raise NotFoundException(f"unknown device {label!r}")
    vendor_reg = VENDORS.get(cfg_dev.vendor)
    if vendor_reg is None:
        return cfg_dev, None, []
    driver_cls = vendor_reg.drivers.get(cfg_dev.kind)
    if driver_cls is None:
        return cfg_dev, None, []
    inst = driver_cls(slave_id=cfg_dev.slave_id or 0, label=label)
    return cfg_dev, inst, list(inst.writable_settings())


@get("/api/devices/{label:str}/settings")
async def device_settings(label: str, state: State) -> dict[str, Any]:
    """Per-device user-tunable settings (#111 phase 1).

    Returns the `WritableSetting` descriptors declared by the device's
    driver, paired with the current value pulled from the most recent
    poll snapshot. Edit support lands in phase 2 (PATCH endpoint +
    UI modal); right now this is read-only so the UI can render a
    "Current settings" panel without giving customers a way to brick
    their charger before the confirm-flow is in place.

    404 if the device isn't configured. Empty `items` if the driver
    doesn't declare any writable settings (default, Victron devices
    are read-only forever per product scope, JK BMS write surface is
    a future phase, only Renogy Rover exposes settings today).
    """
    cfg_dev, inst, settings = _resolve_device_settings(state, label)
    if not settings:
        return {"label": label, "items": []}

    store: Store = state["store"]
    snapshot = (await store.get_latest()).get(label, {}) or {}
    items = []
    for s in settings:
        current = snapshot.get(s.read_from) if s.read_from else None
        items.append({
            "key":        s.key,
            "label":      s.label,
            "kind":       s.kind,
            "units":      s.units,
            "choices":    [{"value": v, "label": l} for v, l in s.choices],
            "min":        s.min,
            "max":        s.max,
            "step":       s.step,
            "help_text":  s.help_text,
            "current_value": current,
            "editable":   True,
        })
    return {"label": label, "items": items}


@patch("/api/devices/{label:str}/settings/{key:str}", status_code=200)
async def patch_device_setting(
    label: str, key: str, request: Request, state: State,
) -> dict[str, Any]:
    """Apply a new value to one writable setting (#111 phase 2).

    Body: {"value": <new value>}  (number or int per descriptor.kind)

    Validates against the WritableSetting descriptor (enum choices,
    min/max for numeric), applies the descriptor's scale to encode
    the register value, then writes via FC06 with the same BT-2
    ack-swallowing fallback the Rover load-output adapter uses.

    On success returns the confirmed read-back value and the new
    user-facing value. On clamp (device returned a different value
    than we wrote) returns ok=False with the device's actual value
    so the UI can render what landed.
    """
    cfg_dev, inst, settings = _resolve_device_settings(state, label)
    setting = next((s for s in settings if s.key == key), None)
    if setting is None:
        raise NotFoundException(f"unknown setting {key!r} for device {label!r}")

    body = await request.json()
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(status_code=400, detail="missing `value` in body")
    raw_value = body["value"]

    # ---- validation by kind ----
    if setting.kind == "enum":
        try:
            v_int = int(raw_value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"setting {key!r} is enum; value must be int")
        valid = {c[0] for c in setting.choices}
        if v_int not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"value {v_int} not in valid choices {sorted(valid)}",
            )
        register_value = v_int
    elif setting.kind in ("float", "int"):
        try:
            v_num = float(raw_value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"setting {key!r} requires a number")
        if setting.min is not None and v_num < setting.min:
            raise HTTPException(
                status_code=400,
                detail=f"value {v_num} below minimum {setting.min}",
            )
        if setting.max is not None and v_num > setting.max:
            raise HTTPException(
                status_code=400,
                detail=f"value {v_num} above maximum {setting.max}",
            )
        # Encode register value via scale. With scale=0.1 (e.g. 14.4 V
        # in user-facing units), the register holds 144.
        register_value = int(round(v_num / (setting.scale or 1.0)))
        if not (0 <= register_value <= 0xFFFF):
            raise HTTPException(
                status_code=400,
                detail=f"encoded value {register_value} out of FC06 range",
            )
    else:
        raise HTTPException(
            status_code=500,
            detail=f"driver declared unknown setting kind {setting.kind!r}",
        )

    # ---- resolve transport ----
    scheduler = state.get("scheduler") if hasattr(state, "get") else state["scheduler"]
    transport = scheduler.get_transport(cfg_dev.transport) if scheduler else None
    if transport is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"transport {cfg_dev.transport!r} not running, has the "
                f"daemon finished its first poll cycle?"
            ),
        )
    if not hasattr(transport, "request"):
        # Passive transports (Victron BLE Instant Readout) can't write.
        # Drivers behind them shouldn't declare WritableSettings, but
        # if one does, refuse loudly rather than crash inside FC06.
        raise HTTPException(
            status_code=409,
            detail=(
                "this device's transport is read-only (BLE broadcast), "
                "writes aren't supported"
            ),
        )

    # ---- write + audit log ----
    from ..settings_write import write_setting_register
    slave_id = cfg_dev.slave_id or 0
    log.info(
        "[settings] write %s.%s = %r (reg=0x%04X, encoded=%d) by user",
        label, key, raw_value, setting.register, register_value,
    )
    result = await write_setting_register(
        transport, slave_id, setting.register, register_value,
    )

    # ---- update store so UI sees the new value immediately ----
    if result["ok"] and setting.read_from:
        store: Store = state["store"]
        applied_user_value = (
            result["confirmed_value"] * (setting.scale or 1.0)
            if result["confirmed_value"] is not None
            else (register_value * (setting.scale or 1.0))
        )
        # Best-effort: push the new value into `latest` so the
        # Settings panel reflects it before the next regular poll.
        try:
            await store.record_poll({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
                "devices": {label: {
                    "_vendor": cfg_dev.vendor,
                    "_kind":   cfg_dev.kind,
                    "_slave_id": slave_id,
                    setting.read_from: applied_user_value,
                }},
                "errors": [],
                "elapsed_seconds": 0.0,
            })
        except Exception:
            log.exception("[settings] post-write store update failed")

    return {
        "label":             label,
        "key":               key,
        "ok":                bool(result["ok"]),
        "register_value":    result["confirmed_value"],
        "applied_value":     (
            result["confirmed_value"] * (setting.scale or 1.0)
            if result["confirmed_value"] is not None else None
        ),
        "detail":            result["detail"],
    }


@get("/api/devices/{label:str}/efficiency")
async def device_efficiency(label: str, state: State) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.efficiency(state["store"], label)


@get("/api/load_heatmap")
async def load_heatmap(state: State, days: int = 30) -> dict[str, Any]:
    from .. import viewmodels
    return await viewmodels.load_heatmap(state["store"], days)


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

    Before exec we shut the scheduler down properly, that disconnects
    the BLE transports cleanly so BlueZ doesn't hold a phantom
    connection to the BT-2 across the process swap (otherwise the new
    process gets "device not advertising" for ~60 s and the dashboard
    fills up with "device errors on last poll" warnings).

    Works whether or not a supervisor (systemd) is around, os.execv
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
    show up immediately, flagged `alive: false` until the daemon
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
                # failed, keep the connection alive and let the next poll
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


@get("/api/snapshot")
async def snapshot(state: State) -> dict[str, Any]:
    # Frame-locked REST view: devices + poll_run + today read in one
    # atomic pass via the scheduler. Replaces three separate fetches in
    # the SPA's polling fallback so the hero and flow tiles can never
    # disagree about which poll cycle they're rendering (#162).
    scheduler: PollScheduler = state["scheduler"]
    snap = await scheduler.build_snapshot()
    # The device cards usually render off this snapshot (not /api/devices),
    # so join the transport here too — otherwise the per-device delete
    # button has no transport to address and refuses ("couldn't find its
    # transport").
    if isinstance(snap, dict) and isinstance(snap.get("devices"), list):
        snap["devices"] = _join_device_transport(state.get("config"), snap["devices"])
    return snap


@get("/metrics")
async def prometheus_metrics(state: State) -> Response:
    """Prometheus scrape endpoint (#14). Served only when a
    `prometheus` exporter is configured (404 otherwise); renders the
    latest poll result as Prometheus text exposition format for Grafana.
    Anonymous by design — scrapers carry no session, and this is the
    same read-only telemetry the LAN dashboard shows."""
    from ..export.prometheus import PrometheusExporter
    scheduler: PollScheduler = state["scheduler"]
    exp = next(
        (e for e in getattr(scheduler, "_exporters", []) or []
         if isinstance(e, PrometheusExporter)),
        None,
    )
    if exp is None:
        raise NotFoundException("prometheus exporter not enabled")
    return Response(
        content=exp.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
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
        raise NotFoundException("index.html missing, was the package built correctly?")
    # no-cache (revalidate, not no-store) so the browser always re-checks the
    # shell HTML and picks up new ?v= asset versions after a deploy. Without
    # it Safari heuristically caches the shell for hours and serves stale
    # app.js/styles.css (the recurring "stale shell"). Assets stay
    # long-cached via their ?v= busters; only the shell revalidates.
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


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
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


# Mode-aware kiosk route — pin a wall display at /kiosk/cabin (for
# example) and the SPA reads the path segment, sets `data-mode` on
# <body>, and renders the layout tuned for that persona. The validation
# is permissive: unknown modes fall back to the standard kiosk view
# rather than 404'ing, so a typo'd URL still gives the wall something
# useful to show. See [[modes-state-machine]] for the full set.
_VALID_KIOSK_MODES = frozenset({"home", "van", "cabin", "marine", "kiosk"})


@get("/kiosk/{mode:str}", sync_to_thread=False)
def kiosk_index_mode(mode: str) -> File:
    """Mode-aware kiosk view (one of home / van / cabin / marine / kiosk)."""
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing")
    # Pass the requested mode through to the SPA via a header the
    # boot script reads; it doesn't need to be in the body since the
    # path itself encodes the intent (`window.location.pathname`).
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


@get("/login", sync_to_thread=False)
def login_page(request: Request) -> File | Response:
    """Static HTML login form. POSTs to /api/login → cookie + redirect."""
    from .. import web_auth as _wa
    # First run: the password is still the auto-generated one the user
    # has never seen. Offer an in-browser "create your password" page
    # instead of an "enter password" form they can't fill. `?signin=1`
    # forces the normal form for the rare user who DID read the MOTD
    # password and wants to use it.
    qs = (request.scope.get("query_string", b"") or b"").decode("latin-1")
    if _wa.is_first_run() and "signin=1" not in qs:
        setup = _web_dir() / "login-setup.html"
        if setup.exists():
            return File(path=setup, media_type="text/html",
                        content_disposition_type="inline")
    path = _web_dir() / "login.html"
    if not path.exists():
        raise NotFoundException("login.html missing")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


@post("/api/setup/first-run-password", status_code=200)
async def first_run_password(data: dict, request: Request, state: State) -> Response:
    """One-time, pre-auth endpoint: set the appliance's web password on
    first run (while it's still the auto-generated one). Lets a LAN user
    claim the box in-browser instead of fishing the random password out
    of the MOTD / docker logs. Self-guards: only works while
    is_first_run(): the marker it checks is cleared by the first
    successful login or any password change, so once the owner has been
    in this endpoint is closed. Shares the /api/login brute-force
    backoff. (It does not reject proxied requests.)"""
    from .. import web_auth as _wa
    client_ip = request.scope.get("client", ("?",))[0] if request.scope.get("client") else "?"
    loopback = _wa.is_loopback_source(request.scope)
    if not loopback:
        wait = _wa.login_locked_for(client_ip)
        if wait > 0:
            return Response(
                {"ok": False, "detail": "too many attempts — try again shortly"},
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )
    if not _wa.is_first_run():
        if not loopback:
            _wa.record_login_failure(client_ip)
        return Response(
            {"ok": False,
             "detail": "already set up — sign in with your password"},
            status_code=409,
        )
    pw = (data or {}).get("password") or ""
    if not isinstance(pw, str) or len(pw) < 8:
        return Response(
            {"ok": False, "detail": "password must be at least 8 characters"},
            status_code=400,
        )
    try:
        _wa.set_user_web_password(pw)
    except OSError as e:
        log.exception("first-run password: hash write failed")
        return Response(
            {"ok": False, "detail": f"couldn't save the password: {e}"},
            status_code=500,
        )
    await _audit(state, event_type="first_run_password_set", payload={"ip": client_ip})
    # Log them straight in so they land in Settings, not a second prompt.
    token = _wa.issue_session()
    nxt = (data or {}).get("next") or "/"
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = Response({"ok": True, "redirect": nxt})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME, value=token,
        max_age=_wa.SESSION_TTL_SECONDS, httponly=True,
        samesite="lax", path="/", secure=False,
    )
    return resp


@post("/api/kiosk/claim", status_code=200, sync_to_thread=False)
def kiosk_claim(data: dict, request: Request) -> Response:
    """Exchange a valid kiosk token for a read-only kiosk session cookie.
    The wall display loads `/kiosk?token=<t>` and the SPA calls this once
    per load. Self-guards: only issues a cookie for a token that verifies."""
    from .. import web_auth as _wa
    tok = (data or {}).get("token") or ""
    if not _wa.verify_kiosk_token(tok):
        return Response({"ok": False, "detail": "invalid or revoked kiosk token"},
                        status_code=401)
    session = _wa.issue_session(origin="kiosk")
    resp = Response({"ok": True})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME, value=session,
        max_age=_wa.SESSION_TTL_SECONDS, httponly=True,
        samesite="lax", path="/", secure=False,
    )
    return resp


async def _audit(state, *, event_type: str, payload: dict | None = None) -> None:
    """Helper: write_event to the signed audit log (Phase 8B).
    Best-effort, never raises into the caller, since security
    touchpoints must not break when audit logging hiccups."""
    try:
        from .. import signed_audit as _sa
        store = state["store"]
        if store is not None and store._db is not None:
            await _sa.write_event(store._db, event_type=event_type, payload=payload or {})
            await store._db.commit()
    except Exception:
        log.exception("signed_audit: write_event(%s) failed", event_type)


@post("/api/login", status_code=200)
async def do_login(data: dict, request: Request, state: State) -> Response:
    """Verify the supplied password, drop a session cookie. Returns
    the URL the caller should redirect to (the `next` query param if
    safe, else `/`). Demo-mode + no-password installs short-circuit
    above this in the middleware, so we don't have to handle them.

    Phase 8B (#310), login outcomes recorded in the signed audit
    log so brute-force probes leave a tamper-evident trace and the
    cloud's per-site security view (when wired) can flag anomalies."""
    from .. import web_auth as _wa
    client_ip = request.scope.get("client", ("?",))[0] if request.scope.get("client") else "?"
    # Brute-force backoff: per-source-IP, exponential after a few free
    # tries. Genuine loopback (on-box) is exempt so the owner can never
    # be locked out of their own device.
    loopback = _wa.is_loopback_source(request.scope)
    if not loopback:
        wait = _wa.login_locked_for(client_ip)
        if wait > 0:
            await _audit(state, event_type="login_throttled", payload={"ip": client_ip})
            return Response(
                {"ok": False, "detail": "too many attempts — try again shortly"},
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )
    pw = (data or {}).get("password") or ""
    if not _wa.verify_password(pw):
        # Record the failure with the client IP, useful for
        # detecting attempted brute force after the fact. Don't
        # include the attempted password (obvious anti-pattern).
        if not loopback:
            _wa.record_login_failure(client_ip)
        await _audit(state, event_type="login_failed", payload={"ip": client_ip})
        return Response({"ok": False, "detail": "wrong password"}, status_code=401)
    if not loopback:
        _wa.record_login_success(client_ip)
    await _audit(state, event_type="login_succeeded", payload={"ip": client_ip})
    # The (possibly auto-generated) password has now been used by someone
    # who knew it: close the anonymous first-run "set a password" window.
    _wa.clear_first_run_marker()
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
        secure=False,  # appliance is HTTP on the LAN
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
   , at /sw.js it can intercept the whole site, at /web/sw.js it could
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


# Path prefixes a kiosk credential (LAN kiosk token / kiosk session) may
# reach on this appliance. GET/HEAD/OPTIONS only, enforced in
# kiosk_scope_allows. The kiosk is the appliance's own SPA, so it must be
# able to read `/api/kiosk/config` to render the owner's chosen skin.
# Module-level so it's a single source of truth + regression-testable.
KIOSK_GET_PATHS = (
    "/kiosk", "/api/snapshot", "/api/devices", "/api/poll_run",
    "/api/today", "/api/energy/today", "/api/weather", "/api/kiosk/config",
    # Live-update stream so a token'd LAN wall display gets real-time
    # data, not just the polling fallback. Read-only SSE.
    "/api/stream",
    "/web/", "/static/",
)


# `web.public_view: true` lets anonymous LAN visitors READ the dashboard. It
# must not also hand out the box's configuration, credentials or control
# surface, so these GET prefixes still need a session even then. (The
# dashboard/kiosk read endpoints - snapshot, devices, history, energy,
# forecast/pv, weather/current, kiosk/config, stream, ... - stay readable.)
PUBLIC_VIEW_DENY_PREFIXES = (
    "/api/system/",        # backup (full DB + config.yaml), diagnostics, logs,
                           # netsec, kiosk-tokens, integrations, update, ...
    "/api/network",        # wifi scan/join, IP config
    "/api/setup",          # device wizard: transports, BLE/USB scans
    "/api/cloud",          # pairing state
    "/api/exporters",      # MQTT / Prometheus config
    "/api/forecast/config",
    "/api/weather/config",
    "/api/gps/config",
    "/api/hotspot",
    "/api/location",
    "/api/mqtt_in",
    "/api/alerts",         # rules + transports (webhook URLs, addresses)
    "/api/devices/",       # only the /settings sub-resource, see below
)
# Under /api/system/ these stay readable in public view (dashboard widgets).
PUBLIC_VIEW_SYSTEM_ALLOW = (
    "/api/system/device-health", "/api/system/host-history",
    "/api/system/auth-status", "/api/system/oidc-available",
)


def public_view_allows(method: str, path: str) -> bool:
    """Whether an ANONYMOUS request is allowed under `web.public_view`.
    Read-only methods only, minus the sensitive prefixes above."""
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        return False
    import re as _re
    p = _re.sub(r"/{2,}", "/", path or "/")
    if p.startswith("/api/system/") and p.startswith(PUBLIC_VIEW_SYSTEM_ALLOW):
        return True
    for pref in PUBLIC_VIEW_DENY_PREFIXES:
        if pref == "/api/devices/":
            if p.startswith(pref) and (p.rstrip("/").endswith("/settings")
                                       or "/settings/" in p):
                return False
            continue
        if p == pref.rstrip("/") or p.startswith(pref):
            return False
    return True


def kiosk_scope_allows(method: str, path: str) -> bool:
    """Whether a kiosk-credential request for (method, path) is permitted
    on this appliance. Read-only by construction — only safe
    GET-family methods on the `KIOSK_GET_PATHS` allow-list pass; a
    kiosk visitor can never mutate (e.g. PATCH /api/kiosk/config is denied,
    so a guest can't change the wall display's skin)."""
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        return False
    return path == "/kiosk" or any(
        path.startswith(p) for p in KIOSK_GET_PATHS
    )


def build_app(
    config: Config,
    db_path: str,
    interval_seconds: int = 60,
    config_path: str = "config.yaml",
) -> Litestar:
    store = Store(db_path)
    # Apply config.history overrides (#172). The CLI-passed
    # interval_seconds wins ONLY when the config doesn't specify one,
    # so the YAML value takes effect after the next daemon restart
    # without anyone having to re-edit a systemd unit. Retention
    # windows mutate `store` directly, also persisted via the
    # /api/system/history_settings PATCH endpoint.
    hist = getattr(config, "history", None)
    if hist:
        if hist.poll_interval_seconds is not None:
            interval_seconds = int(hist.poll_interval_seconds)
        store.set_retention_policy(
            raw_days=hist.retention_raw_days,
            min_days=hist.retention_min_days,
            hour_days=hist.retention_hour_days,
        )
    scheduler = PollScheduler(config, store, interval_seconds=interval_seconds)
    # Stash config_path on the scheduler too so the CloudService it
    # owns can persist mutations (cloud pairing state from heartbeat) without
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
        # Scheduled local-snapshot service, only spins up the loop if
        # backup.enabled is set in config.yaml. The on-demand endpoints
        # in api/backup.py work whether or not this service is running.
        from ..config import BackupCfg
        backup_cfg = config.backup or BackupCfg()
        # If cloud_upload is on AND the appliance is paired, wire the
        # uploader hook. Cloud-side enforces the Pro/Installer tier
        # gate; we just send and surface the response (incl. 402) on
        # the BackupService for the Settings UI to render.
        uploader = None
        if (backup_cfg.cloud_upload
                and config.cloud is not None
                and config.cloud.bearer_token
                and config.cloud.endpoint):
            from ..backup.cloud_uploader import make_uploader
            uploader = make_uploader(
                config.cloud.endpoint,
                config.cloud.bearer_token,
                backup_cfg.cloud_keep_count,
            )
        backup_svc = BackupService(
            backup_cfg, Path(db_path), Path(config_path),
            cloud_uploader=uploader,
        )
        await backup_svc.start()
        app.state["backup_service"] = backup_svc
        # Make the BackupService reachable from CloudService so the
        # cloud-triggered `backup_now` command (#165) can call
        # snapshot_now() through the same code path as the scheduled
        # weekly backup, same naming, same upload, same prune.
        setattr(scheduler, "backup_service", backup_svc)

    async def on_shutdown(app: Litestar) -> None:
        svc = app.state.get("backup_service")
        if svc is not None:
            await svc.stop()
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
    # dashboard needs to function (none currently, the local
    # dashboard's POSTs are all writes, so this is empty). Pure ASGI
    # middleware so we can match by request scope without going through
    # Litestar's route system.
    import os as _os
    _DEMO = _os.environ.get("WATTPOST_DEMO") == "1"

    def _log_500_traceback(exc: Exception, request: Request) -> None:
        # Litestar swallows the traceback for 500s by default; without
        # this we only see "500 Internal Server Error" in logs and have
        # to repro by hand. Mirrors what cloud got in #194.
        from litestar.exceptions import HTTPException as _LHE
        if isinstance(exc, _LHE) and exc.status_code < 500:
            return
        import logging as _logging
        import traceback as _tb
        _log = _logging.getLogger(__name__)
        _log.error(
            "appliance 500 on %s %s\n%s",
            getattr(request, "method", "?"),
            getattr(getattr(request, "url", None), "path", "?"),
            "".join(_tb.format_exception(type(exc), exc, exc.__traceback__)),
        )

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
    #   - source IP is loopback and not relayed by a reverse proxy:
    #     trusted (LAN clients can't fake that)
    #   - anonymous paths (/kiosk, /web/static, /login, /api/login,
    #     /api/heartbeat): always allowed
    #   - GET on /api/* read endpoints: allowed when "read-only public"
    #     mode is on (default for v0 so kiosks-on-wifi keep working
    #     unchanged); the toggle moves to Settings → System later
    #   - everything else: needs a valid wp_local_session cookie
    #     → 401 (for /api/*) or redirect to /login (for HTML routes)
    from .. import web_auth as _web_auth
    # Login is mandatory for the whole appliance by default (cloud #15).
    # Anonymous LAN viewing only happens when the operator explicitly
    # opts in via `web.public_view` (a deliberately-public box). Wall
    # displays use a kiosk token regardless. Genuine loopback
    # still bypasses; the first-run flow makes sure a password exists.
    _READONLY_PUBLIC = bool(getattr(config, "web", None) and config.web.public_view)

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
            # for requests relayed by a reverse proxy on this host
            # (forwarding headers present).
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
            # No password file = misconfigured install. Fail-closed
            # to the login page (which itself surfaces a "password
            # not configured" message), NEVER let the request through.
            if not _web_auth.password_is_set():
                # /login + /api/login + static assets are always
                # allowed, the user needs SOMETHING to render.
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
            # session, so kiosks-on-wifi keep working unchanged.
            if _READONLY_PUBLIC and public_view_allows(method, path):
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
            # Local kiosk credential (read-only wall displays, cloud #15).
            # A kiosk-origin session cookie — or a valid ?token= on a
            # kiosk-scoped GET to bootstrap the first load — grants the
            # read-only KIOSK allow-list only, never writes.
            if kiosk_scope_allows(method, path):
                if _web_auth.session_origin(token) == "kiosk":
                    await self.app(scope, receive, send)
                    return
                qs = scope.get("query_string", b"").decode("latin-1")
                qtok = None
                for part in qs.split("&"):
                    if part.startswith("token="):
                        from urllib.parse import unquote
                        qtok = unquote(part[6:])
                        break
                if qtok and _web_auth.verify_kiosk_token(qtok):
                    await self.app(scope, receive, send)
                    return
            if _web_auth.is_session_valid(token):
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
            today_soc_envelope,
            list_devices,
            device_latest,
            device_history,
            device_history_csv,
            energy_today,
            device_lifetime,
            device_efficiency,
            device_charger_stats,
            device_settings,
            patch_device_setting,
            set_device_display_name,
            export_backup,
            import_backup,
            backup_schedule,
            backup_run_now,
            backup_download_one,
            backup_delete_one,
            backup_cloud_list,
            backup_cloud_restore,
            backup_cloud_toggle,
            discovery_state,
            discovery_toggle,
            local_telemetry_state,
            local_telemetry_toggle,
            battery_health,
            runtime_forecast,
            load_heatmap,
            stream,
            snapshot,
            prometheus_metrics,
            list_alerts,
            test_alert,
            restart_daemon,
            system_logs,
            system_info,
            auth_status,
            diagnostics_bundle,
            get_history_settings,
            patch_history_settings,
            device_health,
            host_history,
            reset_to_defaults,
            update_state,
            update_check_now,
            set_update_channel,
            rotate_web_password,
            update_apply,
            update_log,
            slot_state,
            slot_rollback,
            release_changelog,
            appliance_branding,
            service_worker,
            manifest,
            create_rule,
            update_rule,
            delete_rule,
            create_transport,
            update_transport,
            delete_transport,
            update_quiet_hours,
            get_integrations,
            get_pv_forecast,
            get_forecast_config,
            update_forecast_config,
            test_forecast_fetch,
            get_forecast_accuracy,
            get_current_weather,
            get_gps_status,
            get_gps_config,
            update_gps_config,
            get_mqtt_in_status,
            get_hotspot_status,
            update_hotspot_config,
            hotspot_on,
            hotspot_off,
            wifi_scan,
            wifi_join,
            wifi_scan_ap_bounce,
            wifi_scan_ap_bounce_status,
            network_status,
            network_set_ipv4,
            android_204,
            apple_detect,
            win_connecttest,
            win_ncsi,
            ubuntu_canonical,
            get_location_status,
            update_location_share,
            get_kiosk_config,
            update_kiosk_config,
            get_weather_config,
            update_weather_config,
            test_weather_fetch,
            weather_history,
            get_cloud_config,
            update_cloud_config,
            pair_appliance,
            unpair_appliance,
            trigger_heartbeat,
            get_mqtt_config,
            update_mqtt_config,
            test_mqtt,
            get_prometheus_config,
            update_prometheus_config,
            ble_status,
            discovered,
            ble_scan,
            ble_diagnose,
            usb_scan,
            hid_scan,
            add_transport,
            edit_setup_transport,
            delete_setup_transport,
            list_setup_transports,
            known_devices,
            probe,
            add_device,
            delete_device,
            delete_device_by_label,
            list_outputs,
            toggle_output,
            confirm_output_safety,
            list_output_schedules,
            create_output_schedule,
            update_output_schedule,
            delete_output_schedule,
            get_solar_pause,
            patch_solar_pause,
            index,
            kiosk_index,
            kiosk_index_mode,
            login_page,
            do_login,
            first_run_password,
            kiosk_claim,
            kiosk_tokens_list,
            kiosk_tokens_create,
            kiosk_tokens_revoke,
            netsec_status,
            netsec_set_firewall,
            netsec_set_ssh,
            do_logout,
            # Identity v2 Phase 3 (#305), LAN OIDC login. Both
            # endpoints 404 when oidc_config.json is absent (i.e.
            # the appliance hasn't completed v2 upgrade yet), so
            # registering them is safe even on pre-v2 appliances.
            auth_lan_login,
            auth_callback,
            oidc_available,
            static_router,
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        after_exception=[_log_500_traceback],
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
