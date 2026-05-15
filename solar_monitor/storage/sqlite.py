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

-- Small key/value scratch table for cached blobs (forecast payloads,
-- third-party integration state, etc). Use it for things too small to
-- warrant their own schema; anything bigger should get a real table.
CREATE TABLE IF NOT EXISTS kv (
    k          TEXT PRIMARY KEY,
    v          TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Archive of every PV-forecast point we've ever fetched. The current-
-- forecast blob in `kv` is overwritten on each poll, so without this
-- table we can't compute "yesterday's prediction vs reality." Keyed
-- by (fetched_at, period_end) — a single forecast fetch produces
-- ~336 rows (7 days × 48 half-hour slices), and we keep ~30 days of
-- history, so the table caps at ~80k rows. Light.
CREATE TABLE IF NOT EXISTS forecast_history (
    fetched_at INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    pv_w       REAL NOT NULL,
    pv_w_p10   REAL,
    pv_w_p90   REAL,
    PRIMARY KEY (fetched_at, period_end)
);
CREATE INDEX IF NOT EXISTS idx_fc_hist_period
    ON forecast_history (period_end);
"""

# Retention windows (seconds). Each lower-resolution table keeps data for
# longer; the higher-res source data is purged after being rolled up and
# past its retention.
RETENTION_RAW       = 7  * 86400     # 7 days of 60s polls
RETENTION_1MIN      = 30 * 86400     # 30 days of 1-min aggregates
RETENTION_1HOUR     = 365 * 86400    # 1 year of 1-hour aggregates
RETENTION_FORECAST  = 30 * 86400     # 30 days of archived PV-forecast points
# samples_1day is retained indefinitely.

# Tuning for WAL on Pi-class hardware.
PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 134217728",  # 128 MB; harmless if RAM is smaller
    "PRAGMA cache_size = -8192",     # 8 MB
)


# ---------- Schema migrations ----------
#
# `SCHEMA` above is CREATE-IF-NOT-EXISTS only — safe to run forever on
# an existing DB, but it can't *evolve* an existing table (rename a
# column, add a NOT NULL constraint, etc.). For those, we need a real
# migration runner.
#
# Strategy: use SQLite's built-in `PRAGMA user_version` as the
# bookkeeper. Every migration here gets a monotonic integer; on boot
# we read the stored user_version, run any with a higher number than
# stored, then write the new max version. Idempotent — re-running
# against an up-to-date DB is a no-op.
#
# Adding a migration:
#   1. Pick the next integer.
#   2. Add (N, "short name", SQL_or_async_callable) to MIGRATIONS.
#   3. Bump SCHEMA_VERSION to N.
#   4. Test against a copy of a real customer DB before tagging a
#      release — schema mistakes are forever for self-hosted users.
#
# Why not Alembic: the cloud uses Alembic because it has a single
# Postgres + a managed deploy. The appliance has thousands of
# self-hosted SQLite files, no central control plane to bless a
# revision id, and the simplicity of `PRAGMA user_version` is a
# better fit. If migrations get genuinely complex, revisit.
#
# Adding a NEW TABLE doesn't need a migration — add it to SCHEMA
# above with CREATE IF NOT EXISTS and it'll appear on next boot.
# Migrations are for ALTERing tables that already exist.
SCHEMA_VERSION = 1

MIGRATIONS: list[tuple[int, str, str]] = [
    # (version, description, SQL).
    # Placeholder for the first real migration. Example shape:
    #   (1, "samples: add quality column",
    #    "ALTER TABLE samples ADD COLUMN quality INTEGER DEFAULT 0"),
]


async def _apply_migrations(db) -> None:
    """Run any unapplied schema migrations against the open
    connection. Reads / writes `PRAGMA user_version`. Each migration
    runs in its own transaction — a failure leaves the previous
    versions applied and the user_version pointing at the highest
    that succeeded, so an admin can investigate without losing
    intermediate state.

    Logged at INFO so a recovering customer can see in their daemon
    logs which migration ran (or stalled)."""
    async with db.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current = int(row[0] if row else 0)

    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        log.debug("schema up-to-date at version %d", current)
        return

    log.info("applying %d schema migration(s) from v%d → v%d",
             len(pending), current, max(m[0] for m in pending))
    for version, name, sql in sorted(pending, key=lambda m: m[0]):
        log.info("migration v%d: %s", version, name)
        try:
            if callable(sql):
                await sql(db)
            else:
                await db.executescript(sql)
            # user_version must be set AFTER the migration body so a
            # crash mid-migration leaves user_version pointing at the
            # last successful step. aiosqlite's default isolation
            # level commits DDL automatically; we follow with a
            # synchronous commit() to make sure the version bump
            # lands on disk before the next migration runs.
            await db.execute(f"PRAGMA user_version = {version}")
            await db.commit()
        except Exception:
            log.exception(
                "migration v%d (%s) FAILED — DB left at user_version=%d. "
                "Daemon will keep booting against the old schema; investigate "
                "before applying further changes.",
                version, name, current,
            )
            raise


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
        # Base schema first — CREATE-IF-NOT-EXISTS so this is safe to
        # run against an existing DB. Then run any pending migrations
        # to evolve from one schema_version to the next.
        await self._db.executescript(SCHEMA)
        await _apply_migrations(self._db)
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

    # ---------- forecast archive ----------

    async def archive_forecast(
        self, fetched_at: int,
        points: list[tuple[int, float, float | None, float | None]],
    ) -> None:
        """Persist a batch of forecast points under one fetched_at
        timestamp. Each point is (period_end_ts, pv_w, p10, p90).

        Idempotent — re-archiving the same (fetched_at, period_end)
        replaces the previous row. The same scheduler that records the
        live cache calls this; the two stay in lockstep."""
        if self._db is None:
            raise RuntimeError("Store not open")
        if not points:
            return
        rows = [(fetched_at, t, w, p10, p90) for (t, w, p10, p90) in points]
        await self._db.executemany(
            "INSERT OR REPLACE INTO forecast_history "
            "(fetched_at, period_end, pv_w, pv_w_p10, pv_w_p90) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()

    async def forecast_accuracy_for_day(
        self,
        day_mid_ts: int,
        controller_labels: list[str],
    ) -> dict[str, Any] | None:
        """Compute predicted-vs-actual PV energy for one local day.

        Predicted: integrate the forecast that the user could have
        SEEN before the day started — the latest fetch made strictly
        before `day_mid_ts`. That's the prediction they "trusted"; a
        forecast made mid-day after observing low PV doesn't count.

        Actual: sum integrated pv_power_w from any charge_controller
        device whose label is in `controller_labels`, between
        day_mid_ts and day_mid_ts + 86400 exclusive.

        Returns None when either side has no data — the UI hides the
        widget rather than show "—%" of nothing.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        day_end = day_mid_ts + 86400

        # Predicted: latest forecast made before the target day, then
        # all of its points falling inside the day's window. (Solcast
        # period_end follows the END of the slice — we bucket by the
        # mid-point ts - 900 to keep the day boundary honest, same as
        # the dashboard summariser.)
        async with self._db.execute(
            "SELECT MAX(fetched_at) FROM forecast_history "
            "WHERE fetched_at < ?", (day_mid_ts,),
        ) as cur:
            row = await cur.fetchone()
            fetched_at = int(row[0]) if row and row[0] is not None else None
        if fetched_at is None:
            return None

        async with self._db.execute(
            "SELECT period_end, pv_w FROM forecast_history "
            "WHERE fetched_at = ? "
            "  AND (period_end - 900) >= ? "
            "  AND (period_end - 900) <  ? "
            "ORDER BY period_end",
            (fetched_at, day_mid_ts, day_end),
        ) as cur:
            forecast_points = [(int(r[0]), float(r[1])) async for r in cur]

        if not forecast_points:
            return None
        # Each point is the END of a 30-min slice; energy = pv_w × 0.5h
        predicted_wh = sum(w * 0.5 for _, w in forecast_points)

        # Actual: trapezoid-integrate pv_power_w from each controller's
        # samples, then sum across controllers.
        actual_wh = 0.0
        n_actual_samples = 0
        for label in controller_labels:
            sql = (
                "WITH paired AS ("
                "  SELECT ts, value,"
                "         LAG(ts)    OVER (ORDER BY ts) AS prev_ts,"
                "         LAG(value) OVER (ORDER BY ts) AS prev_v"
                "  FROM samples"
                "  WHERE device = ? AND metric = 'pv_power_w' AND ts BETWEEN ? AND ?"
                ")"
                "SELECT SUM(avg_w * dt_h), COUNT(*) FROM ("
                "  SELECT (prev_v + value) / 2.0 AS avg_w,"
                "         (ts - prev_ts) / 3600.0 AS dt_h"
                "  FROM paired"
                "  WHERE prev_ts IS NOT NULL"
                "    AND (ts - prev_ts) BETWEEN 1 AND 3600"
                ")"
            )
            async with self._db.execute(
                sql, (label, day_mid_ts, day_end),
            ) as cur:
                row = await cur.fetchone()
                if row and row[0] is not None:
                    actual_wh += float(row[0])
                    n_actual_samples += int(row[1] or 0)

        if n_actual_samples < 6:
            # Less than 6 intervals = not enough actual data to compare
            # against. Day isn't over yet, or daemon was offline.
            return None

        accuracy_pct = (actual_wh / predicted_wh * 100.0) if predicted_wh > 0 else None

        return {
            "day_ts":         day_mid_ts,
            "predicted_wh":   round(predicted_wh, 1),
            "actual_wh":      round(actual_wh, 1),
            "delta_wh":       round(actual_wh - predicted_wh, 1),
            "accuracy_pct":   round(accuracy_pct, 1) if accuracy_pct is not None else None,
            "forecast_fetched_at": fetched_at,
            "actual_samples": n_actual_samples,
        }

    # ---------- key/value scratch ----------

    async def kv_set(self, key: str, value: str) -> None:
        """Upsert a string blob under `key`. Caller is responsible for
        serialising (JSON, etc.); we treat the body as opaque so this
        stays cheap and provider-agnostic."""
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "INSERT INTO kv (k, v, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v, "
            "                              updated_at = excluded.updated_at",
            (key, value, int(time.time())),
        )
        await self._db.commit()

    async def kv_get(self, key: str) -> tuple[str, int] | None:
        """Returns (value, updated_at) or None if not set."""
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "SELECT v, updated_at FROM kv WHERE k = ?", (key,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return str(row[0]), int(row[1])

    # ---------- writes ----------

    async def record_poll(
        self, result: dict[str, Any], ts_override: int | None = None,
    ) -> None:
        """Persist a single `orchestrator.poll_once()` result.

        `ts_override` is only used by the demo history-seeding path —
        it lets us insert historical rows at past timestamps. Real
        polls always use the current clock.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        ts_str = result.get("timestamp")
        # Use seconds since epoch for storage; chart code wants this anyway.
        ts = int(ts_override) if ts_override is not None else int(time.time())
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

            # Derived metrics: cell drift / min / max for smart batteries.
            # Stored as regular numeric metrics so the existing history +
            # rollup machinery picks them up automatically.
            if data.get("_kind") == "smart_battery":
                cell_volts: list[float] = []
                n = int(data.get("cell_count") or 0)
                for i in range(n):
                    v = data.get(f"cell_voltage_{i}_v")
                    if isinstance(v, (int, float)):
                        cell_volts.append(float(v))
                if cell_volts:
                    cell_min = min(cell_volts)
                    cell_max = max(cell_volts)
                    cell_drift = cell_max - cell_min
                    for k, val in (
                        ("cell_min_v", cell_min),
                        ("cell_max_v", cell_max),
                        ("cell_drift_v", cell_drift),
                    ):
                        num_rows.append((ts, label, k, val))
                        latest_num.append((label, k, ts, val))

        # ----- Bank pseudo-device aggregate -----
        # Compute and persist bank-level metrics under device="bank" so the
        # existing history endpoint can chart bank.soc_pct, bank.power_w,
        # bank.voltage_v, etc. the same way it charts a real device.
        #
        # Source preference: a shunt (if present) wins over smart-battery
        # summation — matches the JS bank-aggregate logic.
        #
        # Critically, we DON'T recompute from "what responded this cycle"
        # alone — on a lossy BLE link, individual packs can miss a poll
        # cycle, which would flip pack_count from 3 → 2 and halve the
        # bank's capacity/current/voltage for a minute. Instead, augment
        # this cycle's devices with the most-recent snapshot of any
        # smart_battery/shunt from the `latest` table that's still fresh.
        # A pack that's been silent longer than BANK_AGGREGATE_STALE_S
        # gets dropped — the bank reflects current reality, not a frozen
        # version of an unplugged pack.
        augmented = await self._augment_for_bank(devices, now_ts=ts)
        bank = self._compute_bank_aggregate(augmented)
        if bank:
            for k, val in bank.items():
                if val is None:
                    continue
                num_rows.append((ts, "bank", k, float(val)))
                latest_num.append(("bank", k, ts, float(val)))
            device_meta_rows.append(("bank", "internal", "bank", None, ts))

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

    # Packs silent longer than this drop OUT of the bank aggregate.
    # 5 min covers the ~1 min poll cadence plus a few cycles of missed
    # polls (which is normal on a noisy BLE link without meaning the
    # pack is gone). Beyond that, treat it as actually offline — the
    # bank shrinks to match.
    BANK_AGGREGATE_STALE_S = 300

    async def _augment_for_bank(
        self, devices: dict[str, dict], now_ts: int,
    ) -> dict[str, dict]:
        """Return a copy of `devices` plus any cached smart_battery /
        shunt entries from the latest table that didn't respond this
        cycle but were last seen recently enough to still count as
        present. Lets the bank aggregate stay stable across single
        missed polls — without this, pack_count flickers between
        N and N-1 every time the BLE link drops a packet."""
        out = dict(devices)
        try:
            async with self._db.execute(
                "SELECT device, vendor, kind, slave_id, last_seen "
                "FROM device_meta WHERE kind IN ('smart_battery', 'shunt')"
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            log.exception("bank aggregate: device_meta lookup failed")
            return out

        # For each pack that's both already-seen-recently AND not in
        # this cycle's response set, pull its latest snapshot and
        # graft it in. Skip stale ones.
        for label, vendor, kind, slave_id, last_seen in rows:
            if label in out:
                continue
            if last_seen is None or (now_ts - int(last_seen)) > self.BANK_AGGREGATE_STALE_S:
                continue
            snap: dict = {
                "_vendor": vendor, "_kind": kind, "_slave_id": slave_id,
                "_last_seen": last_seen,
            }
            async with self._db.execute(
                "SELECT metric, value_num, value_str FROM latest WHERE device = ?",
                (label,),
            ) as mc:
                for metric, value_num, value_str in await mc.fetchall():
                    snap[metric] = value_num if value_num is not None else value_str
            out[label] = snap
        return out

    def _compute_bank_aggregate(self, devices: dict[str, dict]) -> dict[str, float] | None:
        """Compute bank-level aggregate metrics from one poll's per-device
        snapshot. Shunt wins for V/I/SoC if present; otherwise sum across
        smart batteries. Returns None if there's nothing to aggregate.
        """
        shunt = next((d for d in devices.values()
                      if d and d.get("_kind") == "shunt"), None)
        batts = [d for d in devices.values()
                 if d and d.get("_kind") == "smart_battery"]

        if shunt is not None:
            l = shunt
            v = float(l.get("voltage_v") or 0)
            i = float(l.get("current_a") or 0)
            cap = float(l.get("bank_capacity_ah") or l.get("capacity_ah") or 0)
            rem = float(l.get("remaining_ah") or (cap * (float(l.get("soc_pct") or 0) / 100)))
            soc = float(l.get("soc_pct") or (rem / cap * 100 if cap else 0))
            power_w = float(l.get("power_w") if l.get("power_w") is not None else v * i)
            return {
                "voltage_v": v,
                "current_a": i,
                "power_w": power_w,
                "soc_pct": soc,
                "remaining_ah": rem,
                "capacity_ah": cap,
                "pack_count": float(len(batts)),
            }

        if not batts:
            return None

        sum_v = sum(float(b.get("voltage_v") or 0) for b in batts)
        sum_i = sum(float(b.get("current_a") or 0) for b in batts)
        total_cap = sum(float(b.get("capacity_ah") or 0) for b in batts)
        total_rem = sum(float(b.get("remaining_charge_ah") or 0) for b in batts)
        mean_v = sum_v / len(batts) if batts else 0
        soc = (total_rem / total_cap * 100) if total_cap else 0
        power_w = mean_v * sum_i

        # Worst-pack drift across the bank — a single number that signals
        # "any pack imbalance worth investigating" when charted over time.
        worst_drift = 0.0
        all_cell_min = None
        all_cell_max = None
        for b in batts:
            n = int(b.get("cell_count") or 0)
            cells = []
            for j in range(n):
                cv = b.get(f"cell_voltage_{j}_v")
                if isinstance(cv, (int, float)):
                    cells.append(float(cv))
            if cells:
                pmin, pmax = min(cells), max(cells)
                worst_drift = max(worst_drift, pmax - pmin)
                all_cell_min = pmin if all_cell_min is None else min(all_cell_min, pmin)
                all_cell_max = pmax if all_cell_max is None else max(all_cell_max, pmax)

        out: dict[str, float] = {
            "voltage_v": mean_v,
            "current_a": sum_i,
            "power_w": power_w,
            "soc_pct": soc,
            "remaining_ah": total_rem,
            "capacity_ah": total_cap,
            "pack_count": float(len(batts)),
            "worst_pack_drift_v": worst_drift,
        }
        if all_cell_min is not None: out["cell_min_v"] = all_cell_min
        if all_cell_max is not None: out["cell_max_v"] = all_cell_max
        return out

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
    ) -> dict[str, Any]:
        """Return time-series data + min/max bands + summary stats.

        Returns a dict with:
          ts:     [unix-seconds, ...]
          values: [number, ...]                  (raw value or avg)
          min:    [number, ...] | None           (rollup min, if rollup table)
          max:    [number, ...] | None           (rollup max, if rollup table)
          stats:  {now, min, max, avg, count}
          table:  which storage table the data came from
        """
        if self._db is None:
            raise RuntimeError("Store not open")
        until = until if until is not None else int(time.time())
        table, ts_col, native_bucket = self._pick_history_table(until - since)
        is_rollup = table != "samples"

        # Build the right SELECT clause: rollups have avg/min/max columns;
        # raw samples only have the single `value`.
        if is_rollup:
            value_expr = "avg"
            min_expr = "min"
            max_expr = "max"
        else:
            value_expr = "value"
            min_expr = "value"
            max_expr = "value"

        use_bucketing = bucket_seconds and bucket_seconds > native_bucket
        if use_bucketing:
            sql = (
                f"SELECT ({ts_col} / ?) * ? AS bucket, "
                f"       AVG({value_expr}), MIN({min_expr}), MAX({max_expr}) "
                f"FROM {table} "
                f"WHERE device = ? AND metric = ? AND {ts_col} BETWEEN ? AND ? "
                f"GROUP BY bucket "
                f"ORDER BY bucket"
            )
            params: tuple = (bucket_seconds, bucket_seconds, device, metric, since, until)
        else:
            sql = (
                f"SELECT {ts_col}, {value_expr}, {min_expr}, {max_expr} "
                f"FROM {table} "
                f"WHERE device = ? AND metric = ? AND {ts_col} BETWEEN ? AND ? "
                f"ORDER BY {ts_col}"
            )
            params = (device, metric, since, until)

        ts: list[int] = []
        values: list[float] = []
        mins: list[float] = []
        maxs: list[float] = []
        async with self._db.execute(sql, params) as cur:
            async for t, v, mn, mx in cur:
                ts.append(int(t))
                values.append(float(v))
                mins.append(float(mn))
                maxs.append(float(mx))

        # Summary stats — computed once on the server so every client gets
        # the same numbers without re-doing the math.
        stats: dict[str, Any] = {"count": len(values)}
        if values:
            stats["now"] = round(values[-1], 4)
            stats["min"] = round(min(mins), 4)
            stats["max"] = round(max(maxs), 4)
            stats["avg"] = round(sum(values) / len(values), 4)
            stats["range"] = round(stats["max"] - stats["min"], 4)
        else:
            stats.update({"now": None, "min": None, "max": None, "avg": None, "range": None})

        return {
            "ts": ts,
            "values": values,
            "min": mins if is_rollup or use_bucketing else None,
            "max": maxs if is_rollup or use_bucketing else None,
            "stats": stats,
            "table": table,
        }

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

        # Prune archived forecast points past retention. We keep enough
        # history for the accuracy widget to look back ~30 days.
        purged_fc = await db.execute(
            "DELETE FROM forecast_history WHERE period_end < ?",
            (now - RETENTION_FORECAST,),
        )
        stats["purged_forecast_history"] = purged_fc.rowcount

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

    async def load_heatmap(self, since_ts: int, until_ts: int) -> dict[str, Any]:
        """Aggregate bank power (V×I summed across packs) by hour-of-day
        × day-of-week. Returns a 7×24 grid of mean load (positive = the
        bank supplying watts, i.e. household consumption).

        Uses local time for the hour/day bucketing — what counts as
        "Sunday at 6pm" is the user's idea, not UTC's.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        sql = (
            "SELECT v.ts, SUM(v.value * i.value) AS bank_w "
            "FROM samples v "
            "JOIN samples i ON v.ts = i.ts AND v.device = i.device "
            "WHERE v.metric = 'voltage_v' AND i.metric = 'current_a' "
            "  AND v.device LIKE 'battery_%' "
            "  AND v.ts BETWEEN ? AND ? "
            "GROUP BY v.ts"
        )

        # buckets[dow][hour] = (sum_w, count) — accumulating absolute
        # discharge wattage (we only count negative bank_w as "load").
        # 7×24 grid; Monday = 0 follows ISO + Python convention.
        sums = [[0.0] * 24 for _ in range(7)]
        counts = [[0] * 24 for _ in range(7)]

        async with self._db.execute(sql, (since_ts, until_ts)) as cur:
            async for ts, w in cur:
                # Only count discharging (negative bank net) — for off-grid
                # heat-mapping "what's drawing power" is the question.
                if w is None or w >= 0:
                    continue
                load_w = -float(w)  # positive watts consumed
                local = time.localtime(ts)
                dow = local.tm_wday  # 0 = Mon
                hour = local.tm_hour
                sums[dow][hour] += load_w
                counts[dow][hour] += 1

        # Compute per-cell mean; None where no data
        grid: list[list[float | None]] = [
            [(sums[d][h] / counts[d][h]) if counts[d][h] else None for h in range(24)]
            for d in range(7)
        ]

        # Headline stats
        all_w = [v for row in grid for v in row if v is not None]
        return {
            "since_ts": since_ts,
            "until_ts": until_ts,
            "grid": grid,                          # 7 rows × 24 cols, watts
            "counts": counts,                       # for tooltip / opacity
            "max_w": max(all_w) if all_w else 0,
            "min_w": min(all_w) if all_w else 0,
            "mean_w": (sum(all_w) / len(all_w)) if all_w else 0,
        }

    async def battery_lifetime_stats(self, device: str) -> dict[str, Any]:
        """Compute coulomb-counted lifetime Ah in/out + cycle count for a pack.

        Trapezoid-integrates `current_a` across every poll we have for this
        device. Cycles are referenced against the latest known `capacity_ah`
        (also fetched here, defaulting to 100 Ah for the typical LFP pack).

        For multi-year data this becomes expensive — at v0.0.x volumes it's
        fast. When it starts to bite we'll add a `lifetime_counters` table
        that the scheduler increments on each poll.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        # Window-function pairing: each row gets the previous poll's ts + value.
        sql = (
            "WITH paired AS ("
            "  SELECT ts, value,"
            "         LAG(ts)    OVER (ORDER BY ts) AS prev_ts,"
            "         LAG(value) OVER (ORDER BY ts) AS prev_v"
            "  FROM samples"
            "  WHERE device = ? AND metric = 'current_a'"
            ")"
            "SELECT"
            "  SUM(CASE WHEN avg_i > 0 THEN  avg_i * dt_h ELSE 0 END) AS ah_in,"
            "  SUM(CASE WHEN avg_i < 0 THEN -avg_i * dt_h ELSE 0 END) AS ah_out,"
            "  MIN(ts) AS until_ts,"
            "  COUNT(*) AS n "
            "FROM ("
            "  SELECT (prev_v + value) / 2.0 AS avg_i,"
            "         (ts - prev_ts) / 3600.0 AS dt_h, ts"
            "  FROM paired"
            "  WHERE prev_ts IS NOT NULL"
            "    AND (ts - prev_ts) BETWEEN 1 AND 3600"  # ignore polling-gap outliers
            ")"
        )

        ah_in = ah_out = 0.0
        since_ts: int | None = None
        until_ts: int | None = None
        n_intervals = 0
        async with self._db.execute(sql, (device,)) as cur:
            row = await cur.fetchone()
            if row:
                ah_in = float(row[0] or 0)
                ah_out = float(row[1] or 0)
                until_ts = int(row[2]) if row[2] is not None else None
                n_intervals = int(row[3] or 0)

        # First-seen timestamp for context ("X days ago")
        async with self._db.execute(
            "SELECT MIN(ts), MAX(ts) FROM samples "
            "WHERE device = ? AND metric = 'current_a'",
            (device,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                since_ts = int(row[0]) if row[0] is not None else None
                until_ts = int(row[1]) if row[1] is not None else until_ts

        # Latest known capacity for the pack — needed to express cycle equivalence
        capacity_ah = 100.0
        async with self._db.execute(
            "SELECT value FROM samples WHERE device = ? AND metric = 'capacity_ah' "
            "ORDER BY ts DESC LIMIT 1",
            (device,),
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None:
                capacity_ah = float(row[0])

        cycles = round(ah_out / capacity_ah, 2) if capacity_ah > 0 else 0
        return {
            "device": device,
            "ah_in": round(ah_in, 2),
            "ah_out": round(ah_out, 2),
            "ah_throughput": round(ah_in + ah_out, 2),
            "cycles": cycles,
            "capacity_ah": capacity_ah,
            "since_ts": since_ts,
            "until_ts": until_ts,
            "interval_samples": n_intervals,
        }

    async def _coulomb_window(
        self, device: str, since_ts: int | None = None,
    ) -> tuple[float, float]:
        """Trapezoid-integrate `current_a` for one device, optionally
        bounded by since_ts. Returns (ah_in, ah_out) where both are
        positive. Pairs include one sample taken before since_ts so the
        first interval inside the window is fully accounted for —
        prevents bias when the window starts mid-cycle."""
        if self._db is None:
            raise RuntimeError("Store not open")

        since_clause = ""
        params: list[Any] = [device]
        if since_ts is not None:
            since_clause = " AND ts >= ?"
            params.append(since_ts)

        sql = (
            "WITH paired AS ("
            "  SELECT ts, value,"
            "         LAG(ts)    OVER (ORDER BY ts) AS prev_ts,"
            "         LAG(value) OVER (ORDER BY ts) AS prev_v"
            "  FROM samples"
            "  WHERE device = ? AND metric = 'current_a'"
            ")"
            "SELECT"
            "  SUM(CASE WHEN avg_i > 0 THEN  avg_i * dt_h ELSE 0 END) AS ah_in,"
            "  SUM(CASE WHEN avg_i < 0 THEN -avg_i * dt_h ELSE 0 END) AS ah_out "
            "FROM ("
            "  SELECT (prev_v + value) / 2.0 AS avg_i,"
            "         (ts - prev_ts) / 3600.0 AS dt_h, ts"
            "  FROM paired"
            "  WHERE prev_ts IS NOT NULL"
            "    AND (ts - prev_ts) BETWEEN 1 AND 3600"
            f"{since_clause}"
            ")"
        )
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
        if not row:
            return 0.0, 0.0
        return float(row[0] or 0), float(row[1] or 0)

    async def _remaining_ah_near(
        self, device: str, target_ts: int, direction: str = "after",
    ) -> tuple[float | None, int | None]:
        """Return (remaining_charge_ah, ts) for the sample closest to
        target_ts on the specified side. direction='after' picks the
        first sample at or after target_ts; 'before' picks the last
        before. Returns (None, None) if no sample on that side."""
        if self._db is None:
            raise RuntimeError("Store not open")
        if direction == "after":
            sql = (
                "SELECT value, ts FROM samples "
                "WHERE device = ? AND metric = 'remaining_charge_ah' "
                "  AND ts >= ? ORDER BY ts ASC LIMIT 1"
            )
        else:
            sql = (
                "SELECT value, ts FROM samples "
                "WHERE device = ? AND metric = 'remaining_charge_ah' "
                "  AND ts <= ? ORDER BY ts DESC LIMIT 1"
            )
        async with self._db.execute(sql, (device, target_ts)) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None, None
        return float(row[0]), int(row[1])

    async def battery_efficiency(
        self, device: str, since_ts: int | None = None,
    ) -> dict[str, Any]:
        """SoC-corrected coulomb efficiency for one smart battery pack
        over a window.

        Math: ah_in * η_charge - ah_out = (remaining_end - remaining_start)
              => η_charge = (ah_out + Δremaining) / ah_in

        Both `ah_in` and `ah_out` are positive Ah totals over the window;
        Δremaining (in Ah) accounts for charge still in the pack at the
        end vs the start, so a window that ends mid-charge doesn't
        artificially depress the ratio.

        We only mark the result `reliable` when total throughput is at
        least one pack's worth — i.e. ah_in >= capacity_ah. With less
        cycling, SoC measurement noise dominates the signal.

        Window:
          since_ts == None → lifetime
          else → samples with ts >= since_ts
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        ah_in, ah_out = await self._coulomb_window(device, since_ts)

        # Latest capacity for the reliability gate + cycle-equivalents.
        capacity_ah = 100.0
        async with self._db.execute(
            "SELECT value FROM samples WHERE device = ? AND metric = 'capacity_ah' "
            "ORDER BY ts DESC LIMIT 1",
            (device,),
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None:
                capacity_ah = float(row[0])

        # Pin window start/end timestamps so the SoC delta sees the
        # right boundaries. For lifetime, the start is the device's
        # first remaining_charge_ah sample.
        async with self._db.execute(
            "SELECT MIN(ts), MAX(ts) FROM samples "
            "WHERE device = ? AND metric = 'remaining_charge_ah'",
            (device,),
        ) as cur:
            row = await cur.fetchone()
            ts_first = int(row[0]) if row and row[0] is not None else None
            ts_last  = int(row[1]) if row and row[1] is not None else None

        start_target = since_ts if since_ts is not None else (ts_first or 0)
        rem_start, rem_start_ts = await self._remaining_ah_near(
            device, start_target, direction="after",
        )
        rem_end, rem_end_ts = (None, None)
        if ts_last is not None:
            rem_end, rem_end_ts = await self._remaining_ah_near(
                device, ts_last, direction="before",
            )

        delta_remaining: float | None = None
        if rem_start is not None and rem_end is not None:
            delta_remaining = rem_end - rem_start

        efficiency_pct: float | None = None
        if ah_in > 0 and delta_remaining is not None:
            # The corrected formula (see docstring).
            eta = (ah_out + delta_remaining) / ah_in
            # Round-trip is normally in [0.85, 1.02]. Anything outside is
            # almost certainly a data artefact (sensor noise on a near-
            # empty window). Cap to a sensible range so the UI never
            # shows "126% efficiency" which would erode trust.
            if 0.5 <= eta <= 1.05:
                efficiency_pct = round(eta * 100, 2)

        cycles = round(ah_in / capacity_ah, 2) if capacity_ah > 0 else 0
        reliable = (
            efficiency_pct is not None
            and ah_in >= capacity_ah   # at least one cycle of throughput
        )
        return {
            "device":            device,
            "since_ts":          since_ts,
            "ah_in":             round(ah_in, 2),
            "ah_out":            round(ah_out, 2),
            "remaining_start":   round(rem_start, 2)  if rem_start  is not None else None,
            "remaining_end":     round(rem_end, 2)    if rem_end    is not None else None,
            "delta_remaining":   round(delta_remaining, 2) if delta_remaining is not None else None,
            "capacity_ah":       round(capacity_ah, 2),
            "cycle_equivalents": cycles,
            "efficiency_pct":    efficiency_pct,
            "reliable":          reliable,
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
