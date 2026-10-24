"""Per-device REST endpoints: list, latest, settings, history, efficiency (split from app.py)."""
from __future__ import annotations

import time
from typing import Any

from litestar import Request, get, patch, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from ..storage import Store

import logging
log = logging.getLogger(__name__)


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
