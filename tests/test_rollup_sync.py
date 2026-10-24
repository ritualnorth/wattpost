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

def test_happy_path_advances_watermark_and_is_caught_up():
    async def go():
        st = await _store(seed_minutes=5)
        cloud = FakeCloud()
        res = await _sync(st, cloud).run_once(now=T0 + 400)
        assert res.error is None and res.caught_up and res.sent_rows == 10
        assert (await st.kv_get(rs.WATERMARK_KEY))[0] == str(T0 + 240)
        b = cloud.batches[0]
        assert b["devices"][0]["device"] == "bank" and b["latest"]
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
