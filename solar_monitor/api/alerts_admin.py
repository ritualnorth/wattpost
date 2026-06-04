"""CRUD endpoints for alert rules + notification transports.

UI-first: the SPA edits rules / transports through these endpoints, the
endpoints validate + atomically rewrite `config.yaml`, and (for rules)
hot-reload the running AlertEngine so changes take effect immediately
without a daemon restart. Transport changes return `restart_required:
true` because transports own connections (httpx clients, SMTP sessions)
that need rebuilding.

Mutation model mirrors `api/setup.py` (the BLE-add-device flow):
  - validate against schema + against the live config
  - take a .bak of config.yaml
  - write via .tmp + atomic rename
  - mutate the in-memory Config struct so endpoints see the change
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import delete, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from ..alerts import AlertRule
from ..alerts.registry import NOTIFICATION_TRANSPORTS
from ..config import AlertRuleCfg, Config, QuietHoursCfg
from ..scheduler import PollScheduler

log = logging.getLogger(__name__)

_VALID_OPS = {"lt", "lte", "gt", "gte", "eq", "neq"}
_VALID_SEVERITIES = {"warn", "alarm"}


# ---------- payload schemas ----------

class AlertRulePayload(msgspec.Struct, kw_only=True):
    id: str
    name: str
    metric: str
    op: str
    threshold: float
    severity: str = "warn"
    cooldown_seconds: int = 1800
    transports: list[str] = []
    enabled: bool = True


class NotificationTransportPayload(msgspec.Struct, kw_only=True):
    id: str
    type: str
    # Free-form extra fields per transport type (topic / url / host / etc).
    # Validated by the transport's own factory at start time.
    extra: dict[str, Any] = {}


# ---------- helpers ----------

def _save_config(config_path: str, mutator) -> None:
    """Load config.yaml, run `mutator(raw_dict)`, atomic-write back with
    a .bak. The mutator must return the (possibly modified) dict."""
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw = mutator(raw)
    if raw is None:
        raise RuntimeError("config mutator returned None")
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)


def _validate_rule(p: AlertRulePayload, config: Config, transport_ids: set[str]) -> None:
    if not p.id or not p.id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400,
                            detail="rule id must be alphanumeric/_/- only")
    if p.op not in _VALID_OPS:
        raise HTTPException(status_code=400,
                            detail=f"op must be one of {sorted(_VALID_OPS)}")
    if p.severity not in _VALID_SEVERITIES:
        raise HTTPException(status_code=400,
                            detail=f"severity must be one of {sorted(_VALID_SEVERITIES)}")
    if p.cooldown_seconds < 0:
        raise HTTPException(status_code=400, detail="cooldown_seconds must be >= 0")
    if not p.metric:
        raise HTTPException(status_code=400, detail="metric is required")
    if not p.transports:
        raise HTTPException(status_code=400,
                            detail="at least one transport is required")
    for tid in p.transports:
        if tid not in transport_ids:
            raise HTTPException(status_code=400,
                                detail=f"unknown transport id {tid!r}")


def _validate_transport(p: NotificationTransportPayload) -> None:
    if not p.id or not p.id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400,
                            detail="transport id must be alphanumeric/_/- only")
    if p.type not in NOTIFICATION_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown transport type {p.type!r}; "
                   f"available: {sorted(NOTIFICATION_TRANSPORTS)}",
        )


def _refresh_rules_in_engine(scheduler: PollScheduler, config: Config) -> None:
    """Push the latest config.alerts into the live engine."""
    rules = [
        AlertRule(
            id=r.id, name=r.name, metric=r.metric, op=r.op,
            threshold=r.threshold, severity=r.severity,
            cooldown_seconds=r.cooldown_seconds, transports=r.transports,
            enabled=getattr(r, "enabled", True),
        )
        for r in config.alerts
    ]
    scheduler._alerts.reload_rules(rules)


def _transport_ids(config: Config) -> set[str]:
    return {t["id"] for t in config.notification_transports if "id" in t}


# ---------- rule CRUD ----------

@post("/api/alerts/rules")
async def create_rule(data: AlertRulePayload, state: State) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    scheduler: PollScheduler = state["scheduler"]

    _validate_rule(data, config, _transport_ids(config))
    if any(r.id == data.id for r in config.alerts):
        raise HTTPException(status_code=409, detail=f"rule {data.id!r} already exists")

    new_cfg = AlertRuleCfg(
        id=data.id, name=data.name, metric=data.metric, op=data.op,
        threshold=data.threshold, severity=data.severity,
        cooldown_seconds=data.cooldown_seconds, transports=data.transports,
        enabled=data.enabled,
    )
    config.alerts.append(new_cfg)

    def _mutate(raw):
        raw.setdefault("alerts", []).append({
            "id": data.id, "name": data.name, "metric": data.metric,
            "op": data.op, "threshold": data.threshold,
            "severity": data.severity, "cooldown_seconds": data.cooldown_seconds,
            "transports": data.transports, "enabled": data.enabled,
        })
        return raw

    _save_config(config_path, _mutate)
    _refresh_rules_in_engine(scheduler, config)
    log.info("alert rule created: %s (%s %s %s)", data.id, data.metric, data.op, data.threshold)
    return {"ok": True, "id": data.id, "restart_required": False}


@put("/api/alerts/rules/{rule_id:str}")
async def update_rule(rule_id: str, data: AlertRulePayload, state: State) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    scheduler: PollScheduler = state["scheduler"]

    if data.id != rule_id:
        raise HTTPException(status_code=400, detail="payload id does not match URL")
    if not any(r.id == rule_id for r in config.alerts):
        raise NotFoundException(f"rule {rule_id!r} not found")
    _validate_rule(data, config, _transport_ids(config))

    config.alerts = [
        AlertRuleCfg(
            id=data.id, name=data.name, metric=data.metric, op=data.op,
            threshold=data.threshold, severity=data.severity,
            cooldown_seconds=data.cooldown_seconds, transports=data.transports,
            enabled=data.enabled,
        ) if r.id == rule_id else r
        for r in config.alerts
    ]

    def _mutate(raw):
        rules = raw.get("alerts", [])
        for i, r in enumerate(rules):
            if r.get("id") == rule_id:
                rules[i] = {
                    "id": data.id, "name": data.name, "metric": data.metric,
                    "op": data.op, "threshold": data.threshold,
                    "severity": data.severity, "cooldown_seconds": data.cooldown_seconds,
                    "transports": data.transports, "enabled": data.enabled,
                }
                break
        raw["alerts"] = rules
        return raw

    _save_config(config_path, _mutate)
    _refresh_rules_in_engine(scheduler, config)
    log.info("alert rule updated: %s", rule_id)
    return {"ok": True, "id": rule_id, "restart_required": False}


@delete("/api/alerts/rules/{rule_id:str}", status_code=200)
async def delete_rule(rule_id: str, state: State) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")
    scheduler: PollScheduler = state["scheduler"]

    if not any(r.id == rule_id for r in config.alerts):
        raise NotFoundException(f"rule {rule_id!r} not found")

    config.alerts = [r for r in config.alerts if r.id != rule_id]

    def _mutate(raw):
        raw["alerts"] = [r for r in raw.get("alerts", []) if r.get("id") != rule_id]
        return raw

    _save_config(config_path, _mutate)
    _refresh_rules_in_engine(scheduler, config)
    log.info("alert rule deleted: %s", rule_id)
    return {"ok": True, "id": rule_id, "restart_required": False}


# ---------- transport CRUD ----------

@post("/api/alerts/transports")
async def create_transport(
    data: NotificationTransportPayload, state: State,
) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    _validate_transport(data)
    if any(t.get("id") == data.id for t in config.notification_transports):
        raise HTTPException(status_code=409, detail=f"transport {data.id!r} already exists")

    new_entry = {"id": data.id, "type": data.type, **data.extra}
    config.notification_transports.append(new_entry)

    def _mutate(raw):
        raw.setdefault("notification_transports", []).append(new_entry)
        return raw

    _save_config(config_path, _mutate)
    log.info("notification transport created: %s (%s)", data.id, data.type)
    return {"ok": True, "id": data.id, "restart_required": True}


@put("/api/alerts/transports/{transport_id:str}")
async def update_transport(
    transport_id: str, data: NotificationTransportPayload, state: State,
) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if data.id != transport_id:
        raise HTTPException(status_code=400, detail="payload id does not match URL")
    if not any(t.get("id") == transport_id for t in config.notification_transports):
        raise NotFoundException(f"transport {transport_id!r} not found")
    _validate_transport(data)

    new_entry = {"id": data.id, "type": data.type, **data.extra}
    config.notification_transports = [
        new_entry if t.get("id") == transport_id else t
        for t in config.notification_transports
    ]

    def _mutate(raw):
        ts = raw.get("notification_transports", [])
        for i, t in enumerate(ts):
            if t.get("id") == transport_id:
                ts[i] = new_entry
                break
        raw["notification_transports"] = ts
        return raw

    _save_config(config_path, _mutate)
    log.info("notification transport updated: %s", transport_id)
    return {"ok": True, "id": transport_id, "restart_required": True}


@delete("/api/alerts/transports/{transport_id:str}", status_code=200)
async def delete_transport(transport_id: str, state: State) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if not any(t.get("id") == transport_id for t in config.notification_transports):
        raise NotFoundException(f"transport {transport_id!r} not found")

    # Refuse to delete a transport that's still referenced by a rule,
    # avoids leaving rules pointing at nothing.
    referenced = [r.id for r in config.alerts if transport_id in r.transports]
    if referenced:
        raise HTTPException(
            status_code=409,
            detail=f"transport {transport_id!r} is still used by rule(s): "
                   + ", ".join(referenced),
        )

    config.notification_transports = [
        t for t in config.notification_transports if t.get("id") != transport_id
    ]

    def _mutate(raw):
        raw["notification_transports"] = [
            t for t in raw.get("notification_transports", [])
            if t.get("id") != transport_id
        ]
        return raw

    _save_config(config_path, _mutate)
    log.info("notification transport deleted: %s", transport_id)
    return {"ok": True, "id": transport_id, "restart_required": True}


# ---------- quiet hours CRUD ----------

class QuietHoursPayload(msgspec.Struct, kw_only=True):
    # `null` for either field disables quiet hours. We accept both
    # ints and null so the UI can clear the window with one PUT.
    start_hour: int | None = None
    end_hour: int | None = None


@put("/api/alerts/quiet_hours")
async def update_quiet_hours(
    data: QuietHoursPayload, state: State,
) -> dict[str, Any]:
    """Set or clear the quiet-hours window. Clearing requires
    null/null (or equal start==end, which the engine treats as
    disabled). The engine reads `quiet_hours` only at boot; runtime
    changes return `restart_required: true` so the UI can prompt."""
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    enabled = data.start_hour is not None and data.end_hour is not None
    if enabled:
        for v, name in ((data.start_hour, "start_hour"), (data.end_hour, "end_hour")):
            if not (0 <= v <= 23):
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} must be in [0, 23]; got {v}",
                )

    new_qh = (
        QuietHoursCfg(start_hour=data.start_hour, end_hour=data.end_hour)
        if enabled else None
    )
    config.quiet_hours = new_qh

    def _mutate(raw):
        if enabled:
            raw["quiet_hours"] = {
                "start_hour": data.start_hour,
                "end_hour":   data.end_hour,
            }
        else:
            raw.pop("quiet_hours", None)
        return raw

    _save_config(config_path, _mutate)
    log.info(
        "quiet hours updated: %s",
        f"{data.start_hour}..{data.end_hour}" if enabled else "disabled",
    )
    return {"ok": True, "restart_required": True}
