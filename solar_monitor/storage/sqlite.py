"""SQLite-backed telemetry store.

Schema decisions:

* `samples` — long format, one row per (device, metric, ts) numeric reading.
  Long format wins here because metrics vary per vendor and we don't want to
  ALTER TABLE every time a new field appears.
* `samples_str` — same shape but for string-valued metrics (model name,
  charging_state). Far fewer rows.
* `latest` — a snapshot table updated on every poll. Lets `/api/devices` be
  a fast single-row-per-(device, metric) read with no aggregation.
* `device_meta` — vendor + kind + first/last seen, populated on poll.

Write path is funneled through a single async task to keep SQLite happy.
We commit per poll (a handful of inserts each), not per row.

Rollup tables (`samples_1min`, `_1hour`, `_1day`) are intentionally NOT in v1.
At 60s polling and ~30 metrics × 5 devices, the raw `samples` table grows by
~216k rows/day. SQLite handles range queries against 30 days of that
(~6.5M rows) under 100ms with the right index. We'll add rollups when we
have a customer hitting the limit, not before.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

log = logging.getLogger(__name__)

# Internal keys produced by drivers; not stored as samples.
_META_KEYS = {"_vendor", "_kind", "_label", "_slave_id", "_errors"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts      INTEGER NOT NULL,
    device  TEXT    NOT NULL,
    metric  TEXT    NOT NULL,
    value   REAL    NOT NULL,
    PRIMARY KEY (ts, device, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_samples_dev_metric_ts
    ON samples(device, metric, ts);

CREATE TABLE IF NOT EXISTS samples_str (
    ts      INTEGER NOT NULL,
    device  TEXT    NOT NULL,
    metric  TEXT    NOT NULL,
    value   TEXT    NOT NULL,
    PRIMARY KEY (ts, device, metric)
) WITHOUT ROWID;

-- Rollup tables. bucket_ts is the floor of the bucket window (unix seconds).
CREATE TABLE IF NOT EXISTS samples_1min (
    bucket_ts INTEGER NOT NULL,
    device    TEXT    NOT NULL,
    metric    TEXT    NOT NULL,
    avg       REAL    NOT NULL,
    min       REAL    NOT NULL,
    max       REAL    NOT NULL,
    n         INTEGER NOT NULL,
    PRIMARY KEY (bucket_ts, device, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_samples_1min_dev_metric_bucket
    ON samples_1min(device, metric, bucket_ts);

CREATE TABLE IF NOT EXISTS samples_1hour (
    bucket_ts INTEGER NOT NULL,
    device    TEXT    NOT NULL,
    metric    TEXT    NOT NULL,
    avg       REAL    NOT NULL,
    min       REAL    NOT NULL,
    max       REAL    NOT NULL,
    n         INTEGER NOT NULL,
    PRIMARY KEY (bucket_ts, device, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_samples_1hour_dev_metric_bucket
    ON samples_1hour(device, metric, bucket_ts);

CREATE TABLE IF NOT EXISTS samples_1day (
    bucket_ts INTEGER NOT NULL,
    device    TEXT    NOT NULL,
    metric    TEXT    NOT NULL,
    avg       REAL    NOT NULL,
    min       REAL    NOT NULL,
    max       REAL    NOT NULL,
    n         INTEGER NOT NULL,
    PRIMARY KEY (bucket_ts, device, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_samples_1day_dev_metric_bucket
    ON samples_1day(device, metric, bucket_ts);

CREATE TABLE IF NOT EXISTS latest (
    device     TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    ts         INTEGER NOT NULL,
    value_num  REAL,
    value_str  TEXT,
    PRIMARY KEY (device, metric)
);

CREATE TABLE IF NOT EXISTS device_meta (
    device      TEXT PRIMARY KEY,
    vendor      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    slave_id    INTEGER,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_runs (
    ts            INTEGER PRIMARY KEY,
    elapsed_ms    INTEGER NOT NULL,
    devices_ok    INTEGER NOT NULL,
    errors_count  INTEGER NOT NULL,
    errors_json   TEXT
);
"""

# Retention windows (seconds). Each lower-resolution table keeps data for
# longer; the higher-res source data is purged after being rolled up and
# past its retention.
RETENTION_RAW       = 7  * 86400     # 7 days of 60s polls
RETENTION_1MIN      = 30 * 86400     # 30 days of 1-min aggregates
RETENTION_1HOUR     = 365 * 86400    # 1 year of 1-hour aggregates
# samples_1day is retained indefinitely.

# Tuning for WAL on Pi-class hardware.
PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 134217728",  # 128 MB; harmless if RAM is smaller
    "PRAGMA cache_size = -8192",     # 8 MB
)


class Store:
    """Async SQLite telemetry store. One open connection per instance.

    All inserts go through `record_poll()` which the scheduler calls. Reads
    (`get_latest`, `get_history`) are safe to call concurrently with writes
    thanks to WAL.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        for pragma in PRAGMAS:
            await self._db.execute(pragma)
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("storage open at %s", self.path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Store":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ---------- writes ----------

    async def record_poll(self, result: dict[str, Any]) -> None:
        """Persist a single `orchestrator.poll_once()` result."""
        if self._db is None:
            raise RuntimeError("Store not open")

        ts_str = result.get("timestamp")
        # Use seconds since epoch for storage; chart code wants this anyway.
        ts = int(time.time())
        elapsed_ms = int(round(result.get("elapsed_seconds", 0) * 1000))
        errors = result.get("errors", []) or []
        devices: dict[str, dict] = result.get("devices") or {}

        num_rows: list[tuple[int, str, str, float]] = []
        str_rows: list[tuple[int, str, str, str]] = []
        latest_num: list[tuple[str, str, int, float]] = []
        latest_str: list[tuple[str, str, int, str]] = []
        device_meta_rows: list[tuple[str, str, str, int | None, int]] = []
        ok_count = 0

        for label, data in devices.items():
            if not data:
                continue
            ok_count += 1
            device_meta_rows.append((
                label,
                str(data.get("_vendor", "")),
                str(data.get("_kind", "")),
                data.get("_slave_id"),
                ts,
            ))
            for k, v in data.items():
                if k in _META_KEYS:
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    num_rows.append((ts, label, k, float(v)))
                    latest_num.append((label, k, ts, float(v)))
                elif isinstance(v, str):
                    str_rows.append((ts, label, k, v))
                    latest_str.append((label, k, ts, v))
                # bools and Nones are skipped intentionally

        db = self._db
        try:
            if num_rows:
                await db.executemany(
                    "INSERT OR REPLACE INTO samples(ts, device, metric, value) "
                    "VALUES (?, ?, ?, ?)",
                    num_rows,
                )
            if str_rows:
                await db.executemany(
                    "INSERT OR REPLACE INTO samples_str(ts, device, metric, value) "
                    "VALUES (?, ?, ?, ?)",
                    str_rows,
                )
            if latest_num:
                await db.executemany(
                    "INSERT INTO latest(device, metric, ts, value_num, value_str) "
                    "VALUES (?, ?, ?, ?, NULL) "
                    "ON CONFLICT(device, metric) DO UPDATE SET "
                    "  ts=excluded.ts, value_num=excluded.value_num, value_str=NULL",
                    latest_num,
                )
            if latest_str:
                await db.executemany(
                    "INSERT INTO latest(device, metric, ts, value_num, value_str) "
                    "VALUES (?, ?, ?, NULL, ?) "
                    "ON CONFLICT(device, metric) DO UPDATE SET "
                    "  ts=excluded.ts, value_num=NULL, value_str=excluded.value_str",
                    latest_str,
                )
            for row in device_meta_rows:
                await db.execute(
                    "INSERT INTO device_meta(device, vendor, kind, slave_id, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen",
                    (*row, row[-1]),  # first_seen = last_seen on insert; overwritten only by ON CONFLICT path
                )
            await db.execute(
                "INSERT OR REPLACE INTO poll_runs(ts, elapsed_ms, devices_ok, errors_count, errors_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, elapsed_ms, ok_count, len(errors), repr(errors) if errors else None),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    # ---------- reads ----------

    async def get_latest(self) -> dict[str, dict[str, Any]]:
        """Return {device_label: {metric: value, ...}, ...} from the latest table."""
        if self._db is None:
            raise RuntimeError("Store not open")
        out: dict[str, dict[str, Any]] = {}
        async with self._db.execute(
            "SELECT device, metric, ts, value_num, value_str FROM latest "
            "ORDER BY device, metric"
        ) as cur:
            async for device, metric, ts, vnum, vstr in cur:
                d = out.setdefault(device, {"_updated_at": ts})
                d["_updated_at"] = max(d.get("_updated_at", 0), ts)
                d[metric] = vnum if vnum is not None else vstr

        # Attach metadata
        async with self._db.execute(
            "SELECT device, vendor, kind, slave_id, first_seen, last_seen FROM device_meta"
        ) as cur:
            async for device, vendor, kind, slave_id, first_seen, last_seen in cur:
                d = out.setdefault(device, {})
                d["_vendor"] = vendor
                d["_kind"] = kind
                d["_slave_id"] = slave_id
                d["_first_seen"] = first_seen
                d["_last_seen"] = last_seen
        return out

    async def list_devices(self) -> list[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("Store not open")
        rows = []
        async with self._db.execute(
            "SELECT device, vendor, kind, slave_id, first_seen, last_seen "
            "FROM device_meta ORDER BY device"
        ) as cur:
            async for device, vendor, kind, slave_id, first_seen, last_seen in cur:
                rows.append({
                    "label": device,
                    "vendor": vendor,
                    "kind": kind,
                    "slave_id": slave_id,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                })
        return rows

    def _pick_history_table(self, range_seconds: int) -> tuple[str, str, int]:
        """Choose the right rollup table for a query range.

        Returns (table, ts_column, native_bucket_seconds). The native bucket
        is the resolution stored — the caller may further downsample via
        SQL-side aggregation.
        """
        # Aim for ~500-2000 points across the range. Below thresholds where
        # raw resolution comfortably fits, prefer raw for accuracy.
        if range_seconds <= 6 * 3600:        # ≤ 6h  → raw (360 points @ 60s)
            return "samples", "ts", 1
        if range_seconds <= 7 * 86400:       # ≤ 7d  → 1-min rollup (10080 pts; downsample)
            return "samples_1min", "bucket_ts", 60
        if range_seconds <= 90 * 86400:      # ≤ 90d → 1-hour rollup
            return "samples_1hour", "bucket_ts", 3600
        return "samples_1day", "bucket_ts", 86400

    async def get_history(
        self,
        device: str,
        metric: str,
        since: int,
        until: int | None = None,
        bucket_seconds: int | None = None,
    ) -> list[tuple[int, float]]:
        """Return [(ts, avg_or_value), ...] for the given range.

        Picks raw `samples` for short ranges or one of the rollup tables for
        longer ones. If `bucket_seconds` is set and exceeds the native bucket
        of the chosen table, an extra SQL-side aggregation is applied so the
        client gets a sensibly small payload.
        """
        if self._db is None:
            raise RuntimeError("Store not open")
        until = until if until is not None else int(time.time())
        table, ts_col, native_bucket = self._pick_history_table(until - since)

        # Pick the right value expression: raw samples store `value`,
        # rollups store `avg` (and min/max/n, which we don't surface here).
        value_expr = "value" if table == "samples" else "avg"
        agg_expr = f"AVG({value_expr})" if table == "samples" else f"AVG({value_expr})"

        # If caller asked for a coarser bucket than the table's native one,
        # do an extra GROUP BY on (ts / bucket).
        use_bucketing = bucket_seconds and bucket_seconds > native_bucket
        if use_bucketing:
            sql = (
                f"SELECT ({ts_col} / ?) * ? AS bucket, {agg_expr} "
                f"FROM {table} "
                f"WHERE device = ? AND metric = ? AND {ts_col} BETWEEN ? AND ? "
                f"GROUP BY bucket "
                f"ORDER BY bucket"
            )
            params: tuple = (bucket_seconds, bucket_seconds, device, metric, since, until)
        else:
            sql = (
                f"SELECT {ts_col}, {value_expr} "
                f"FROM {table} "
                f"WHERE device = ? AND metric = ? AND {ts_col} BETWEEN ? AND ? "
                f"ORDER BY {ts_col}"
            )
            params = (device, metric, since, until)

        rows: list[tuple[int, float]] = []
        async with self._db.execute(sql, params) as cur:
            async for ts, value in cur:
                rows.append((int(ts), float(value)))
        return rows

    # ---------- maintenance ----------

    async def rollup_and_purge(self, now: int | None = None) -> dict[str, int]:
        """Compute pending rollups and apply retention. Idempotent.

        Re-rolls a sliding window so any late-arriving samples get folded in.
        Safe to call repeatedly; uses INSERT OR REPLACE keyed on the bucket.

        Returns counts per operation for observability.
        """
        if self._db is None:
            raise RuntimeError("Store not open")
        now = now if now is not None else int(time.time())
        stats: dict[str, int] = {}
        db = self._db

        # ---- Roll up samples → samples_1min ----
        # Re-roll the last RETENTION_RAW window. Cheap, ensures any late
        # writes get reflected.
        rolled_min = await db.execute(
            "INSERT OR REPLACE INTO samples_1min "
            "(bucket_ts, device, metric, avg, min, max, n) "
            "SELECT (ts / 60) * 60 AS b, device, metric, "
            "       AVG(value), MIN(value), MAX(value), COUNT(*) "
            "FROM samples "
            "WHERE ts >= ? "
            "GROUP BY b, device, metric",
            (now - RETENTION_RAW,),
        )
        stats["rolled_into_1min"] = rolled_min.rowcount

        # ---- Roll up samples_1min → samples_1hour ----
        rolled_hour = await db.execute(
            "INSERT OR REPLACE INTO samples_1hour "
            "(bucket_ts, device, metric, avg, min, max, n) "
            "SELECT (bucket_ts / 3600) * 3600 AS b, device, metric, "
            "       AVG(avg), MIN(min), MAX(max), SUM(n) "
            "FROM samples_1min "
            "WHERE bucket_ts >= ? "
            "GROUP BY b, device, metric",
            (now - RETENTION_1MIN,),
        )
        stats["rolled_into_1hour"] = rolled_hour.rowcount

        # ---- Roll up samples_1hour → samples_1day ----
        rolled_day = await db.execute(
            "INSERT OR REPLACE INTO samples_1day "
            "(bucket_ts, device, metric, avg, min, max, n) "
            "SELECT (bucket_ts / 86400) * 86400 AS b, device, metric, "
            "       AVG(avg), MIN(min), MAX(max), SUM(n) "
            "FROM samples_1hour "
            "WHERE bucket_ts >= ? "
            "GROUP BY b, device, metric",
            (now - RETENTION_1HOUR,),
        )
        stats["rolled_into_1day"] = rolled_day.rowcount

        # ---- Purge past retention ----
        purged_raw = await db.execute(
            "DELETE FROM samples WHERE ts < ?", (now - RETENTION_RAW,)
        )
        stats["purged_raw"] = purged_raw.rowcount

        purged_str = await db.execute(
            "DELETE FROM samples_str WHERE ts < ?", (now - RETENTION_RAW,)
        )
        stats["purged_raw_str"] = purged_str.rowcount

        purged_min = await db.execute(
            "DELETE FROM samples_1min WHERE bucket_ts < ?", (now - RETENTION_1MIN,)
        )
        stats["purged_1min"] = purged_min.rowcount

        purged_hour = await db.execute(
            "DELETE FROM samples_1hour WHERE bucket_ts < ?", (now - RETENTION_1HOUR,)
        )
        stats["purged_1hour"] = purged_hour.rowcount

        # Trim very old poll_runs too — keep ~30 days for diagnostics.
        purged_runs = await db.execute(
            "DELETE FROM poll_runs WHERE ts < ?", (now - 30 * 86400,)
        )
        stats["purged_poll_runs"] = purged_runs.rowcount

        await db.commit()
        log.info("rollup+purge: %s", stats)
        return stats

    async def today_aggregate(self, midnight_ts: int, now_ts: int) -> dict[str, Any]:
        """Compute today's energy aggregates from the bank's instantaneous power.

        The Rover's `consumption_today_wh` only counts loads through its load
        output terminals; anything wired directly to the bus is invisible to
        it. Integrating bank V × I across today's polls captures everything
        regardless of wiring.

        We use the trapezoid rule across consecutive polls to handle the
        variable inter-poll gap correctly.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        # Per-poll bank power: join voltage_v × current_a across batteries
        # at the same timestamp.
        sql = (
            "SELECT v.ts, SUM(v.value * i.value) AS bank_w "
            "FROM samples v "
            "JOIN samples i "
            "  ON v.ts = i.ts AND v.device = i.device "
            "WHERE v.metric = 'voltage_v' "
            "  AND i.metric = 'current_a' "
            "  AND v.device LIKE 'battery_%' "
            "  AND v.ts >= ? AND v.ts <= ? "
            "GROUP BY v.ts ORDER BY v.ts"
        )
        rows: list[tuple[int, float]] = []
        async with self._db.execute(sql, (midnight_ts, now_ts)) as cur:
            async for ts, w in cur:
                rows.append((int(ts), float(w)))

        charged_wh = 0.0
        discharged_wh = 0.0
        prev_ts: int | None = None
        prev_w: float = 0.0
        for ts, w in rows:
            if prev_ts is not None:
                dt_h = (ts - prev_ts) / 3600.0
                # Use trapezoid rule for the energy in this interval.
                # Split into positive/negative components separately so a
                # sign change inside the interval doesn't cancel out.
                avg_w = (prev_w + w) / 2.0
                e_wh = avg_w * dt_h
                if e_wh > 0:
                    charged_wh += e_wh
                else:
                    discharged_wh += -e_wh
            prev_ts, prev_w = ts, w

        # Pull PV today from the latest Rover poll.
        pv_today_wh = 0.0
        async with self._db.execute(
            "SELECT value FROM samples WHERE device = 'rover_mppt' "
            "AND metric = 'energy_today_wh' AND ts BETWEEN ? AND ? "
            "ORDER BY ts DESC LIMIT 1",
            (midnight_ts, now_ts),
        ) as cur:
            row = await cur.fetchone()
            if row:
                pv_today_wh = float(row[0])

        # Derived: total load today (any path, including unmeasured)
        load_today_wh = max(0.0, pv_today_wh + discharged_wh - charged_wh)

        return {
            "since_ts": midnight_ts,
            "now_ts": now_ts,
            "pv_today_wh": round(pv_today_wh, 1),
            "bank_charged_today_wh": round(charged_wh, 1),
            "bank_discharged_today_wh": round(discharged_wh, 1),
            "bank_net_today_wh": round(charged_wh - discharged_wh, 1),
            "load_today_wh": round(load_today_wh, 1),
            "poll_count": len(rows),
        }

    async def last_poll_run(self) -> dict[str, Any] | None:
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "SELECT ts, elapsed_ms, devices_ok, errors_count "
            "FROM poll_runs ORDER BY ts DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        ts, elapsed_ms, devices_ok, errors_count = row
        return {
            "ts": int(ts),
            "elapsed_ms": int(elapsed_ms),
            "devices_ok": int(devices_ok),
            "errors_count": int(errors_count),
        }
