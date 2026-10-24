"""ViewPublisher: which view-models go up, how often, and what survives errors."""
import asyncio
import os
import tempfile

from solar_monitor.cloud.views import ViewPublisher
from solar_monitor.storage.sqlite import Store

T0 = 1_700_000_040


def _poll(p, soc=60.0):
    return {"timestamp": "x", "elapsed_seconds": 0.01, "errors": [],
            "devices": {
                "battery_0": {"power_w": p, "soc_pct": soc, "voltage_v": 12.8,
                              "current_a": p / 12.8, "cycle_count": 5.0,
                              "_vendor": "renogy", "_kind": "smart_battery"},
                "rover": {"pv_power_w": p, "_vendor": "renogy", "_kind": "charge_controller"},
            }}


class FakeScheduler:
    def __init__(self):
        self.n = 0

    async def build_snapshot(self):
        self.n += 1
        return {"type": "snapshot", "ts": self.n, "devices": []}


async def _setup():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    st = Store(path); await st.open()
    for i in range(6):
        await st.record_poll(_poll(100.0 + i), ts_override=T0 + i * 15)
    return st


def test_first_publish_has_everything_then_only_snapshot():
    async def go():
        st = await _setup()
        pub = ViewPublisher(st, FakeScheduler(), analytics_every_s=900)
        first = await pub.due(now=1000)
        assert "snapshot" in first and "battery-health" in first and "load-heatmap" in first
        assert "device/battery_0/lifetime" in first and "device/rover/charger-stats" in first
        pub.confirm(first)
        second = await pub.due(now=1010)
        assert list(second) == ["snapshot"]          # analytics not due yet
        await st.close()
    asyncio.run(go())


def test_unchanged_analytics_are_not_resent_after_interval():
    async def go():
        st = await _setup()
        pub = ViewPublisher(st, FakeScheduler(), analytics_every_s=900)
        pub.confirm(await pub.due(now=1000))
        later = await pub.due(now=2000)               # interval passed, data identical
        stable = {k for k in later if k not in ("snapshot",)}
        # the lifetime/efficiency/heatmap content didn't change -> not re-sent
        assert "load-heatmap" not in stable and "device/battery_0/lifetime" not in stable
        await st.close()
    asyncio.run(go())


def test_changed_analytics_are_resent_and_unconfirmed_are_retried():
    async def go():
        st = await _setup()
        pub = ViewPublisher(st, FakeScheduler(), analytics_every_s=900)
        v = await pub.due(now=1000)                   # NOT confirmed (upload failed)
        again = await pub.due(now=1001)
        assert "battery-health" in again              # retried, not forgotten
        pub.confirm(again)
        for i in range(10):
            await st.record_poll(_poll(900.0 + i, soc=95.0), ts_override=T0 + 200 + i * 15)
        changed = await pub.due(now=3000)
        assert "device/battery_0/lifetime" in changed or "runtime-forecast" in changed
        await st.close()
    asyncio.run(go())


def test_one_broken_view_does_not_block_the_rest():
    async def go():
        st = await _setup()
        async def boom(*a, **k):
            raise RuntimeError("nope")
        st.load_heatmap = boom
        pub = ViewPublisher(st, FakeScheduler())
        out = await pub.due(now=1000)
        assert "load-heatmap" not in out and "snapshot" in out and "battery-health" in out
        await st.close()
    asyncio.run(go())
