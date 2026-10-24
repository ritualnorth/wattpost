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


class FakeWeather:
    lat, lon = 51.5, -0.1


class FakeCfg:
    weather = None
    devices = []


class CfgScheduler(FakeScheduler):
    def __init__(self, cfg):
        super().__init__()
        self.config = cfg


def test_forecast_and_weather_views_published_even_when_unconfigured():
    async def go():
        st = await _setup()
        pub = ViewPublisher(st, CfgScheduler(FakeCfg()), analytics_every_s=900)
        first = await pub.due(now=1000)
        assert first["forecast/pv"] == {"points": [], "fetched_at": None, "provider": None}
        assert first["weather/current"] == {"provider": None, "fetched_at": None}
        assert first["forecast/accuracy"]["ok"] is False
        for b in (300, 900, 3600, 21600):
            h = first[f"weather/history/b{b}"]
            assert h["available"] is False and h["configured"] is False
        await st.close()
    asyncio.run(go())


def test_forecast_and_weather_use_cached_blobs_and_cadence():
    async def go():
        import json
        from solar_monitor.forecast.service import CACHE_KEY as FKEY
        from solar_monitor.weather.service import CACHE_KEY as WKEY
        st = await _setup()
        await st.kv_set(FKEY, json.dumps({"provider": "solcast", "points": [{"ts": 1, "w": 5}]}))
        await st.kv_set(WKEY, json.dumps({"provider": "openmeteo", "temp_c": 12.0}))
        pub = ViewPublisher(st, CfgScheduler(FakeCfg()), analytics_every_s=900)
        first = await pub.due(now=1000)
        assert first["forecast/pv"]["points"] == [{"ts": 1, "w": 5}]
        assert first["weather/current"]["temp_c"] == 12.0
        pub.confirm(first)
        # weather/current is due after 10 min, forecast only after 30; unchanged -> not re-sent
        assert "weather/current" not in await pub.due(now=1000 + 11 * 60)
        await st.kv_set(WKEY, json.dumps({"provider": "openmeteo", "temp_c": 13.0}))
        later = await pub.due(now=1000 + 22 * 60)
        assert later["weather/current"]["temp_c"] == 13.0
        assert "forecast/pv" not in later
        await st.close()
    asyncio.run(go())


def test_weather_history_built_per_range_when_configured(monkeypatch):
    async def go():
        from solar_monitor.cloud import views as V
        calls = []

        async def fake(config, store, since=None, until=None, bucket=None):
            calls.append((since, bucket))
            return {"configured": True, "available": True, "series": {"ts": [1], "cloud_cover_pct": [5.0]}}
        monkeypatch.setattr(V, "build_weather_history", fake)
        cfg = FakeCfg(); cfg.weather = FakeWeather()
        st = await _setup()
        pub = ViewPublisher(st, CfgScheduler(cfg), analytics_every_s=900)
        first = await pub.due(now=1000)
        assert sorted(b for _, b in calls) == [300, 900, 3600, 21600]
        assert dict((b, s is None) for s, b in calls)[300] is True      # today: midnight default
        assert first["weather/history/b3600"]["available"] is True
        await st.close()
    asyncio.run(go())


def test_view_batch_never_exceeds_cloud_limit():
    async def go():
        from solar_monitor.cloud.views import MAX_VIEWS_PER_BATCH
        st = await _setup()
        pub = ViewPublisher(st, CfgScheduler(FakeCfg()), analytics_every_s=900)
        pub._device_views = lambda: _many()
        async def _many():
            async def b(): return {"x": 1}
            return {f"device/d{i}/lifetime": b for i in range(40)}
        first = await pub.due(now=1000)
        assert len(first) == MAX_VIEWS_PER_BATCH and "snapshot" in first
        pub.confirm(first)
        second = await pub.due(now=1001)
        assert any(k.startswith("device/") for k in second)    # the overflow follows
        await st.close()
    asyncio.run(go())


def test_a_moving_wall_clock_window_does_not_look_like_a_change():
    """The heatmap's since_ts/until_ts tick every second; the hash must ignore
    them or the view is re-uploaded every interval (and this suite flakes
    whenever a second boundary falls between two builds)."""
    from solar_monitor.cloud.views import _stable
    a = {"since_ts": 1, "until_ts": 100, "cells": [1, 2]}
    b = {"since_ts": 2, "until_ts": 101, "cells": [1, 2]}
    assert _stable(a) == _stable(b)
    assert _stable(a) != _stable({**b, "cells": [1, 3]})
