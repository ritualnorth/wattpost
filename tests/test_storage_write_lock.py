"""Concurrency test for the Store write lock.

record_poll and rollup_and_purge run as separate asyncio tasks on one
aiosqlite connection, and commit() is connection-global — so their
multi-statement transactions must be serialised (Store._write_lock) or one
writer's commit can flush the other's half-finished transaction. This drives
both concurrently and asserts:
  (a) mutual exclusion — the two writers' lock-protected critical sections
      never overlap (max observed concurrency is 1), and
  (b) no deadlock — the whole batch completes well under a timeout.
"""
import asyncio
import os
import tempfile

from solar_monitor.storage.sqlite import Store


def _poll(p: int) -> dict:
    return {
        "timestamp": "2026-06-14T00:00:00Z",
        "elapsed_seconds": 0.05,
        "errors": [],
        "devices": {
            "solar_1": {
                "power_w": float(p), "voltage_v": 12.5,
                "_vendor": "test", "_kind": "mppt",
            },
        },
    }


async def _run() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(path)
    await store.open()
    try:
        # Seed a few rows so rollup_and_purge has something to fold.
        for i in range(5):
            await store.record_poll(_poll(i), ts_override=1_700_000_000 + i)

        # Instrument the lock-protected inner methods to measure how many run
        # at once. They're only reached while the lock is held, so without a
        # working lock the sleep(0) yield below would let a second task enter
        # and push max > 1.
        state = {"active": 0, "max": 0}
        orig_poll = store._record_poll_locked
        orig_roll = store._rollup_and_purge_locked

        async def _tracked(orig, *a, **k):
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            try:
                await asyncio.sleep(0)  # yield — an UNLOCKED pair would overlap here
                return await orig(*a, **k)
            finally:
                state["active"] -= 1

        store._record_poll_locked = lambda *a, **k: _tracked(orig_poll, *a, **k)
        store._rollup_and_purge_locked = lambda *a, **k: _tracked(orig_roll, *a, **k)

        tasks = (
            [store.record_poll(_poll(p)) for p in range(15)]
            + [store.rollup_and_purge() for _ in range(5)]
        )
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)

        assert state["max"] == 1, (
            f"writers' critical sections overlapped (max concurrency "
            f"{state['max']}) — the write lock isn't serialising them"
        )
        # Connection still healthy afterwards.
        await store.rollup_and_purge()
        print(f"PASS write-lock: {len(tasks)} concurrent writers serialised "
              f"(max concurrency {state['max']}), no deadlock")
    finally:
        await store.close()
        try:
            os.unlink(path)
        except OSError:
            pass


def test_write_lock_serialises_writers():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
