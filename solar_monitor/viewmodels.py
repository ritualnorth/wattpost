"""View-models shared by the local API and the cloud publisher.

Each function turns the store into the exact JSON the dashboard renders. The
local routes call them directly and `cloud.views` publishes the same output
to the cloud, so both UIs show identical numbers from one implementation.
"""
from __future__ import annotations

import json
import time
from typing import Any

from litestar.exceptions import NotFoundException

from .storage.sqlite import Store


async def soc_envelope(store: Store) -> dict[str, Any]:
    """Min/max of bank.soc_pct since local midnight. Powers the
    "SoC today" cell on the Today tile, answers "did the bank get
    critically low overnight?" without opening History."""
    now = int(time.time())
    local = time.localtime(now)
    midnight = int(time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    ))
    lo, hi = await store.bank_soc_minmax(midnight, now)
    return {
        "since_ts": midnight,
        "now_ts":   now,
        "min_pct":  round(lo, 1) if lo is not None else None,
        "max_pct":  round(hi, 1) if hi is not None else None,
    }


async def runtime_forecast(store: Store) -> dict[str, Any]:
    """Battery runtime prediction (#99).

    Two layers:

      * **Naive**: rolling 1-hour avg load → hours to SoC 10% at
        present rate. Stable; survives a transient kettle.
      * **Forecast-aware**: integrate next 48h of forecast PV
        against the avg load and walk the bank's SoC down each
        hour. Reports either an absolute depletion timestamp OR a
        "reserve days" number if the forecast says you stay above
        10% indefinitely.

    The frontend renders both lines under the Hero tile's "until
    empty" so customers see both the worst-case (naive) and the
    expected (forecast)."""

    # Pull current bank state from the latest table.
    latest = await store.get_latest()
    bank = latest.get("bank") or {}
    soc_pct = bank.get("soc_pct")
    cap_ah = bank.get("capacity_ah")
    voltage = bank.get("voltage_v") or 12.8
    if not isinstance(soc_pct, (int, float)) or not isinstance(cap_ah, (int, float)) or cap_ah <= 0:
        return {"ok": False, "reason": "no_bank"}

    bank_wh = float(cap_ah) * float(voltage)
    reserve_pct = 10.0  # don't predict past 10% SoC, LFP wants headroom
    usable_wh = bank_wh * max(0.0, float(soc_pct) - reserve_pct) / 100.0

    # Rolling 1-hour average load (negative when discharging).
    avg_w = await store.rolling_load_avg(3600)
    naive: dict[str, Any] = {"avg_load_w": None, "hours_to_empty": None}
    if avg_w is not None:
        naive["avg_load_w"] = round(avg_w, 1)
        if avg_w < -5:  # discharging at >5 W
            naive["hours_to_empty"] = round(usable_wh / abs(avg_w), 2)
        elif avg_w > 5:
            # Charging, would never empty at this rate
            naive["status"] = "charging"
        else:
            naive["status"] = "idle"

    # Forecast-aware: try to load the cached forecast and walk hourly.
    forecast_result: dict[str, Any] = {"available": False}
    try:
        cached = await store.kv_get("forecast:pv")
        if cached is not None:
            body, _ = cached
            payload = json.loads(body)
            points = payload.get("points") or []
            # points: [{"ts": unix, "watts": float}, ...] hourly
            # We only care about future points.
            now_ts = int(time.time())
            future = [(int(p["ts"]), float(p.get("pv_w") or 0))
                      for p in points if int(p.get("ts", 0)) > now_ts]
            future.sort()
            if future and avg_w is not None and avg_w < -5:
                # Walk hourly: net = pv_w - |load_w|.
                # Stop when SoC hits reserve_pct.
                soc_wh = usable_wh
                load_w = abs(avg_w)
                depletion_ts = None
                survived_horizon = True
                prev_ts = now_ts
                for ts, pv_w in future:
                    dt_h = (ts - prev_ts) / 3600.0
                    net_w = float(pv_w) - load_w
                    soc_wh += net_w * dt_h
                    if soc_wh <= 0:
                        # Linear interpolate within this hour.
                        # frac = (current_soc_before_step) / (net_w * dt_h * -1)
                        prev_soc = soc_wh - net_w * dt_h
                        if net_w * dt_h < 0:
                            frac = prev_soc / (-net_w * dt_h)
                            depletion_ts = int(prev_ts + frac * (ts - prev_ts))
                        else:
                            depletion_ts = ts
                        survived_horizon = False
                        break
                    prev_ts = ts
                forecast_result = {
                    "available": True,
                    "horizon_hours": round((future[-1][0] - now_ts) / 3600.0, 1),
                    "depletion_ts":  depletion_ts,
                    "hours_to_empty": (
                        round((depletion_ts - now_ts) / 3600.0, 1)
                        if depletion_ts else None
                    ),
                    "reserves_indefinite": survived_horizon,
                }
    except Exception:
        # Forecast lookup is best-effort; never fail the endpoint.
        # Forecast walk failures are non-fatal; swallow.
        pass

    return {
        "ok": True,
        "now": {
            "soc_pct":   round(float(soc_pct), 1),
            "capacity_ah": round(float(cap_ah), 1),
            "voltage_v": round(float(voltage), 2),
            "usable_wh": round(usable_wh, 1),
        },
        "naive": naive,
        "forecast": forecast_result,
    }


async def battery_health(store: Store, days: int = 30) -> dict[str, Any]:
    """SoC residency histogram + cycle/lifetime numbers for the Battery
    Health tile (#109).

    Default window is 30 days, long enough to surface a real residency
    pattern, short enough to stay responsive on the 1-min/1-hour rollup
    tables. Caller can override via ?days=N (clamped 1-365)."""
    days = max(1, min(365, int(days)))
    now = int(time.time())
    return await store.battery_health_aggregate(now - days * 86400, now)


async def charger_stats(store: Store, label: str) -> dict[str, Any]:
    """Charger-specific aggregates for the device-detail page:
    lifetime kWh delivered, today's active-time + state breakdown,
    plus a `state_ribbon` (segments of charging_state across today)
    for the colored 24h timeline.

    Works for any device that exposes a power metric + charging_state:
      - ac_charger    → output_1_power_w
      - charge_controller → pv_power_w (Renogy MPPT etc.)
    """
    # Determine which power metric to integrate based on what fields
    # exist on the device, `latest` is the source of truth.
    all_latest = await store.get_latest()
    if label not in all_latest:
        raise NotFoundException(f"unknown device {label!r}")
    latest = all_latest[label]
    if "output_1_power_w" in latest:
        power_metric = "output_1_power_w"
    elif "pv_power_w" in latest:
        power_metric = "pv_power_w"
    else:
        return {"error": "device has no recognised charger power metric"}
    now = int(time.time())
    # Midnight LOCAL-time of today (uses the appliance's TZ).
    from datetime import datetime
    midnight = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return await store.charger_stats(label, power_metric, midnight, now)


async def efficiency(store: Store, label: str) -> dict[str, Any]:
    """SoC-corrected charge efficiency for one battery pack over a
    range of trailing windows. The shorter windows surface early, a
    7d efficiency drop tells you something's going wrong before the
    lifetime number catches up. The `reliable` flag on each window
    tracks whether the pack saw enough throughput for the number to
    be trustworthy."""
    now = int(time.time())
    windows = {
        "7d":   now - 7  * 86400,
        "30d":  now - 30 * 86400,
        "90d":  now - 90 * 86400,
    }
    out: dict[str, Any] = {"device": label, "windows": {}}
    for name, since in windows.items():
        out["windows"][name] = await store.battery_efficiency(label, since_ts=since)
    out["windows"]["lifetime"] = await store.battery_efficiency(label, since_ts=None)
    return out


async def load_heatmap(store: Store, days: int = 30) -> dict[str, Any]:
    """Hour-of-day × day-of-week mean discharge wattage over the last N days."""
    now = int(time.time())
    since = now - days * 86400
    return await store.load_heatmap(since, now)
