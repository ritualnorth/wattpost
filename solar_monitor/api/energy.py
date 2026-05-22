"""Energy-today overview endpoint — powers the chart at the top of /history.

Replaces five separate per-metric history requests + client-side
alignment with a single endpoint that returns one shared `ts` grid +
parallel series + pre-computed kWh totals + self-powered breakdown.
The frontend hands the series straight to uPlot.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from litestar import get
from litestar.datastructures import State

from ..storage.sqlite import Store


# Source / charger device kinds. Anything that *injects* power onto
# the bus belongs here. dcdc/dcdc_xs are DC-DC chargers (e.g. Orion-Tr,
# Renogy DCC50S) — they show up as sources because they pull from
# alternator/aux + push onto the bank bus.
_SOURCE_KINDS = ("charge_controller", "mppt", "dcdc", "dcdc_charger", "dcdc_xs")
_CHARGER_KINDS = ("ac_charger",)


@get("/api/energy/today")
async def energy_today(
    state: State,
    since: int | None = None,
    until: int | None = None,
    bucket: int | None = None,
) -> dict[str, Any]:
    """Aligned multi-series + aggregates for the Energy-today view.

    Defaults to the local calendar day (midnight → now) at 5-minute
    buckets. Override via ?since=, ?until=, ?bucket= for week/month
    views (Slice 2). All returned series share the same `ts` array.
    """
    store: Store = state["store"]
    return await compute_energy(store, since=since, until=until, bucket=bucket)


async def compute_energy(
    store: Store,
    *,
    since: int | None = None,
    until: int | None = None,
    bucket: int | None = None,
) -> dict[str, Any]:
    """Helper extracted from the HTTP endpoint so background services
    (cloud heartbeat) can reuse the aggregation without a Litestar
    State context. Same return shape as `/api/energy/today`."""
    now_ts = int(time.time())
    if since is None:
        local = time.localtime(now_ts)
        since = int(time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        ))
    if until is None:
        until = now_ts
    bucket_s = int(bucket) if bucket and int(bucket) > 0 else 300

    devices = await store.list_devices()
    pv_labels      = [d["label"] for d in devices if d.get("kind") in _SOURCE_KINDS]
    charger_labels = [d["label"] for d in devices if d.get("kind") in _CHARGER_KINDS]

    async def _h(label: str, metric: str) -> dict[str, Any]:
        return await store.get_history(label, metric, since, until, bucket_seconds=bucket_s)

    # PV: pv_power_w. DC-DC chargers use output_power_w; bundle both.
    pv_tasks: list[asyncio.Task[dict[str, Any]]] = []
    for lbl in pv_labels:
        for m in ("pv_power_w", "output_power_w"):
            pv_tasks.append(asyncio.create_task(_h(lbl, m)))
    # AC chargers: output_1/2/3 (multi-bank models).
    chg_tasks: list[asyncio.Task[dict[str, Any]]] = []
    for lbl in charger_labels:
        for m in ("output_1_power_w", "output_2_power_w", "output_3_power_w"):
            chg_tasks.append(asyncio.create_task(_h(lbl, m)))

    pv_series, chg_series, bank_pw, bank_soc, bank_temp = await asyncio.gather(
        asyncio.gather(*pv_tasks)  if pv_tasks  else _empty_list(),
        asyncio.gather(*chg_tasks) if chg_tasks else _empty_list(),
        _h("bank", "power_w"),
        _h("bank", "soc_pct"),
        _h("bank", "temperature_c"),
    )

    # Build the shared ts grid off whichever series has densest coverage.
    # bank.power_w is the most reliable (poll cycle persists it every
    # snapshot). Fall back to a synthetic grid if empty.
    grid_ts: list[int] = list(bank_pw.get("ts") or [])
    if not grid_ts:
        start = since - (since % bucket_s)
        grid_ts = list(range(start, until + 1, bucket_s))

    solar_w   = _sum_aligned(pv_series,  grid_ts)
    charger_w = _sum_aligned(chg_series, grid_ts)
    bank_w    = _align(bank_pw,   grid_ts)
    soc_pct   = _align(bank_soc,  grid_ts)
    temp_c    = _align(bank_temp, grid_ts)

    # kWh integration — each non-null bucket contributes
    # `value_W × (bucket_s / 3600) = Wh`. Positive bank_w = charging,
    # negative = discharging.
    factor = bucket_s / 3600.0
    solar_wh    = sum(v for v in solar_w   if v is not None and v > 0) * factor
    charger_wh  = sum(v for v in charger_w if v is not None and v > 0) * factor
    bank_in_wh  = sum( v for v in bank_w if v is not None and v > 0) * factor
    bank_out_wh = sum(-v for v in bank_w if v is not None and v < 0) * factor
    sources_wh  = solar_wh + charger_wh
    load_wh     = max(0.0, sources_wh + bank_out_wh - bank_in_wh)

    # Self-powered breakdown — what fraction of the load was served
    # by each input. Per-bucket attribution: in each bucket, load is
    # approximately (sources_in + bank_out − bank_in); we attribute
    # the served portion proportionally to each source's share of
    # total power entering the bus. Coarse but matches the visual
    # split the user expects ("most of today was solar").
    served = {"solar": 0.0, "battery": 0.0, "charger": 0.0}
    for i, _t in enumerate(grid_ts):
        s = (solar_w[i]   or 0.0) if solar_w[i]   is not None and solar_w[i]   > 0 else 0.0
        c = (charger_w[i] or 0.0) if charger_w[i] is not None and charger_w[i] > 0 else 0.0
        bw = bank_w[i] if bank_w[i] is not None else 0.0
        b_out = -bw if bw < 0 else 0.0
        b_in  =  bw if bw > 0 else 0.0
        total_in = s + c + b_out
        if total_in <= 0:
            continue
        load_in_bucket = max(0.0, s + c + b_out - b_in)
        if load_in_bucket <= 0:
            continue
        served["solar"]   += s     / total_in * load_in_bucket * factor
        served["charger"] += c     / total_in * load_in_bucket * factor
        served["battery"] += b_out / total_in * load_in_bucket * factor
    total_served = sum(served.values()) or 1.0
    breakdown = {k: round(100.0 * v / total_served, 1) for k, v in served.items()}

    return {
        "since_ts":       since,
        "until_ts":       until,
        "bucket_seconds": bucket_s,
        "series": {
            "ts":        grid_ts,
            "solar_w":   solar_w,
            "charger_w": charger_w,
            "bank_w":    bank_w,
            "soc_pct":   soc_pct,
            "temp_c":    temp_c,
        },
        "totals": {
            "solar_wh":           round(solar_wh,    1),
            "charger_wh":         round(charger_wh,  1),
            "bank_charged_wh":    round(bank_in_wh,  1),
            "bank_discharged_wh": round(bank_out_wh, 1),
            "load_wh":            round(load_wh,     1),
        },
        "self_powered": breakdown,
    }


async def _empty_list() -> list[dict[str, Any]]:
    """gather() of zero tasks is illegal; return an empty list instead."""
    return []


def _align(series: dict[str, Any], grid_ts: list[int]) -> list[float | None]:
    """Project one series onto the shared `grid_ts`. Both share the
    bucket boundary so equal ts means same bucket; values not present
    in the input become `None` (uPlot treats None as a gap)."""
    s_ts = series.get("ts") or []
    s_v  = series.get("values") or []
    if not s_ts:
        return [None] * len(grid_ts)
    d = {int(t): float(v) for t, v in zip(s_ts, s_v) if v is not None}
    return [d.get(int(t)) for t in grid_ts]


def _sum_aligned(series_list: list[dict[str, Any]], grid_ts: list[int]) -> list[float | None]:
    """Sum multiple series on the same grid. None counts as zero unless
    EVERY contributor is None at that bucket (then result is None too —
    distinguishes "no data" from "zero W")."""
    if not series_list:
        return [None] * len(grid_ts)
    out: list[float | None] = [None] * len(grid_ts)
    aligned = [_align(s, grid_ts) for s in series_list]
    for i in range(len(grid_ts)):
        bucket_vals = [a[i] for a in aligned if a[i] is not None]
        if bucket_vals:
            out[i] = sum(bucket_vals)
    return out
