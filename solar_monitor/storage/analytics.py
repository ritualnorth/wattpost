"""Energy / battery analytics queries for the telemetry store (mixed into Store)."""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class AnalyticsMixin:
    """Read-side aggregates over the rollup and sample tables. Uses self._db."""

    async def today_aggregate(self, midnight_ts: int, now_ts: int) -> dict[str, Any]:
        """Compute today's energy aggregates from per-poll instantaneous
        power, integrated via trapezoid rule across the day.

        Captures everything wired anywhere on the bus, not just the
        load output of a particular MPPT, integration over the bank's
        V × I tells the truth regardless of cabling.

        Previous implementation (pre-v0.0.81) had two bugs:
          1. PV "today" came from a hardcoded `device = 'rover_mppt'`
             SQL query, but the actual MPPT device label varies
             ("charge_controller", "rover_40a", anything the install
             chose). For everyone except the original Renogy Rover
             install, PV today was always zero.
          2. The source-energy term only counted PV. An install with
             an AC charger + alternator running could be net-charging
             the bank with significant load on top, but the load
             formula (`pv + discharged − charged`) would compute
             negative and clamp to zero, hiding the load entirely.

        Both fixed by integrating power directly over EVERY source-kind
        device's per-poll metric:
          - charge_controller → pv_power_w
          - ac_charger        → output_1_power_w
          - dcdc / dcdc_xs    → output_power_w
        And summing into `sources_today_wh`. Load is then
            max(0, sources_in − bank_net),
        which is the correct energy-balance identity.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        # Per-poll bank power: join voltage_v × current_a across batteries
        # at the same timestamp.
        bank_sql = (
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
        bank_rows: list[tuple[int, float]] = []
        async with self._db.execute(bank_sql, (midnight_ts, now_ts)) as cur:
            async for ts, w in cur:
                bank_rows.append((int(ts), float(w)))

        charged_wh = 0.0
        discharged_wh = 0.0
        prev_ts: int | None = None
        prev_w: float = 0.0
        for ts, w in bank_rows:
            if prev_ts is not None:
                dt_h = (ts - prev_ts) / 3600.0
                avg_w = (prev_w + w) / 2.0
                e_wh = avg_w * dt_h
                if e_wh > 0: charged_wh += e_wh
                else:        discharged_wh += -e_wh
            prev_ts, prev_w = ts, w

        # ---- Per-source integration ----
        # Map (device_kind, metric_name) → trapezoid integration of that
        # metric across today's polls. Each row in `samples` is one
        # device/metric/timestamp triple, we filter by metric and
        # `device LIKE pattern` corresponding to the kind. Device names
        # aren't tagged with their kind in `samples`, but Renogy MPPTs
        # report `pv_power_w`, AC chargers report `output_1_power_w`,
        # etc, so filtering by the metric name effectively scopes by
        # kind. Multiple devices of the same kind sum naturally (each
        # device contributes its own poll rows).
        async def _integrate_metric(metric: str) -> float:
            sql = (
                "SELECT device, ts, value FROM samples "
                "WHERE metric = ? AND ts >= ? AND ts <= ? "
                "ORDER BY device, ts"
            )
            total_wh = 0.0
            prev_dev: str | None = None
            prev_t: int | None = None
            prev_v: float = 0.0
            async with self._db.execute(sql, (metric, midnight_ts, now_ts)) as cur:
                async for dev, ts, v in cur:
                    ts = int(ts); v = float(v)
                    if dev != prev_dev:
                        prev_dev, prev_t, prev_v = dev, ts, v
                        continue
                    dt_h = (ts - prev_t) / 3600.0
                    # Sources are always positive output; clamp at 0 so
                    # a spurious negative reading can't subtract from
                    # the day's total.
                    avg_w = max(0.0, (prev_v + v) / 2.0)
                    total_wh += avg_w * dt_h
                    prev_t, prev_v = ts, v
            return total_wh

        # PV: prefer the device's own `energy_today_wh` cumulative
        # counter when it exists (Renogy MPPTs all expose this and
        # it's accurate to the second). Integration via 60 s polls
        # systematically under-counts fast peaks, observed at ~40%
        # under on a real install. Fall back to integration only for
        # PV devices that don't surface the counter (rare).
        #
        # Counter-reset detection: the device's daily counter resets
        # on ITS OWN clock (BT-2 dongle time), not the server's
        # midnight. Across midnight-UTC → device-midnight, the
        # samples carry yesterday's accumulated total, taking a
        # naive `MAX(value)` would phantom-credit that to today.
        # Walk the samples per device in time order and accumulate
        # only the POSITIVE deltas (cur >= prev). When the counter
        # drops, that's a reset event, we start a fresh leg.
        pv_today_wh = 0.0
        sql = (
            "SELECT device, ts, value FROM samples "
            "WHERE metric = 'energy_today_wh' "
            "  AND ts BETWEEN ? AND ? "
            "ORDER BY device, ts"
        )
        seen_any = False
        prev_dev: str | None = None
        prev_v: float = 0.0
        async with self._db.execute(sql, (midnight_ts, now_ts)) as cur:
            async for dev, _ts, v in cur:
                seen_any = True
                v = float(v)
                if dev != prev_dev:
                    # First sample for this device in the window. We
                    # don't yet know whether the counter has reset
                    # since yesterday; treat the value at this point
                    # as the baseline (not as today's harvest).
                    prev_dev = dev
                    prev_v = v
                    continue
                if v >= prev_v:
                    pv_today_wh += (v - prev_v)
                # else: counter dropped → device reset. Don't credit
                # the drop as negative energy; just rebase.
                prev_v = v
        if not seen_any or pv_today_wh <= 0:
            # No device counter visible, or the counter hasn't moved
            # at all today (overnight, brand-new install). Integrate
            # power directly as a fallback.
            pv_today_wh = await _integrate_metric("pv_power_w")
        # AC chargers / DC-DC: no per-device daily counter in the BLE
        # advertisement payload, so integration is the only option.
        # 60 s polls give a usable-but-rough total, fine for "is the
        # load real" sanity but don't expect device-counter accuracy.
        ac_charger_today_wh   = await _integrate_metric("output_1_power_w")
        dcdc_today_wh         = await _integrate_metric("output_power_w")
        sources_today_wh = pv_today_wh + ac_charger_today_wh + dcdc_today_wh

        # Derived: total load today (any path, including unmeasured)
        load_today_wh = max(0.0, sources_today_wh + discharged_wh - charged_wh)

        return {
            "since_ts": midnight_ts,
            "now_ts": now_ts,
            "pv_today_wh":             round(pv_today_wh, 1),
            "ac_charger_today_wh":     round(ac_charger_today_wh, 1),
            "dcdc_today_wh":           round(dcdc_today_wh, 1),
            "sources_today_wh":        round(sources_today_wh, 1),
            "bank_charged_today_wh":   round(charged_wh, 1),
            "bank_discharged_today_wh": round(discharged_wh, 1),
            "bank_net_today_wh":       round(charged_wh - discharged_wh, 1),
            "load_today_wh":           round(load_today_wh, 1),
            "poll_count":              len(bank_rows),
        }

    async def battery_health_aggregate(
        self, since_ts: int, until_ts: int,
    ) -> dict[str, Any]:
        """Bucket bank SoC residency into 10-percent bins, plus the
        equivalent-full-cycle count over the window.

        Two distinct numbers customers care about:

          * **Where does the battery live?** A bank that spends 80 %
            of its time at 30-50 % SoC will wear out faster than one
            that lives at 70-90 %. The histogram makes that visible.

          * **How many cycles HAVE you done since install?** The BMS
            cycle counter only exists if you have a BMS. For everyone
            else, we integrate discharged Ah and divide by capacity.
            Doesn't match the BMS exactly (the BMS uses its own
            definition of "cycle") but lands in the same ballpark.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        # SoC residency histogram. Sample bank.soc_pct hourly across
        # the window and bucket. samples_1hour gives us pre-rolled
        # averages, but we only have those if the window is long
        # enough, otherwise sample raw.
        table, ts_col, _ = self._pick_history_table(until_ts - since_ts)
        value_col = "value" if table == "samples" else "avg"
        soc_sql = (
            f"SELECT {value_col} "
            f"FROM {table} "
            f"WHERE device = 'bank' AND metric = 'soc_pct' "
            f"  AND {ts_col} BETWEEN ? AND ? "
            f"  AND {value_col} IS NOT NULL"
        )
        soc_buckets = [0] * 10  # 0-10, 10-20, …, 90-100
        async with self._db.execute(soc_sql, (since_ts, until_ts)) as cur:
            async for row in cur:
                v = float(row[0] or 0)
                # Clamp 0-100, pick bucket. 100 → bucket 9 (90-100).
                idx = min(9, max(0, int(v // 10)))
                soc_buckets[idx] += 1
        total_samples = sum(soc_buckets) or 1
        soc_residency = [
            {
                "range": f"{i*10}-{(i+1)*10}%",
                "pct":   round(count / total_samples * 100, 1),
            }
            for i, count in enumerate(soc_buckets)
        ]

        # Discharged Ah across the window (sum of |negative current|
        # integrated). Cycle equivalents = ÷ bank capacity.
        ah_sql = (
            "SELECT v.ts, SUM(v.value * i.value) AS bank_w "
            "FROM samples v "
            "JOIN samples i "
            "  ON v.ts = i.ts AND v.device = i.device "
            "WHERE v.metric = 'voltage_v' "
            "  AND i.metric = 'current_a' "
            "  AND v.device LIKE 'battery_%' "
            "  AND v.ts BETWEEN ? AND ? "
            "GROUP BY v.ts ORDER BY v.ts"
        )
        discharged_wh = 0.0
        charged_wh = 0.0
        prev_ts: int | None = None
        prev_w = 0.0
        async with self._db.execute(ah_sql, (since_ts, until_ts)) as cur:
            async for ts, w in cur:
                if prev_ts is not None:
                    dt_h = (int(ts) - prev_ts) / 3600.0
                    e = (prev_w + float(w)) / 2.0 * dt_h
                    if e > 0: charged_wh += e
                    else:     discharged_wh += -e
                prev_ts, prev_w = int(ts), float(w)

        # Pull bank capacity from latest table (BMS or shunt).
        cap_ah = 0.0
        nominal_v = 12.8  # LFP 4S, sensible default
        async with self._db.execute(
            "SELECT value_num FROM latest "
            "WHERE device = 'bank' AND metric IN ('capacity_ah', 'voltage_v')"
        ) as cur:
            async for row in cur:
                pass  # placeholder, re-query below for clarity
        async with self._db.execute(
            "SELECT metric, value_num FROM latest "
            "WHERE device = 'bank' AND metric IN ('capacity_ah', 'voltage_v')"
        ) as cur:
            async for metric, val in cur:
                if metric == "capacity_ah" and val: cap_ah = float(val)
                if metric == "voltage_v"  and val: nominal_v = float(val)

        discharged_ah = discharged_wh / nominal_v if nominal_v else 0
        equivalent_cycles = round(discharged_ah / cap_ah, 1) if cap_ah else None

        # Lifetime / time-on-system: earliest bank sample.
        days_online: float | None = None
        async with self._db.execute(
            "SELECT MIN(ts) FROM samples WHERE device = 'bank' AND metric = 'soc_pct'"
        ) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                days_online = round((until_ts - int(row[0])) / 86400.0, 1)

        # Pull lifetime fields from latest table (BMS-direct, may be None).
        bms_lifetime: dict[str, float] = {}
        async with self._db.execute(
            "SELECT metric, value_num FROM latest "
            "WHERE device = 'bank' "
            "  AND metric IN ('cycle_count', 'lifetime_throughput_ah', "
            "                 'lifetime_throughput_kwh')"
        ) as cur:
            async for metric, val in cur:
                if val is not None:
                    bms_lifetime[metric] = float(val)

        # #295, Shunt-derived lifetime fallback. When the BMS doesn't
        # report lifetime throughput / cycles (typical for Daly, no-BMS
        # shunt-only installs, JBD configs that don't expose the
        # counter), pull what the shunts themselves track. Renogy +
        # Junctek shunts both report cumulative_charge_ah and
        # cumulative_discharge_ah straight off the device.
        #
        # Summed across all shunts on the bus, covers the rare
        # multi-shunt install (one per parallel string). Single-shunt
        # is the normal case and the sum collapses cleanly.
        #
        # Returned as a separate `shunt` dict so the frontend can pick
        # the best source per metric (BMS > shunt > integration-from-
        # history) and surface provenance to the user.
        shunt_devices: list[str] = []
        async with self._db.execute(
            "SELECT device FROM device_meta WHERE kind = 'shunt'"
        ) as cur:
            async for (device,) in cur:
                shunt_devices.append(device)

        shunt_lifetime: dict[str, float] = {}
        if shunt_devices:
            placeholders = ",".join("?" * len(shunt_devices))
            sums: dict[str, float] = {}
            async with self._db.execute(
                f"SELECT metric, SUM(value_num) FROM latest "
                f"WHERE device IN ({placeholders}) "
                f"  AND metric IN ('cumulative_charge_ah', 'cumulative_discharge_ah', "
                f"                 'cycle_count') "
                f"  AND value_num IS NOT NULL "
                f"GROUP BY metric",
                shunt_devices,
            ) as cur:
                async for metric, total in cur:
                    if total is not None:
                        sums[metric] = float(total)

            if "cumulative_charge_ah" in sums:
                shunt_lifetime["cumulative_charge_ah"] = round(sums["cumulative_charge_ah"], 1)
            if "cumulative_discharge_ah" in sums:
                dis_ah = sums["cumulative_discharge_ah"]
                shunt_lifetime["cumulative_discharge_ah"] = round(dis_ah, 1)
                # Convert to kWh using the bank nominal voltage so the
                # frontend can render a single "lifetime energy" value
                # without re-doing this math.
                if nominal_v:
                    shunt_lifetime["lifetime_throughput_kwh"] = round(
                        dis_ah * nominal_v / 1000.0, 2
                    )
                # Estimated full-cycle count = total discharged Ah ÷
                # bank capacity. Only meaningful when we know capacity;
                # otherwise we'd be dividing by zero.
                if cap_ah:
                    shunt_lifetime["estimated_cycle_count"] = round(dis_ah / cap_ah, 1)
            # If the shunt itself reports a cycle counter (some Junctek
            # models do), surface it as-is.
            if "cycle_count" in sums:
                shunt_lifetime["cycle_count"] = round(sums["cycle_count"], 0)

        return {
            "since_ts": since_ts,
            "until_ts": until_ts,
            "soc_residency": soc_residency,
            "window_charged_kwh":    round(charged_wh / 1000.0, 2),
            "window_discharged_kwh": round(discharged_wh / 1000.0, 2),
            "window_equivalent_cycles": equivalent_cycles,
            "days_online": days_online,
            "bms": bms_lifetime,      # cycle_count, lifetime_throughput_ah/kwh (or empty if no BMS)
            "shunt": shunt_lifetime,  # cumulative_*_ah, lifetime_throughput_kwh, estimated_cycle_count (empty if no shunt)
            "bank_capacity_ah": round(cap_ah, 1) if cap_ah else None,
        }

    async def bank_soc_minmax(
        self, since_ts: int, until_ts: int,
    ) -> tuple[float | None, float | None]:
        """(min, max) of bank.soc_pct over the window. None if no rows.

        Used by the cloud heartbeat to surface "today's low/high SoC"
        on the dashboard card, answers "did the bank get critically
        low overnight?" without needing the operator to open History."""
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute(
            "SELECT MIN(value), MAX(value) FROM samples "
            "WHERE device = 'bank' AND metric = 'soc_pct' "
            "  AND ts BETWEEN ? AND ?",
            (since_ts, until_ts),
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None and row[1] is not None:
                return float(row[0]), float(row[1])
        return None, None

    async def rolling_load_avg(self, window_seconds: int = 3600) -> float | None:
        """Mean bank power (W) over the last `window_seconds`. Negative
        when discharging.

        Used by the runtime-forecast endpoint (#99) to make "hours to
        empty" stable. The instant V×I number is volatile, a kettle
        on for 30 seconds drags it down to a worrying value. A rolling
        1-hour average tracks the actual draw pattern."""
        if self._db is None:
            raise RuntimeError("Store not open")
        now = int(time.time())
        since = now - window_seconds
        async with self._db.execute(
            "SELECT AVG(v.value * i.value) "
            "FROM samples v "
            "JOIN samples i "
            "  ON v.ts = i.ts AND v.device = i.device "
            "WHERE v.metric = 'voltage_v' "
            "  AND i.metric = 'current_a' "
            "  AND v.device LIKE 'battery_%' "
            "  AND v.ts >= ?",
            (since,),
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
        return None

    async def load_heatmap(self, since_ts: int, until_ts: int) -> dict[str, Any]:
        """Aggregate bank power (V×I summed across packs) by hour-of-day
        × day-of-week. Returns a 7×24 grid of mean load (positive = the
        bank supplying watts, i.e. household consumption).

        Uses local time for the hour/day bucketing, what counts as
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

        # buckets[dow][hour] = (sum_w, count), accumulating absolute
        # discharge wattage (we only count negative bank_w as "load").
        # 7×24 grid; Monday = 0 follows ISO + Python convention.
        sums = [[0.0] * 24 for _ in range(7)]
        counts = [[0] * 24 for _ in range(7)]

        async with self._db.execute(sql, (since_ts, until_ts)) as cur:
            async for ts, w in cur:
                # Only count discharging (negative bank net), for off-grid
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

    async def charger_stats(
        self, device: str, power_metric: str,
        midnight_ts: int, now_ts: int,
    ) -> dict[str, Any]:
        """Charger value-add stats for the device-detail page.

        For an AC charger (`output_1_power_w`) or solar MPPT
        (`pv_power_w`) device, computes:
          - `lifetime_energy_wh`, total energy delivered since first
            poll (trapezoid-integrated power × dt). Cheap until the
            device has years of samples; revisit if it bites.
          - `today_energy_wh`, same integration, scoped to today.
          - `today_active_s`, seconds today where power > 5 W (i.e.
            charger was meaningfully on, ignoring sleep-tick noise).
          - `today_state_seconds`, dict of `charging_state` value →
            seconds spent in that state today (bulk, absorption,
            float, etc.). Lets the UI render the "Today: 45m bulk,
            2h abs, 5h float" breakdown.
          - `state_ribbon`, list of `{start_ts, end_ts, state}`
            segments today, condensed (consecutive same-state polls
            collapsed). Used by the JS to render the 24h ribbon bar.

        Independent of the `samples_str` table because `charging_state`
        is stored as a TEXT sample. Power metric is taken from the
        regular `samples` (numeric) table.
        """
        if self._db is None:
            raise RuntimeError("Store not open")

        # ---- Lifetime + today energy (integration of power_metric) ----
        async def _integrate(since: int, until: int) -> tuple[float, int]:
            """(energy_wh, active_seconds_with_power_above_threshold)."""
            energy_wh = 0.0
            active_s = 0
            prev_ts: int | None = None
            prev_w: float = 0.0
            async with self._db.execute(
                "SELECT ts, value FROM samples "
                "WHERE device = ? AND metric = ? "
                "  AND ts BETWEEN ? AND ? "
                "ORDER BY ts",
                (device, power_metric, since, until),
            ) as cur:
                async for ts, w in cur:
                    ts, w = int(ts), float(w)
                    if prev_ts is not None:
                        dt_s = ts - prev_ts
                        avg = max(0.0, (prev_w + w) / 2.0)
                        energy_wh += avg * (dt_s / 3600.0)
                        if avg > 5.0:
                            active_s += dt_s
                    prev_ts, prev_w = ts, w
            return energy_wh, active_s

        lifetime_wh, _ = await _integrate(0, now_ts)
        today_wh, today_active_s = await _integrate(midnight_ts, now_ts)

        # ---- Today's charging-state ribbon + per-state seconds ----
        state_rows: list[tuple[int, str]] = []
        async with self._db.execute(
            "SELECT ts, value FROM samples_str "
            "WHERE device = ? AND metric = 'charging_state' "
            "  AND ts BETWEEN ? AND ? "
            "ORDER BY ts",
            (device, midnight_ts, now_ts),
        ) as cur:
            async for ts, v in cur:
                state_rows.append((int(ts), str(v)))

        # Collapse consecutive identical states into segments + tally
        ribbon: list[dict[str, Any]] = []
        per_state: dict[str, int] = {}
        if state_rows:
            seg_start, seg_state = state_rows[0]
            prev_ts = seg_start
            for ts, st in state_rows[1:]:
                if st != seg_state:
                    dur = ts - seg_start
                    ribbon.append({"start_ts": seg_start, "end_ts": ts, "state": seg_state})
                    per_state[seg_state] = per_state.get(seg_state, 0) + dur
                    seg_start, seg_state = ts, st
                prev_ts = ts
            # Close the trailing segment up to now.
            dur = now_ts - seg_start
            ribbon.append({"start_ts": seg_start, "end_ts": now_ts, "state": seg_state})
            per_state[seg_state] = per_state.get(seg_state, 0) + dur

        return {
            "device":              device,
            "since_ts":            midnight_ts,
            "now_ts":              now_ts,
            "lifetime_energy_wh":  round(lifetime_wh, 1),
            "today_energy_wh":     round(today_wh, 1),
            "today_active_s":      today_active_s,
            "today_state_seconds": per_state,
            "state_ribbon":        ribbon,
        }


    async def battery_lifetime_stats(self, device: str) -> dict[str, Any]:
        """Compute coulomb-counted lifetime Ah in/out + cycle count for a pack.

        Trapezoid-integrates `current_a` across every poll we have for this
        device. Cycles are referenced against the latest known `capacity_ah`
        (also fetched here, defaulting to 100 Ah for the typical LFP pack).

        For multi-year data this becomes expensive, at v0.0.x volumes it's
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

        # Latest known capacity for the pack, needed to express cycle equivalence
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
        first interval inside the window is fully accounted for,
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
        least one pack's worth, i.e. ah_in >= capacity_ah. With less
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

    # Width of the raw-sample window aggregated per query in closed_rollups_after
    # (a multiple of 60 so windows never split a bucket).
    ROLLUP_WINDOW_S = 2 * 3600

    async def closed_rollups_after(
        self, after_ts: int, *, now: int | None = None, limit: int = 5000,
    ) -> list[tuple[int, str, str, float, float, float, int]]:
        """Closed 1-minute buckets strictly newer than `after_ts`, oldest
        first, as `(bucket_ts, device, metric, avg, min, max, n)`.

        Used by the cloud rollup sync. Two details matter for correctness:

        * A bucket is only returned once its minute is fully in the past.
          `rollup_and_purge` re-rolls the still-open minute into
          `samples_1min` as a partial row, so reading that table directly
          could ship half a bucket and advance the watermark past it.
          Recent buckets are therefore aggregated from raw `samples`.
        * Buckets older than raw retention only exist in `samples_1min`
          (an appliance that was offline for more than a week), so those
          come from there.

        `limit` caps rows. When it bites, the trailing bucket is dropped
        so a bucket is never split across batches: the caller can always
        advance its watermark to the last returned `bucket_ts`.
        """
        if self._db is None:
            raise RuntimeError("Store not open")
        now = now if now is not None else int(time.time())
        newest_closed = (now // 60) * 60 - 60          # last fully elapsed minute
        start = (int(after_ts) // 60) * 60 + 60
        if start > newest_closed:
            return []
        raw_floor = ((now - self._retention_raw_s) // 60) * 60 + 60
        rows: list[tuple[int, str, str, float, float, float, int]] = []
        if start < raw_floor:
            async with self._db.execute(
                "SELECT bucket_ts, device, metric, avg, min, max, n "
                "FROM samples_1min WHERE bucket_ts >= ? AND bucket_ts < ? "
                "ORDER BY bucket_ts, device, metric LIMIT ?",
                (start, min(raw_floor, newest_closed + 60), limit + 1),
            ) as cur:
                rows.extend([(int(a), b, c, float(d), float(e), float(f), int(g))
                             async for a, b, c, d, e, f, g in cur])
        if len(rows) <= limit and newest_closed >= raw_floor:
            # Aggregate raw samples in bounded time windows, oldest first, and
            # stop as soon as limit+1 rows are in hand. One unbounded
            # GROUP BY ... LIMIT aggregates the whole remaining range before
            # LIMIT applies (seconds of work, blocking the shared connection,
            # when a day or a week of 5 s data is pending).
            w_start = max(start, raw_floor)
            w_end = newest_closed + 60
            while w_start < w_end and len(rows) <= limit:
                hi = min(w_start + self.ROLLUP_WINDOW_S, w_end)
                async with self._db.execute(
                    "SELECT (ts / 60) * 60 AS b, device, metric, "
                    "       AVG(value), MIN(value), MAX(value), COUNT(*) "
                    "FROM samples WHERE ts >= ? AND ts < ? "
                    "GROUP BY b, device, metric ORDER BY b, device, metric LIMIT ?",
                    (w_start, hi, limit + 1 - len(rows)),
                ) as cur:
                    rows.extend([(int(a), b, c, float(d), float(e), float(f), int(g))
                                 async for a, b, c, d, e, f, g in cur])
                w_start = hi
        if len(rows) > limit:
            last = rows[limit][0]
            rows = [r for r in rows[:limit] if r[0] < last]
            if not rows:           # one bucket wider than the limit: ship it whole
                async with self._db.execute(
                    "SELECT (ts / 60) * 60 AS b, device, metric, "
                    "       AVG(value), MIN(value), MAX(value), COUNT(*) "
                    "FROM samples WHERE ts >= ? AND ts < ? "
                    "GROUP BY b, device, metric", (last, last + 60),
                ) as cur:
                    rows = [(int(a), b, c, float(d), float(e), float(f), int(g))
                            async for a, b, c, d, e, f, g in cur]
        return rows
