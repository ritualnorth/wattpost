"""Demo-mode synthetic poller.

When the daemon is launched with WATTPOST_DEMO=1, the scheduler swaps the
real BLE-driven `Poller` for a `SyntheticPoller` that produces believable
telemetry without any real hardware. This is what runs on
`demo.wattpost.io` so visitors can poke around a live dashboard before
they buy a Pi.

Design choices:

  * Time-of-day driven, not "elapsed since startup" — anyone landing on
    the demo should see something consistent with the wall-clock view
    (mid-afternoon = lots of PV, 3 AM = batteries drawing).
  * Plausible noise — perfect sine waves read as a toy. Each metric
    gets a small jittered offset on every poll.
  * Believable inter-pack drift — three batteries report SoC values
    that drift by a few % so the dashboard's per-pack view doesn't look
    suspicious.
  * Locked schema — emits exactly the fields `_compute_bank_aggregate`
    and the dashboard already expect, so no UI changes are needed.
  * No real DB writes outside the normal path — the scheduler still
    calls store.record_poll(result), so demo data lands in the same
    SQLite store and history/CSV/forecast all work end-to-end.

The synthetic devices' labels deliberately match the canonical naming
the rest of the codebase uses (battery_0..N, rover_mppt, shunt) so
metric panels render the same as a real install.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any

from .config import Config


# ----- tunables -----

# Bank capacity (Ah) and nominal voltage — pick values that match a
# realistic mid-size off-grid bank (3 × 100 Ah @ 12.8 V LiFePO4).
BANK_CAPACITY_AH = 300.0
NOMINAL_V       = 12.8
PV_PEAK_W       = 600.0   # 600 W array — a couple of 280 W panels
BASE_LOAD_W     = 50.0    # always-on (fridge, controllers, fans)
EVENING_LOAD_W  = 220.0   # extra evening load (lights, electronics)


def _hour_of_day() -> float:
    """Returns the local time-of-day as a 0.0-24.0 float."""
    t = time.localtime()
    return t.tm_hour + t.tm_min / 60.0 + t.tm_sec / 3600.0


def _pv_curve(hod: float) -> float:
    """Watts produced by the PV array at hour `hod`. Bell curve peaked
    at solar noon (12:30 local), zero at night."""
    # Useful daylight window: 06:30 → 18:30. Outside that range, no PV.
    sunrise, sunset = 6.5, 18.5
    if hod < sunrise or hod > sunset:
        return 0.0
    # Half-sine across the window, peak at the midpoint.
    progress = (hod - sunrise) / (sunset - sunrise)   # 0..1
    intensity = math.sin(progress * math.pi)
    return PV_PEAK_W * intensity


def _load_curve(hod: float) -> float:
    """Watts drawn by loads. Base load all day + evening hump."""
    base = BASE_LOAD_W
    # Evening bump 17:00–23:00, smoothed with a half-sine.
    if 17.0 <= hod <= 23.0:
        bump_progress = (hod - 17.0) / (23.0 - 17.0)
        base += EVENING_LOAD_W * math.sin(bump_progress * math.pi)
    # Tiny morning hum (kettle, coffee) 07:00–09:00.
    if 7.0 <= hod <= 9.0:
        base += 80.0 * math.sin((hod - 7.0) / 2.0 * math.pi)
    return base


# Track SoC as a daemon-lifetime variable so it integrates over actual
# polling cadence — that way history charts show realistic curves
# instead of resetting on every poll.
_state = {
    "soc_pct":    62.0,    # mid-range default
    "last_ts":    None,    # last poll time, for energy integration
}


def _step_soc(now: float) -> float:
    """Advance the bank's SoC based on net flow since the last poll."""
    last = _state["last_ts"]
    _state["last_ts"] = now
    if last is None:
        return _state["soc_pct"]

    dt_h = (now - last) / 3600.0
    if dt_h <= 0 or dt_h > 1.0:
        # Either negative time (clock skew) or a huge gap (resumed
        # after suspend). Don't accumulate from garbage — just keep
        # the existing SoC and let the next interval do real work.
        return _state["soc_pct"]

    hod = _hour_of_day()
    pv = _pv_curve(hod)
    load = _load_curve(hod)
    net_w = pv - load
    # Pretend 12.8 V nominal — 1% SoC ~= 3 Ah for our 300 Ah bank ~=
    # 38.4 Wh. So delta-soc = net_w * dt_h / (BANK_CAPACITY_AH * NOMINAL_V) * 100.
    bank_wh = BANK_CAPACITY_AH * NOMINAL_V
    delta_soc = (net_w * dt_h / bank_wh) * 100.0
    new_soc = max(15.0, min(99.5, _state["soc_pct"] + delta_soc))
    _state["soc_pct"] = new_soc
    return new_soc


def _jitter(value: float, pct: float = 0.02) -> float:
    """Small random offset (±pct of value) so consecutive polls aren't
    bit-identical. Keeps the dashboard from looking too perfect."""
    return value * (1.0 + random.uniform(-pct, pct))


class SyntheticPoller:
    """Drop-in replacement for orchestrator.Poller, no real hardware.

    Same `poll()` interface — returns the same shape of result dict so
    storage.record_poll, the bank aggregate, the live dashboard, the
    history endpoint, and the cloud heartbeat builder all work without
    knowing anything has been replaced.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    async def open(self) -> None:
        # No transports to open. Stub so the scheduler's lifecycle is
        # symmetrical with the real Poller.
        pass

    async def close(self) -> None:
        pass

    async def poll(self) -> dict[str, Any]:
        now = time.time()
        started = now

        hod = _hour_of_day()
        soc = _step_soc(now)
        pv_w = _pv_curve(hod)
        load_w = _load_curve(hod)
        net_w = pv_w - load_w
        # Pack voltage tracks SoC roughly — 13.4 V full, 12.0 V at 20%.
        bank_v = _jitter(12.0 + (soc / 100.0) * 1.4, 0.005)
        bank_a = _jitter(net_w / bank_v, 0.03)

        # Today's PV energy: integrate from sunrise to now. Cheap
        # approximation — area under the half-sine times the peak.
        sunrise = 6.5
        if hod <= sunrise:
            pv_today_wh = 0.0
        else:
            sunset = 18.5
            # how much of today's bell-curve has elapsed
            done = min(1.0, (hod - sunrise) / (sunset - sunrise))
            # full-day integral = peak * (window_hours) * 2/π
            full_day_wh = PV_PEAK_W * (sunset - sunrise) * (2.0 / math.pi)
            # progress along the curve (cosine half-curve cumulative)
            cum = 0.5 * (1.0 - math.cos(done * math.pi))
            pv_today_wh = full_day_wh * cum

        # Per-pack SoC: three batteries with a few % drift from the bank.
        # Locked random per-pack drift so it doesn't jitter wildly
        # between polls — caller still sees small jitter via _jitter().
        pack_drifts = [+0.6, -0.3, -1.1]
        packs = {}
        for i, drift in enumerate(pack_drifts):
            pack_soc = max(15.0, min(99.5, soc + drift))
            pack_v = _jitter(bank_v / 1.0, 0.004)  # all paralleled → same V
            pack_a = _jitter(bank_a / 3.0, 0.10)   # ~split across packs
            packs[f"battery_{i}"] = {
                "_vendor":         "renogy",
                "_kind":           "smart_battery",
                "_slave_id":       0x30 + i,
                "voltage_v":       round(pack_v, 3),
                "current_a":       round(pack_a, 3),
                "soc_pct":         round(pack_soc, 1),
                "temperature_c":   round(_jitter(22.0, 0.05), 1),
                "cycle_count":     180 + i * 12,
                "remaining_ah":    round(BANK_CAPACITY_AH / 3 * pack_soc / 100, 2),
                "capacity_ah":     round(BANK_CAPACITY_AH / 3, 1),
                # Cell drift: small but non-zero so the bank panel's
                # "worst pack drift" indicator has something to show.
                "cell_drift_v":    round(0.012 + i * 0.004, 4),
            }

        # Shunt: wins the bank aggregate. Reports the totals.
        shunt = {
            "_vendor":           "renogy",
            "_kind":             "shunt",
            "_slave_id":         0x60,
            "voltage_v":         round(bank_v, 3),
            "current_a":         round(bank_a, 3),
            "power_w":           round(net_w, 1),
            "soc_pct":           round(soc, 1),
            "remaining_ah":      round(BANK_CAPACITY_AH * soc / 100, 2),
            "capacity_ah":       BANK_CAPACITY_AH,
            "bank_capacity_ah":  BANK_CAPACITY_AH,
            "temperature_c":     round(_jitter(22.0, 0.05), 1),
        }

        # Charge controller / MPPT
        rover = {
            "_vendor":            "renogy",
            "_kind":              "charge_controller",
            "_slave_id":          0x01,
            "battery_voltage_v":  round(bank_v, 3),
            "battery_current_a":  round(max(0.0, pv_w / max(bank_v, 1.0)), 3),
            "battery_soc_pct":    round(soc, 1),
            "pv_voltage_v":       round(_jitter(36.0 if pv_w > 5 else 0, 0.04), 2),
            "pv_current_a":       round(pv_w / 36.0 if pv_w > 5 else 0, 3),
            "pv_power_w":         round(pv_w, 1),
            "energy_today_wh":    round(pv_today_wh, 1),
            "controller_temp_c":  round(_jitter(24.0 + (pv_w / 100.0), 0.05), 1),
            "battery_temp_c":     round(_jitter(22.0, 0.05), 1),
        }

        result: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "devices":   {**packs, "shunt_main": shunt, "rover_mppt": rover},
            "errors":    [],
            "elapsed_seconds": round(time.time() - started, 3),
        }
        return result


# ---------------- history seeding ----------------

def _snapshot_at(ts: float, soc_pct: float) -> dict[str, Any]:
    """Build the same shape SyntheticPoller.poll() returns, for a
    given past timestamp + carry-forward SoC. Used by seed_history()."""
    local = time.localtime(ts)
    hod = local.tm_hour + local.tm_min / 60.0
    pv_w = _pv_curve(hod)
    load_w = _load_curve(hod)
    net_w = pv_w - load_w

    bank_v = 12.0 + (soc_pct / 100.0) * 1.4
    bank_a = net_w / bank_v if bank_v > 0 else 0.0

    # Cheaper pack derivation for backfill — no per-pack jitter, just
    # the headline numbers. Charts read from the shunt aggregate anyway.
    packs = {}
    pack_drifts = [+0.6, -0.3, -1.1]
    for i, drift in enumerate(pack_drifts):
        pack_soc = max(15.0, min(99.5, soc_pct + drift))
        packs[f"battery_{i}"] = {
            "_vendor":         "renogy",
            "_kind":           "smart_battery",
            "_slave_id":       0x30 + i,
            "voltage_v":       round(bank_v, 3),
            "current_a":       round(bank_a / 3.0, 3),
            "soc_pct":         round(pack_soc, 1),
            "temperature_c":   22.0 + i * 0.5,
            "cycle_count":     180 + i * 12,
            "remaining_ah":    round(BANK_CAPACITY_AH / 3 * pack_soc / 100, 2),
            "capacity_ah":     round(BANK_CAPACITY_AH / 3, 1),
            "cell_drift_v":    0.012 + i * 0.004,
        }
    shunt = {
        "_vendor":          "renogy",
        "_kind":            "shunt",
        "_slave_id":        0x60,
        "voltage_v":        round(bank_v, 3),
        "current_a":        round(bank_a, 3),
        "power_w":          round(net_w, 1),
        "soc_pct":          round(soc_pct, 1),
        "remaining_ah":     round(BANK_CAPACITY_AH * soc_pct / 100, 2),
        "capacity_ah":      BANK_CAPACITY_AH,
        "bank_capacity_ah": BANK_CAPACITY_AH,
        "temperature_c":    22.0,
    }
    # PV today integration restarts each calendar day.
    midnight = ts - (hod * 3600.0)
    pv_today_wh = 0.0
    # Approximate cumulative-half-sine integral up to hod:
    sunrise, sunset = 6.5, 18.5
    if hod > sunrise:
        done = min(1.0, (hod - sunrise) / (sunset - sunrise))
        full_day_wh = PV_PEAK_W * (sunset - sunrise) * (2.0 / math.pi)
        cum = 0.5 * (1.0 - math.cos(done * math.pi))
        pv_today_wh = full_day_wh * cum
    rover = {
        "_vendor":            "renogy",
        "_kind":              "charge_controller",
        "_slave_id":          0x01,
        "battery_voltage_v":  round(bank_v, 3),
        "battery_current_a":  round(max(0.0, pv_w / max(bank_v, 1.0)), 3),
        "battery_soc_pct":    round(soc_pct, 1),
        "pv_voltage_v":       round(36.0 if pv_w > 5 else 0, 2),
        "pv_current_a":       round(pv_w / 36.0 if pv_w > 5 else 0, 3),
        "pv_power_w":         round(pv_w, 1),
        "energy_today_wh":    round(pv_today_wh, 1),
        "controller_temp_c":  24.0,
        "battery_temp_c":     22.0,
    }
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
        "devices":   {**packs, "shunt_main": shunt, "rover_mppt": rover},
        "errors":    [],
        "elapsed_seconds": 0.0,
    }


async def seed_history(store, days: int = 30, step_minutes: int = 60) -> int:
    """Backfill `days` of synthetic history into the store at
    `step_minutes` cadence. Idempotent — bails fast if the store
    already has samples from the last 2 hours (i.e. a live run, not a
    fresh demo restart).

    Returns the number of synthetic polls inserted.
    """
    import logging
    log = logging.getLogger(__name__)
    # Idempotency check via the existing `latest` table.
    try:
        latest = await store.get_latest()
        if latest and any(
            d.get("_updated_at", 0) > time.time() - 7200 for d in latest.values()
        ):
            log.info("demo history: store already populated, skipping backfill")
            return 0
    except Exception:
        # Fresh DB with no tables — fall through and seed.
        pass

    log.info("demo history: seeding %dd × %dm cadence", days, step_minutes)
    now = int(time.time())
    start = now - days * 86400
    step = step_minutes * 60

    # SoC starts at the daily-low end so the integration ramps up
    # plausibly during the first morning.
    soc = 55.0
    bank_wh = BANK_CAPACITY_AH * NOMINAL_V
    n = 0
    ts = start
    while ts < now:
        local = time.localtime(ts)
        hod = local.tm_hour + local.tm_min / 60.0
        net_w = _pv_curve(hod) - _load_curve(hod)
        dt_h = step / 3600.0
        soc = max(15.0, min(99.5, soc + (net_w * dt_h / bank_wh) * 100.0))
        snap = _snapshot_at(ts, soc)
        await store.record_poll(snap, ts_override=ts)
        n += 1
        ts += step

    # Carry SoC forward into the live poller so the first live point
    # connects smoothly with the end of the backfill.
    _state["soc_pct"] = soc
    _state["last_ts"] = float(now)
    log.info("demo history: seeded %d polls; SoC handed off at %.1f%%", n, soc)
    return n
