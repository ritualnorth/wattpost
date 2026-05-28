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

    # Aggregate-across-devices helpers, kept narrow on purpose; expand
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
        quiet_hours: tuple[int, int] | None = None,
    ) -> None:
        self.rules = rules
        self.transports_cfg = transports_cfg
        # (start_hour, end_hour) in local time. None or equal hours = disabled.
        # Overnight windows (start > end) are supported.
        if quiet_hours is not None and quiet_hours[0] == quiet_hours[1]:
            quiet_hours = None
        self.quiet_hours = quiet_hours
        self._transports: dict[str, NotificationTransport] = {}
        # rule_id -> ts of last fire (for cooldown gating)
        self._last_fired: dict[str, int] = {}
        # rule_id -> last AlertEvent (so the Settings UI can show recent
        # activity without round-tripping to storage)
        self._last_event: dict[str, AlertEvent] = {}
        # Events deferred during quiet hours, flushed when the window
        # ends. Tuple of (event, transport_ids) so we re-dispatch to the
        # same targets the rule would have hit.
        self._pending: list[tuple[AlertEvent, list[str]]] = []
        # Ring buffer of every event we've fired, in chronological order,
        # capped to the most recent N. The cloud-heartbeat reads from
        # here and ships any with ts > last seen so the cloud inbox
        # (#206) gets every alert without per-heartbeat state on the
        # appliance side, dedup is done by the cloud via a UNIQUE
        # constraint on (appliance_id, rule_id, ts).
        self._event_history: list[AlertEvent] = []
        # Tracks the quiet-hours state across evaluate() calls so we
        # detect the falling edge (was in, now out) and flush. None
        # until the first evaluate() so an existing in-window startup
        # doesn't immediately fire empty digest.
        self._was_quiet: bool | None = None

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

    def recent_events_since(self, ts: int, limit: int = 20) -> list[AlertEvent]:
        """Events from the ring buffer fired strictly after `ts`.
        Newest-first up to `limit`. Used by the cloud heartbeat to
        ship the inbox feed without per-heartbeat state on the
        appliance side; dedup happens cloud-side via a UNIQUE
        constraint."""
        out: list[AlertEvent] = []
        for ev in reversed(self._event_history):
            if ev.ts <= ts:
                break
            out.append(ev)
            if len(out) >= limit:
                break
        return list(reversed(out))  # chronological in the payload

    def reload_rules(self, rules: list[AlertRule]) -> None:
        """Hot-swap the rule list without restarting the daemon. Keeps
        the cooldown / last-fired state so a re-saved rule doesn't lose
        its rate-limit history."""
        self.rules = rules
        # Drop history for rules that no longer exist; keep the rest.
        live_ids = {r.id for r in rules}
        self._last_fired = {k: v for k, v in self._last_fired.items() if k in live_ids}
        self._last_event = {k: v for k, v in self._last_event.items() if k in live_ids}

    def _is_quiet_now(self, ts: int | None = None) -> bool:
        """Are we inside the configured quiet-hours window right now?"""
        if self.quiet_hours is None:
            return False
        start, end = self.quiet_hours
        hour = time.localtime(ts).tm_hour
        if start < end:
            # Same-day window, e.g. 13:00..17:00.
            return start <= hour < end
        # Overnight window, e.g. 22:00..07:00.
        return hour >= start or hour < end

    async def evaluate(self, snapshot: dict) -> list[AlertEvent]:
        """Run every rule against the supplied snapshot. Returns the list
        of newly-fired events (post-cooldown). Never raises, a misbehaving
        transport must not break the daemon.

        Quiet hours: `warn`-severity events fire into `_pending` instead
        of dispatching. When the window ends (next evaluate after the
        end_hour passes), buffered events flush in one batch. `alarm`
        severity always pages through immediately."""
        ctx = build_alert_context(snapshot)
        now = int(time.time())
        in_quiet = self._is_quiet_now(now)

        # Detect window-end edge and flush any buffered events. Skipped
        # on the very first evaluate() so a daemon starting *inside* the
        # window doesn't immediately fire phantom events.
        if self._was_quiet is True and not in_quiet:
            await self._flush_pending()
        self._was_quiet = in_quiet

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
            self._event_history.append(event)
            # Cap the buffer. 200 entries at a 60s poll = ~3 hours of
            # alert activity worst-case (one fire per cycle), plenty
            # of headroom against the cloud's 5-min heartbeat tick.
            if len(self._event_history) > 200:
                del self._event_history[:-200]
            fired.append(event)
            if in_quiet and rule.severity != "alarm":
                # Buffer; flushes when the quiet-hours window ends.
                self._pending.append((event, list(rule.transports)))
                log.info("alert buffered (quiet hours): %s (%s=%s %s %s)",
                         rule.id, rule.metric, val, rule.op, rule.threshold)
            else:
                await self._dispatch(event, rule.transports)
                log.info("alert fired: %s (%s=%s %s %s)",
                         rule.id, rule.metric, val, rule.op, rule.threshold)
        return fired

    async def _flush_pending(self) -> None:
        """Dispatch every event buffered during quiet hours, in the
        order they fired, then clear the buffer."""
        if not self._pending:
            return
        log.info("quiet hours ended, flushing %d buffered alert(s)",
                 len(self._pending))
        pending, self._pending = self._pending, []
        for event, transport_ids in pending:
            await self._dispatch(event, transport_ids)

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
            # #259, "cloud" is a magic transport id. The fan-out
            # (web push, native push, email) happens in the cloud
            # when the heartbeat ingest sees the event has "cloud"
            # in its transports list. Nothing to do appliance-side.
            if tid == "cloud":
                continue
            t = self._transports.get(tid)
            if t is None:
                log.warning("rule %s references unknown transport %r", event.rule_id, tid)
                continue
            try:
                await t.send(event)
            except Exception:
                log.exception("alert send via %s failed", tid)
