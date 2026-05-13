"""Alert evaluator.

Resolves each rule's metric path against the per-poll context dict,
applies the comparison operator, fires events (rate-limited by per-rule
cooldown), and dispatches them to the rule's configured transports.

Context shape, populated by `build_alert_context()` below:

    {
      "bank": {soc_pct, netW, meanV, totalRem, totalCap, worst_pack_drift_v, …},
      "devices": {"<label>": {<latest metrics>}, …},
      "aggregate": {max_cell_drift_v, …},
    }
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .base import AlertEvent, AlertRule, NotificationTransport
from .registry import NOTIFICATION_TRANSPORTS

log = logging.getLogger(__name__)


_OPS = {
    "lt":  lambda a, b: a <  b,
    "lte": lambda a, b: a <= b,
    "gt":  lambda a, b: a >  b,
    "gte": lambda a, b: a >= b,
    "eq":  lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


def _resolve(path: str, ctx: dict) -> Any:
    """Follow a dotted path through a nested dict. Returns None on miss."""
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def build_alert_context(snapshot: dict) -> dict:
    """Reshape the scheduler's snapshot into the context the rules
    address. Centralised here so the cloud evaluator can use the same
    transformation."""
    devices_list = snapshot.get("devices") or []
    by_label = {d["label"]: d.get("latest", {}) for d in devices_list}

    # The "bank" pseudo-device is computed by the store and exposed
    # alongside real devices. If it's missing (very early in the daemon
    # life), fall back to an empty mapping so rule resolution returns
    # None instead of crashing.
    bank_latest = by_label.get("bank", {}) or {}
    bank_ctx = {
        "soc_pct":             bank_latest.get("soc_pct"),
        "netW":                bank_latest.get("power_w"),
        "meanV":               bank_latest.get("voltage_v"),
        "totalRem":            bank_latest.get("remaining_ah"),
        "totalCap":            bank_latest.get("capacity_ah"),
        "worst_pack_drift_v":  bank_latest.get("worst_pack_drift_v"),
    }

    # Aggregate-across-devices helpers — kept narrow on purpose; expand
    # only when a rule actually needs a new one.
    smart_battery_latest = [
        d.get("latest", {}) for d in devices_list
        if d.get("kind") == "smart_battery"
    ]
    max_drift = None
    for l in smart_battery_latest:
        v = l.get("cell_drift_v")
        if isinstance(v, (int, float)):
            max_drift = v if max_drift is None else max(max_drift, v)

    return {
        "bank":      bank_ctx,
        "devices":   by_label,
        "aggregate": {"max_cell_drift_v": max_drift},
    }


class AlertEngine:
    """Holds rules + transports, evaluates after each poll."""

    def __init__(
        self,
        rules: list[AlertRule],
        transports_cfg: list[dict],
    ) -> None:
        self.rules = rules
        self.transports_cfg = transports_cfg
        self._transports: dict[str, NotificationTransport] = {}
        # rule_id -> ts of last fire (for cooldown gating)
        self._last_fired: dict[str, int] = {}
        # rule_id -> last AlertEvent (so the Settings UI can show recent
        # activity without round-tripping to storage)
        self._last_event: dict[str, AlertEvent] = {}

    @property
    def transport_ids(self) -> list[str]:
        return list(self._transports)

    async def start(self) -> None:
        for tcfg in self.transports_cfg:
            ttype = tcfg.get("type")
            factory = NOTIFICATION_TRANSPORTS.get(ttype)
            if factory is None:
                log.error("unknown notification transport type %r (registered: %s)",
                          ttype, list(NOTIFICATION_TRANSPORTS))
                continue
            try:
                t = factory(tcfg)
                await t.start()
                self._transports[t.id] = t
                log.info("alert transport %r ready (%s)", t.id, ttype)
            except Exception:
                log.exception("alert transport %s failed to start", tcfg.get("id"))

    async def stop(self) -> None:
        for t in self._transports.values():
            try:
                await t.stop()
            except Exception:
                log.exception("alert transport %s stop failed", t.id)
        self._transports.clear()

    def reload_rules(self, rules: list[AlertRule]) -> None:
        """Hot-swap the rule list without restarting the daemon. Keeps
        the cooldown / last-fired state so a re-saved rule doesn't lose
        its rate-limit history."""
        self.rules = rules
        # Drop history for rules that no longer exist; keep the rest.
        live_ids = {r.id for r in rules}
        self._last_fired = {k: v for k, v in self._last_fired.items() if k in live_ids}
        self._last_event = {k: v for k, v in self._last_event.items() if k in live_ids}

    async def evaluate(self, snapshot: dict) -> list[AlertEvent]:
        """Run every rule against the supplied snapshot. Returns the list
        of newly-fired events (post-cooldown). Never raises — a misbehaving
        transport must not break the daemon."""
        ctx = build_alert_context(snapshot)
        now = int(time.time())
        fired: list[AlertEvent] = []
        for rule in self.rules:
            op = _OPS.get(rule.op)
            if op is None:
                log.warning("rule %r has unknown op %r", rule.id, rule.op)
                continue
            val = _resolve(rule.metric, ctx)
            if not isinstance(val, (int, float)):
                continue
            if not op(val, rule.threshold):
                continue
            last = self._last_fired.get(rule.id, 0)
            if now - last < rule.cooldown_seconds:
                continue
            self._last_fired[rule.id] = now
            event = AlertEvent(
                rule_id=rule.id, name=rule.name, severity=rule.severity,
                metric=rule.metric, value=float(val), threshold=rule.threshold,
                op=rule.op, ts=now,
            )
            self._last_event[rule.id] = event
            fired.append(event)
            await self._dispatch(event, rule.transports)
            log.info("alert fired: %s (%s=%s %s %s)",
                     rule.id, rule.metric, val, rule.op, rule.threshold)
        return fired

    async def test_fire(self, rule_id: str) -> AlertEvent | None:
        """Force a single rule to fire regardless of state, for the
        Settings → Alerts "Test" button."""
        rule = next((r for r in self.rules if r.id == rule_id), None)
        if rule is None:
            return None
        event = AlertEvent(
            rule_id=rule.id, name=f"[TEST] {rule.name}", severity=rule.severity,
            metric=rule.metric, value=0.0, threshold=rule.threshold,
            op=rule.op, ts=int(time.time()),
        )
        await self._dispatch(event, rule.transports)
        return event

    def snapshot_state(self) -> dict:
        """Cheap state dump for the Settings UI. Includes the original
        transport config dict (passwords stripped) so the UI can show the
        topic / URL / host without round-tripping to YAML."""
        # Build a quick lookup of the loaded transport instances by id so
        # we can mark "alive" vs config-only.
        live = set(self._transports)

        def _sanitise(cfg: dict) -> dict:
            return {
                k: ("****" if k.lower() in {"password", "secret", "token", "api_key"} else v)
                for k, v in cfg.items()
            }

        return {
            "rules": [
                {
                    "id": r.id, "name": r.name, "metric": r.metric, "op": r.op,
                    "threshold": r.threshold, "severity": r.severity,
                    "cooldown_seconds": r.cooldown_seconds,
                    "transports": r.transports,
                    "last_fired_ts": self._last_fired.get(r.id),
                    "last_value": self._last_event[r.id].value
                                  if r.id in self._last_event else None,
                }
                for r in self.rules
            ],
            "transports": [
                {
                    "id": tcfg.get("id"),
                    "type": tcfg.get("type"),
                    "alive": tcfg.get("id") in live,
                    "config": _sanitise(
                        {k: v for k, v in tcfg.items() if k not in ("id", "type")}
                    ),
                }
                for tcfg in self.transports_cfg
            ],
        }

    async def _dispatch(
        self, event: AlertEvent, transport_ids: list[str],
    ) -> None:
        for tid in transport_ids:
            t = self._transports.get(tid)
            if t is None:
                log.warning("rule %s references unknown transport %r", event.rule_id, tid)
                continue
            try:
                await t.send(event)
            except Exception:
                log.exception("alert send via %s failed", tid)
