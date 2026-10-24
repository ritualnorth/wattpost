"""Rollup sync: what gets shipped, when the watermark moves, how it recovers.

The cloud is simulated with httpx.MockTransport, so these run offline and
exercise the real gzip body, the real watermark persistence and the real
Store queries.
"""
import asyncio
import gzip
import json
import os
import random
import tempfile

import httpx

from solar_monitor.cloud import rollup_sync as rs
from solar_monitor.storage.sqlite import Store

T0 = 1_700_000_040          # minute-aligned (multiple of 60)
assert T0 % 60 == 0


def _poll(p: float, soc: float = 60.0) -> dict:
    return {
        "timestamp": "x", "elapsed_seconds": 0.01, "errors": [],
        "devices": {"bank": {"power_w": p, "soc_pct": soc,
                             "_vendor": "internal", "_kind": "bank"}},
    }


def _run(coro):
    return asyncio.run(coro)


async def _store(seed_minutes=5, polls_per_min=4):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Store(path)
    await st.open()
    for m in range(seed_minutes):
        for k in range(polls_per_min):
            await st.record_poll(_poll(100.0 * (m + 1) + k), ts_override=T0 + m * 60 + k * 15)
    return st


class FakeCloud:
    """Records every batch; can be told to fail or to lose the response."""

    def __init__(self):
        self.batches: list[dict] = []
        self.fail_next = 0
        self.lose_response_next = 0
        self.ack_override = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(503, text="down")
        assert request.headers["content-encoding"] == "gzip"
        assert request.headers["authorization"] == "Bearer tok"
        assert request.headers["x-test-sig"] == "ok"
        body = json.loads(gzip.decompress(request.content))
        self.batches.append(body)
        if self.lose_response_next:
            self.lose_response_next -= 1
            raise httpx.ReadTimeout("response lost", request=request)
        top = max((r[0] for r in body["rollups"]), default=None)
        return httpx.Response(200, json={"stored": len(body["rollups"]),
                                         "acked_through": self.ack_override or top,
                                         "server_time": 0})


def _sync(store, cloud, backfill_h=1):
    return rs.RollupSync(
        store=store, endpoint=lambda: "https://cloud.test", bearer=lambda: "tok",
        sign=lambda raw: {"x-test-sig": "ok"}, backfill_hours=lambda: backfill_h,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler)),
    )


# ---- Store.closed_rollups_after -------------------------------------------

def test_open_minute_is_never_returned():
    async def go():
        st = await _store(seed_minutes=3)
        # 'now' is inside minute 2 (T0+120..T0+180): minute 2 is still open.
        rows = await st.closed_rollups_after(T0 - 60, now=T0 + 130)
        assert {r[0] for r in rows} == {T0, T0 + 60}
        # One second after minute 2 closes it appears, complete (4 samples).
        rows = await st.closed_rollups_after(T0 - 60, now=T0 + 181)
        assert {r[0] for r in rows} == {T0, T0 + 60, T0 + 120}
        pw = [r for r in rows if r[0] == T0 + 120 and r[2] == "power_w"][0]
        assert pw[6] == 4 and pw[4] == 300.0 and pw[5] == 303.0
        await st.close()
    _run(go())


def test_partial_samples_1min_row_is_not_trusted():
    """rollup_and_purge may have rolled a still-open minute as a partial row.
    The sync must read raw samples for recent buckets, not that row."""
    async def go():
        st = await _store(seed_minutes=1, polls_per_min=2)
        await st.rollup_and_purge(now=T0 + 20)         # rolls minute 0 with n=2
        for k in range(2, 4):                           # two more samples land
            await st.record_poll(_poll(100.0 + k), ts_override=T0 + k * 15)
        rows = await st.closed_rollups_after(T0 - 60, now=T0 + 61)
        pw = [r for r in rows if r[2] == "power_w"][0]
        assert pw[6] == 4, "must aggregate all 4 raw samples, not the stale n=2 row"
        await st.close()
    _run(go())


def test_old_backlog_falls_back_to_1min_table():
    async def go():
        st = await _store(seed_minutes=2)
        await st.rollup_and_purge(now=T0 + 120)
        # Pretend 8 days pass: raw is purged (7 d retention), 1-min survives (30 d).
        later = T0 + 8 * 86400
        await st.rollup_and_purge(now=later)
        async with st._db.execute("SELECT COUNT(*) FROM samples") as c:
            assert (await c.fetchone())[0] == 0
        rows = await st.closed_rollups_after(T0 - 60, now=later)
        assert {r[0] for r in rows} == {T0, T0 + 60}
        await st.close()
    _run(go())


def test_limit_never_splits_a_bucket():
    async def go():
        st = await _store(seed_minutes=5)            # 2 metrics x 5 buckets = 10 rows
        rows = await st.closed_rollups_after(T0 - 60, now=T0 + 600, limit=5)
        assert len(rows) == 4 and {r[0] for r in rows} == {T0, T0 + 60}
        top = rows[-1][0]
        rest = await st.closed_rollups_after(top, now=T0 + 600, limit=100)
        assert len(rows) + len(rest) == 10           # nothing lost, nothing doubled
        await st.close()
    _run(go())


# ---- RollupSync ------------------------------------------------------------

def test_inventory_is_sent_once_then_only_when_it_changes():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        sync = _sync(st, cloud)                       # one syncer: it remembers what the cloud has
        await sync.run_once(now=T0 + 400)
        assert cloud.batches[0]["devices"] and cloud.batches[0]["latest"] == []
        await sync.run_once(now=T0 + 460)
        assert cloud.batches[-1]["devices"] == []     # unchanged, so not resent
        sync._inventory_sig = None                    # what an identity change does
        await sync.run_once(now=T0 + 520)
        assert cloud.batches[-1]["devices"]
    asyncio.run(go())


def test_happy_path_advances_watermark_and_is_caught_up():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        res = await _sync(st, cloud).run_once(now=T0 + 400)
        assert res.error is None and res.caught_up and res.sent_rows == 10
        assert (await st.kv_get(rs.WATERMARK_KEY))[0] == str(T0 + 240)
        b = cloud.batches[0]
        assert b["devices"][0]["device"] == "bank" and b["latest"] == []
        # Nothing new: next run sends inventory only, zero rollups.
        res = await _sync(st, cloud).run_once(now=T0 + 460)
        assert res.sent_rows == 0 and res.caught_up
        assert cloud.batches[-1]["rollups"] == []
        await st.close()
    _run(go())


def test_failure_keeps_watermark_and_retry_resends_same_rows():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        sync = _sync(st, cloud)
        cloud.fail_next = 1
        res = await sync.run_once(now=T0 + 400)
        assert res.error and sync.failures == 1
        assert await st.kv_get(rs.WATERMARK_KEY) is None
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None and sync.failures == 0 and res.sent_rows == 10
        await st.close()
    _run(go())


def test_lost_response_means_resend_not_loss():
    """Cloud stored the batch but we never heard back. Watermark must not
    move, so the same rows go again (the cloud's upsert makes that a no-op)."""
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        sync = _sync(st, cloud)
        cloud.lose_response_next = 1
        res = await sync.run_once(now=T0 + 400)
        assert res.error and await st.kv_get(rs.WATERMARK_KEY) is None
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None
        first = [r for r in cloud.batches[0]["rollups"]]
        again = [r for r in cloud.batches[1]["rollups"]]
        assert first == again and len(first) == 10
        await st.close()
    _run(go())


def test_short_ack_does_not_advance_watermark():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        cloud.ack_override = T0            # cloud claims it only stored the first bucket
        res = await _sync(st, cloud).run_once(now=T0 + 400)
        assert res.error and "acked" in res.error
        assert await st.kv_get(rs.WATERMARK_KEY) is None
        await st.close()
    _run(go())


def test_backlog_drains_in_batches(monkeypatch=None):
    async def go():
        st = await _store(seed_minutes=5)
        old = rs.BATCH_ROWS
        rs.BATCH_ROWS = 4                   # 2 buckets per batch
        try:
            cloud = FakeCloud()
            res = await _sync(st, cloud).run_once(now=T0 + 400)
        finally:
            rs.BATCH_ROWS = old
        assert res.error is None and res.caught_up
        sent = [r for b in cloud.batches for r in b["rollups"]]
        assert len(sent) == 10 and len({(r[0], r[2]) for r in sent}) == 10
        assert len(cloud.batches) >= 3
        await st.close()
    _run(go())


def test_first_sync_respects_backfill_window():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        # 'now' is 3 h after the data; with a 1 h backfill window none of it is sent.
        res = await _sync(st, cloud, backfill_h=1).run_once(now=T0 + 3 * 3600)
        assert res.sent_rows == 0
        await st.close()
    _run(go())


def test_backoff_is_bounded_and_jittered():
    rng = random.Random(1)
    for n in range(1, 40):
        d = rs.backoff_delay(n, rng)
        assert 0 <= d <= rs.BACKOFF_CAP_S
    ceilings = [max(rs.backoff_delay(n, random.Random(i)) for i in range(200)) for n in (1, 3, 6)]
    assert ceilings[0] < ceilings[1] < ceilings[2]
    assert len({rs.backoff_delay(5, random.Random(i)) for i in range(20)}) > 10


# ---- live lease -------------------------------------------------------------

def test_lease_speeds_up_then_expires():
    async def go():
        st = await _store(seed_minutes=2)
        cloud = FakeCloud()
        sync = _sync(st, cloud)
        assert sync.live_interval() is None
        sync._note_lease({"live": {"ttl_s": 60, "interval_s": 5}})
        assert sync.live_interval() == 5
        sync._note_lease({"live": {"ttl_s": 60, "interval_s": 0.1}})     # floor
        assert sync.live_interval() == rs.LIVE_MIN_INTERVAL_S
        sync._note_lease({})                                              # cloud stops granting
        assert sync.live_interval() is None
        sync._note_lease({"live": {"ttl_s": -1, "interval_s": 5}})
        assert sync.live_interval() is None
        sync._note_lease({"live": {"ttl_s": "abc", "interval_s": 5}})     # junk from the cloud
        assert sync.live_interval() is None
        sync._note_lease({"live": {"ttl_s": 10**9, "interval_s": 5}})     # ttl is capped
        assert sync._live_until_mono - __import__("time").monotonic() <= rs.LIVE_MAX_TTL_S + 1
        await st.close()
    _run(go())


def test_loop_pushes_at_lease_interval_and_returns_to_normal():
    async def go():
        st = await _store(seed_minutes=2)
        posts = []

        def handler(req):
            body = json.loads(gzip.decompress(req.content))
            posts.append(body)
            lease = {"live": {"ttl_s": 30, "interval_s": 2}} if len(posts) <= 2 else {}
            top = max((r[0] for r in body["rollups"]), default=None)
            return httpx.Response(200, json={"acked_through": top, **lease})

        sync = rs.RollupSync(
            store=st, endpoint=lambda: "https://c", bearer=lambda: "t", sign=lambda b: {},
            backfill_hours=lambda: 1,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        waits = []

        async def fake_wait(ev, t):
            waits.append(t)
            return len(waits) >= 4           # stop after four sleeps

        await sync.loop(asyncio.Event(), lambda: 300, sleep=fake_wait)
        assert waits[0] == 2 and waits[1] == 2        # leased: short sleeps
        assert waits[-1] == 300                       # lease lapsed: normal cadence
        await st.close()
    _run(go())


# ---- settings switch ---------------------------------------------------------

def test_uploads_switched_off_send_nothing_and_resume_when_switched_on():
    async def go():
        st = await _store(seed_minutes=2)
        cloud = FakeCloud()
        flag = {"on": False}
        sync = rs.RollupSync(
            store=st, endpoint=lambda: "https://c", bearer=lambda: "tok", sign=lambda b: {"x-test-sig": "ok"},
            backfill_hours=lambda: 1, enabled=lambda: flag["on"],
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler)))
        waits = []

        async def fake_wait(ev, t):
            waits.append(t)
            if len(waits) == 2:
                flag["on"] = True            # user ticks the box in Settings
            return len(waits) >= 4

        await sync.loop(asyncio.Event(), lambda: 300, sleep=fake_wait)
        assert waits[:2] == [rs.SWITCH_POLL_S, rs.SWITCH_POLL_S]   # off: only polling the switch
        assert len(cloud.batches) >= 1       # on: uploads resume, no restart needed
        await st.close()
    _run(go())


def test_switching_uploads_back_on_wakes_a_sleeping_loop_quickly():
    """The loop sleeps for the whole upload period (minutes). It must notice the
    Settings switch flipping without waiting that period out."""
    import time as _t

    async def go():
        st = await _store(seed_minutes=2)
        cloud = FakeCloud()
        flag = {"on": False}
        old = rs.SWITCH_POLL_S
        rs.SWITCH_POLL_S = 0.05
        try:
            sync = rs.RollupSync(
                store=st, endpoint=lambda: "https://c", bearer=lambda: "tok",
                sign=lambda b: {"x-test-sig": "ok"}, backfill_hours=lambda: 1,
                enabled=lambda: flag["on"],
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler)))
            stop = asyncio.Event()
            task = asyncio.create_task(sync.loop(stop, lambda: 300))     # 5 min period
            await asyncio.sleep(0.2)
            assert cloud.batches == []                                    # off: nothing sent
            t0 = _t.monotonic()
            flag["on"] = True
            while not cloud.batches and _t.monotonic() - t0 < 3:
                await asyncio.sleep(0.05)
            assert cloud.batches, "never resumed"
            assert _t.monotonic() - t0 < 1.5
            # and switching off while it sleeps the long period just stops further sends
            sent = len(cloud.batches)
            flag["on"] = False
            await asyncio.sleep(0.3)
            stop.set()
            await asyncio.wait_for(task, 2)
            assert len(cloud.batches) <= sent + 1
        finally:
            rs.SWITCH_POLL_S = old
        await st.close()
    _run(go())


# ---- hostile / broken cloud replies -------------------------------------------

def test_lease_interval_is_clamped_and_non_finite_values_are_rejected():
    async def go():
        st = await _store(seed_minutes=2)
        sync = _sync(st, FakeCloud())
        sync._note_lease({"live": {"ttl_s": 60, "interval_s": 10**9}})
        assert sync.live_interval() == rs.LIVE_MAX_INTERVAL_S
        for bad in (float("nan"), float("inf"), float("-inf")):
            sync._note_lease({"live": {"ttl_s": 60, "interval_s": bad}})
            assert sync.live_interval() is None, bad
            sync._note_lease({"live": {"ttl_s": bad, "interval_s": 5}})
            assert sync.live_interval() is None, bad
        await st.close()
    _run(go())


def test_loop_never_sleeps_past_the_lease():
    async def go():
        st = await _store(seed_minutes=2)

        def handler(req):
            body = json.loads(gzip.decompress(req.content))
            top = max((r[0] for r in body["rollups"]), default=None)
            return httpx.Response(200, json={
                "acked_through": top, "live": {"ttl_s": 5, "interval_s": 10**9}})

        sync = rs.RollupSync(
            store=st, endpoint=lambda: "https://c", bearer=lambda: "t", sign=lambda b: {},
            backfill_hours=lambda: 1,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        waits = []

        async def fake_wait(ev, t):
            waits.append(t)
            return True

        await sync.loop(asyncio.Event(), lambda: 300, sleep=fake_wait)
        assert waits and waits[0] <= 5.0 and waits[0] <= rs.LIVE_MAX_INTERVAL_S
        await st.close()
    _run(go())


def test_ack_beyond_the_batch_is_an_error_and_never_persisted():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        cloud.ack_override = T0 + 10**9        # cloud claims far more than was sent
        res = await _sync(st, cloud).run_once(now=T0 + 400)
        assert res.error and "acked" in res.error
        assert await st.kv_get(rs.WATERMARK_KEY) is None
        for junk in ("12", 1.5, True, [1]):
            cloud.ack_override = junk
            res = await _sync(st, cloud).run_once(now=T0 + 400)
            assert res.error, junk
            assert await st.kv_get(rs.WATERMARK_KEY) is None
        await st.close()
    _run(go())


def test_future_watermark_does_not_silence_uploads():
    async def go():
        st = await _store(seed_minutes=5)
        await st.kv_set(rs.WATERMARK_KEY, str(T0 + 10 * 365 * 86400))
        cloud = FakeCloud()
        sync = _sync(st, cloud)
        assert await sync._watermark(T0 + 400) <= T0 + 400
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None and res.sent_rows == 10
        await st.close()
    _run(go())


# ---- windowed aggregation == the old single-query result -------------------------

async def _reference_closed_rollups_after(st, after_ts, *, now, limit):
    """The pre-windowing implementation, verbatim: one GROUP BY over the whole range."""
    newest_closed = (now // 60) * 60 - 60
    start = (int(after_ts) // 60) * 60 + 60
    if start > newest_closed:
        return []
    raw_floor = ((now - st._retention_raw_s) // 60) * 60 + 60
    rows = []
    if start < raw_floor:
        async with st._db.execute(
            "SELECT bucket_ts, device, metric, avg, min, max, n "
            "FROM samples_1min WHERE bucket_ts >= ? AND bucket_ts < ? "
            "ORDER BY bucket_ts, device, metric LIMIT ?",
            (start, min(raw_floor, newest_closed + 60), limit + 1),
        ) as cur:
            rows.extend([(int(a), b, c, float(d), float(e), float(f), int(g))
                         async for a, b, c, d, e, f, g in cur])
    if len(rows) <= limit and newest_closed >= raw_floor:
        async with st._db.execute(
            "SELECT (ts / 60) * 60 AS b, device, metric, "
            "       AVG(value), MIN(value), MAX(value), COUNT(*) "
            "FROM samples WHERE ts >= ? AND ts < ? "
            "GROUP BY b, device, metric ORDER BY b, device, metric LIMIT ?",
            (max(start, raw_floor), newest_closed + 60, limit + 1 - len(rows)),
        ) as cur:
            rows.extend([(int(a), b, c, float(d), float(e), float(f), int(g))
                         async for a, b, c, d, e, f, g in cur])
    if len(rows) > limit:
        last = rows[limit][0]
        rows = [r for r in rows[:limit] if r[0] < last]
        if not rows:
            async with st._db.execute(
                "SELECT (ts / 60) * 60 AS b, device, metric, "
                "       AVG(value), MIN(value), MAX(value), COUNT(*) "
                "FROM samples WHERE ts >= ? AND ts < ? "
                "GROUP BY b, device, metric", (last, last + 60),
            ) as cur:
                rows = [(int(a), b, c, float(d), float(e), float(f), int(g))
                        async for a, b, c, d, e, f, g in cur]
    return rows


async def _bulk_store(days, metrics, step=5, t_start=T0):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Store(path)
    await st.open()
    n = days * 86400 // step
    names = [f"m{i:03d}" for i in range(metrics)]
    batch = []
    for k in range(n):
        ts = t_start + k * step
        for i, m in enumerate(names):
            batch.append((ts, "bank", m, float((k * 7 + i) % 101)))
        if len(batch) >= 100_000:
            await st._db.executemany("INSERT INTO samples VALUES (?,?,?,?)", batch)
            batch = []
    if batch:
        await st._db.executemany("INSERT INTO samples VALUES (?,?,?,?)", batch)
    await st._db.commit()
    return st


def test_windowed_result_matches_the_single_query_result_over_days():
    async def go():
        st = await _bulk_store(days=3, metrics=3, step=15)
        # make the window much narrower than the data so many windows are crossed
        st.ROLLUP_WINDOW_S = 600
        rng = random.Random(7)
        now = T0 + 3 * 86400 + 1234
        # a 1-min-table region too: roll up the first day and purge its raw rows
        await st.rollup_and_purge(now=T0 + 86400)
        cases = [(T0 - 60, 5000), (T0 - 60, 7), (T0 - 60, 1), (T0 + 86400, 100)]
        cases += [(T0 + rng.randrange(0, 3 * 86400), rng.choice([1, 2, 3, 50, 999, 5000]))
                  for _ in range(25)]
        for after, limit in cases:
            want = await _reference_closed_rollups_after(st, after, now=now, limit=limit)
            got = await st.closed_rollups_after(after, now=now, limit=limit)
            assert got == want, (after, limit)
        # draining with the watermark protocol yields every bucket exactly once
        seen, wm = [], T0 - 60
        for _ in range(10_000):
            b = await st.closed_rollups_after(wm, now=now, limit=500)
            if not b:
                break
            seen += b
            wm = b[-1][0]
        assert len({(r[0], r[1], r[2]) for r in seen}) == len(seen)
        full = await _reference_closed_rollups_after(st, T0 - 60, now=now, limit=10**9)
        assert seen == full
        await st.close()
    _run(go())


def test_first_batch_is_fast_with_a_day_of_5s_data_and_100_metrics():
    import time as _t

    async def go():
        st = await _bulk_store(days=1, metrics=100, step=5)
        now = T0 + 86400 + 120
        t = _t.perf_counter()
        rows = await st.closed_rollups_after(T0 - 60, now=now, limit=5000)
        took = _t.perf_counter() - t
        assert len(rows) == 5000
        assert took < 1.0, f"first batch took {took:.2f}s"
        await st.close()
    _run(go())


# ---- strict JSON / NUL scrubbing -------------------------------------------------

def test_scrub_replaces_non_finite_and_strips_nul():
    d = rs.scrub({"a\x00b": [float("nan"), float("inf"), 1.5, "x\x00y", ("t", float("-inf"))]})
    assert d == {"ab": [None, None, 1.5, "xy", ["t", None]]}


def test_post_body_is_strict_json_even_with_nan_in_views():
    async def go():
        st = await _store(seed_minutes=2)
        seen = []

        def handler(req):
            seen.append(gzip.decompress(req.content))
            body = json.loads(seen[-1])
            top = max((r[0] for r in body["rollups"]), default=None)
            return httpx.Response(200, json={"acked_through": top})

        class V:
            async def due(self, live=False):
                return {"snapshot": {"x": float("nan"), "y": float("inf"), "s": "a\x00b"}}
            def confirm(self, names): pass

        sync = rs.RollupSync(
            store=st, endpoint=lambda: "https://c", bearer=lambda: "t", sign=lambda b: {},
            backfill_hours=lambda: 1, views=V(),
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None, res.error
        raw = seen[0]
        assert b"NaN" not in raw and b"Infinity" not in raw and b"\\u0000" not in raw
        assert json.loads(raw)["views"]["snapshot"] == {"x": None, "y": None, "s": "ab"}
        await st.close()
    _run(go())


def test_non_finite_rollup_rows_are_dropped_and_do_not_wedge_the_watermark():
    async def go():
        st = await _store(seed_minutes=3)
        await st._db.execute("UPDATE samples SET value = 1e999 WHERE ts = ? AND metric = 'power_w'",
                             (T0 + 2 * 60,))
        await st._db.execute("UPDATE samples SET value = 1e999 WHERE ts = ? AND metric = 'power_w'",
                             (T0 + 2 * 60 + 15,))
        await st._db.commit()
        cloud = FakeCloud()
        res = await _sync(st, cloud).run_once(now=T0 + 400)
        assert res.error is None, res.error
        sent = [r for b in cloud.batches for r in b["rollups"]]
        assert all(abs(x) < 1e30 for r in sent for x in r[3:6])
        assert res.skipped_rows >= 1
        assert int((await st.kv_get(rs.WATERMARK_KEY))[0]) == T0 + 2 * 60   # moved past the bad bucket
        await st.close()
    _run(go())


# ---- poison batches ---------------------------------------------------------------

def _poison_cloud(bad_bucket, status=400, down=False):
    cloud = FakeCloud()
    cloud.received = []

    def handler(req):
        body = json.loads(gzip.decompress(req.content))
        if down:
            return httpx.Response(503, text="down")
        if any(r[0] == bad_bucket and r[2] == "power_w" for r in body["rollups"]):
            return httpx.Response(status, text="bad row")
        cloud.received += body["rollups"]
        top = max((r[0] for r in body["rollups"]), default=None)
        return httpx.Response(200, json={"acked_through": top, "rejected": 0})
    cloud.handler = handler
    return cloud


def test_persistent_4xx_batch_is_bisected_and_the_watermark_moves_past_the_poison_row():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = _poison_cloud(T0 + 120)
        sync = _sync(st, cloud)
        for _ in range(rs.POISON_THRESHOLD - 1):
            res = await sync.run_once(now=T0 + 400)
            assert res.error and "HTTP 400" in res.error
            assert await st.kv_get(rs.WATERMARK_KEY) is None      # still retrying the same batch
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None, res.error
        assert res.skipped_rows == 1
        keys = {(r[0], r[2]) for r in cloud.received}
        assert len(keys) == 9 and (T0 + 120, "power_w") not in keys   # everything else arrived
        assert int((await st.kv_get(rs.WATERMARK_KEY))[0]) == T0 + 4 * 60
        # next run is clean and does not resend
        before = len(cloud.received)
        res = await sync.run_once(now=T0 + 400)
        assert res.error is None and len(cloud.received) == before
        await st.close()
    _run(go())


def test_outage_is_never_mistaken_for_poison():
    async def go():
        st = await _store(seed_minutes=5)
        for status, down in ((503, True),):
            cloud = _poison_cloud(T0 + 120, down=down)
            sync = _sync(st, cloud)
            for _ in range(rs.POISON_THRESHOLD + 3):
                res = await sync.run_once(now=T0 + 400)
                assert res.error and res.skipped_rows == 0
            assert await st.kv_get(rs.WATERMARK_KEY) is None
        await st.close()
    _run(go())


def test_auth_and_rate_limit_statuses_are_never_poison():
    async def go():
        st = await _store(seed_minutes=3)
        for status in (401, 408, 429):
            cloud = _poison_cloud(T0 + 60, status=status)
            sync = _sync(st, cloud)
            for _ in range(rs.POISON_THRESHOLD + 2):
                res = await sync.run_once(now=T0 + 400)
                assert res.error and res.skipped_rows == 0
            assert await st.kv_get(rs.WATERMARK_KEY) is None
        await st.close()
    _run(go())


# ---- watermark identity ------------------------------------------------------------

def test_watermark_is_reset_when_appliance_or_endpoint_changes():
    async def go():
        st = await _store(seed_minutes=3)
        cloud = FakeCloud()
        ident = {"v": "7|https://a.example"}

        def mk():
            return rs.RollupSync(
                store=st, endpoint=lambda: "https://c", bearer=lambda: "tok",
                sign=lambda raw: {"x-test-sig": "ok"}, backfill_hours=lambda: 1,
                identity=lambda: ident["v"],
                client_factory=lambda: httpx.AsyncClient(
                    transport=httpx.MockTransport(cloud.handler)))

        await mk().run_once(now=T0 + 400)
        n1 = sum(len(b["rollups"]) for b in cloud.batches)
        assert n1 == 6
        await mk().run_once(now=T0 + 400)                       # same identity: nothing resent
        assert sum(len(b["rollups"]) for b in cloud.batches) == n1
        ident["v"] = "8|https://a.example"                       # re-paired as another appliance
        await mk().run_once(now=T0 + 400)
        assert sum(len(b["rollups"]) for b in cloud.batches) == 2 * n1
        ident["v"] = "8|https://b.example"                       # same id, different endpoint
        await mk().run_once(now=T0 + 400)
        assert sum(len(b["rollups"]) for b in cloud.batches) == 3 * n1
        await st.close()
    _run(go())


def test_legacy_watermark_without_identity_is_kept():
    async def go():
        st = await _store(seed_minutes=3)
        await st.kv_set(rs.WATERMARK_KEY, str(T0 + 2 * 60))
        cloud = FakeCloud()
        sync = rs.RollupSync(
            store=st, endpoint=lambda: "https://c", bearer=lambda: "tok",
            sign=lambda raw: {"x-test-sig": "ok"}, backfill_hours=lambda: 1,
            identity=lambda: "7|https://a",
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler)))
        await sync.run_once(now=T0 + 400)
        assert sum(len(b["rollups"]) for b in cloud.batches) == 0     # nothing newer than wm
        assert (await st.kv_get(rs.IDENTITY_KEY))[0] == "7|https://a"
        await st.close()
    _run(go())


# ---- views: live cadence, timeout, one build per run ---------------------------------

def test_live_mode_still_builds_due_analytics_views():
    from solar_monitor.cloud.views import ViewPublisher

    class Sched:
        n = 0
        async def build_snapshot(self):
            Sched.n += 1
            return {"type": "snapshot", "ts": Sched.n}

    async def go():
        st = await _store(seed_minutes=2)
        pub = ViewPublisher(st, Sched(), analytics_every_s=900)
        first = await pub.due(now=1000, live=True)
        assert "snapshot" in first and "battery-health" in first and "load-heatmap" in first
        pub.confirm(first)
        again = await pub.due(now=1010, live=True)
        assert list(again) == ["snapshot"]                 # analytics keep their normal cadence
        later = await pub.due(now=1000 + 901, live=True)
        assert "snapshot" in later                         # and come due again while live
        await st.close()
    _run(go())


def test_a_slow_view_builder_times_out_and_the_rest_still_go():
    from solar_monitor.cloud import views as V

    class Sched:
        async def build_snapshot(self):
            return {"type": "snapshot", "ts": 1}

    async def go():
        st = await _store(seed_minutes=2)
        pub = V.ViewPublisher(st, Sched(), analytics_every_s=900, builder_timeout_s=0.1)

        async def hang(store):
            await asyncio.sleep(30)
        orig = V.viewmodels.battery_health
        V.viewmodels.battery_health = hang
        try:
            import time as _t
            t = _t.perf_counter()
            out = await pub.due(now=1000)
            assert _t.perf_counter() - t < 5
        finally:
            V.viewmodels.battery_health = orig
        assert "battery-health" not in out and "snapshot" in out and "load-heatmap" in out
        await st.close()
    _run(go())


def test_views_are_built_once_per_run_not_per_batch():
    async def go():
        st = await _store(seed_minutes=5)
        builds = []

        class V:
            async def due(self, live=False):
                builds.append(1)
                return {"snapshot": {"ts": len(builds)}}
            def confirm(self, names): pass

        old = rs.BATCH_ROWS
        rs.BATCH_ROWS = 4
        try:
            cloud = FakeCloud()
            sync = rs.RollupSync(
                store=st, endpoint=lambda: "https://c", bearer=lambda: "tok",
                sign=lambda raw: {"x-test-sig": "ok"}, backfill_hours=lambda: 1, views=V(),
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler)))
            res = await sync.run_once(now=T0 + 400)
        finally:
            rs.BATCH_ROWS = old
        assert res.error is None and len(cloud.batches) >= 3
        assert len(builds) == 1
        assert "snapshot" in cloud.batches[0]["views"] and cloud.batches[-1]["views"] == {}
        await st.close()
    _run(go())
