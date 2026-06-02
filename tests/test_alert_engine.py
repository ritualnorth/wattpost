"""Functional tests for the appliance's local alert engine.

Drives AlertEngine.evaluate() with crafted snapshots and a recording
stub transport, so the full local path is exercised with no real
ntfy/Discord/SMTP: rule firing, transport routing, cooldown gating, the
magic "cloud" transport (handled cloud-side, not dispatched locally),
quiet-hours buffering (warn) vs paging (alarm), and per-device metrics.
"""
import asyncio
import time

from solar_monitor.alerts.engine import AlertEngine
from solar_monitor.alerts.base import AlertRule


class Recorder:
    """Stub transport: records events instead of sending them."""
    def __init__(self, id="rec"):
        self.id = id
        self.sent = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, event):
        self.sent.append(event)


def _bank_snap(soc, power=-50.0, volts=13.0):
    return {"devices": [{"label": "bank", "kind": "shunt",
                         "latest": {"soc_pct": soc, "power_w": power, "voltage_v": volts}}]}


def _engine(rules, quiet=None):
    e = AlertEngine(rules=rules, transports_cfg=[], quiet_hours=quiet)
    rec = Recorder()
    e._transports[rec.id] = rec            # inject without a real start()
    return e, rec


def _rule(rid="low-soc", metric="bank.soc_pct", op="lt", threshold=30.0,
          sev="warn", transports=("rec",), cooldown=1800):
    return AlertRule(id=rid, name=rid, metric=metric, op=op, threshold=threshold,
                     severity=sev, cooldown_seconds=cooldown, transports=list(transports))


def test_rule_fires_and_routes_below_threshold():
    e, rec = _engine([_rule()])
    fired = asyncio.run(e.evaluate(_bank_snap(25)))
    assert [f.rule_id for f in fired] == ["low-soc"]
    assert len(rec.sent) == 1 and rec.sent[0].value == 25.0


def test_no_fire_when_metric_satisfies_threshold():
    e, rec = _engine([_rule()])
    fired = asyncio.run(e.evaluate(_bank_snap(40)))
    assert fired == [] and rec.sent == []


def test_cooldown_blocks_refire():
    e, rec = _engine([_rule(cooldown=1800)])
    asyncio.run(e.evaluate(_bank_snap(25)))
    fired2 = asyncio.run(e.evaluate(_bank_snap(24)))   # still low, but within cooldown
    assert fired2 == []
    assert len(rec.sent) == 1


def test_cloud_transport_skips_local_dispatch_but_ships_to_inbox():
    e, rec = _engine([_rule(transports=["cloud"])])
    fired = asyncio.run(e.evaluate(_bank_snap(25)))
    assert len(fired) == 1                          # fired (for the heartbeat feed)
    assert rec.sent == []                           # not dispatched locally
    assert len(e.recent_events_since(0)) == 1       # in the ring buffer for the cloud


def test_quiet_hours_buffers_warn_but_pages_alarm():
    h = time.localtime().tm_hour
    quiet = (h, (h + 1) % 24)                        # window covering 'now'
    e, rec = _engine([_rule(rid="warn-r", sev="warn"),
                      _rule(rid="alarm-r", sev="alarm")], quiet=quiet)
    fired = asyncio.run(e.evaluate(_bank_snap(25)))
    assert {f.rule_id for f in fired} == {"warn-r", "alarm-r"}
    # Alarm pages through during quiet hours; warn is buffered.
    assert [ev.rule_id for ev in rec.sent] == ["alarm-r"]


def test_per_device_metric_resolves_and_fires():
    e, rec = _engine([_rule(rid="drift", metric="devices.battery_0.cell_drift_v",
                            op="gt", threshold=0.05)])
    snap = {"devices": [{"label": "battery_0", "kind": "smart_battery",
                         "latest": {"cell_drift_v": 0.08}}]}
    fired = asyncio.run(e.evaluate(snap))
    assert [f.rule_id for f in fired] == ["drift"] and len(rec.sent) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL ALERT-ENGINE TESTS PASS")
