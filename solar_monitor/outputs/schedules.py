"""Output schedule engine (Phase B of #104).

Walks the `output_schedules` table once per poll cycle and fires any
schedule whose computed fire-time falls in the window since the
previous check. Supports three trigger kinds:

  * `time`    , fires at a fixed HH:MM in the appliance's local
                 timezone.
  * `sunrise` , fires at today's sunrise ± `offset_min` minutes.
                 Requires the weather integration to be configured
                 (we read sunrise_ts from the cached Open-Meteo
                 blob); otherwise the trigger silently no-ops.
  * `sunset`  , same shape as sunrise, for dusk schedules.

Days-of-week filtering via `days_mask` (bitmask MTWTFSS, Monday is
bit 0). Defaults to 127 = every day.

Idempotency: every fire updates `last_run_at`. The check "has this
schedule already fired today" uses `last_run_at >= today_midnight
&& last_run_at >= fire_ts`, so the daemon can be restarted
without re-firing today's schedules and a single schedule never
fires twice in one day.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def _day_of_week(ts: int) -> int:
    """Monday = 0, Sunday = 6. Matches `datetime.weekday()`."""
    return _dt.datetime.fromtimestamp(ts).weekday()


def _midnight_ts(ts: int) -> int:
    """Local-midnight unix-seconds of the day containing `ts`."""
    dt = _dt.datetime.fromtimestamp(ts)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


def _parse_hhmm(s: str | None) -> tuple[int, int] | None:
    if not s or ":" not in s:
        return None
    try:
        h_str, m_str = s.split(":", 1)
        h, m = int(h_str), int(m_str)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h, m
    except ValueError:
        return None


async def compute_fire_time(
    sched: dict[str, Any],
    *,
    now_ts: int,
    weather_cache: dict[str, Any] | None,
) -> int | None:
    """Return the unix-second timestamp at which `sched` should fire
    today, or None if the trigger doesn't apply (no time / weather
    unavailable / unsupported kind).

    Pure function, no I/O. The scheduler tick calls this for every
    enabled schedule and compares the result against `now_ts` and
    the schedule's `last_run_at`.
    """
    kind = sched.get("trigger_kind")
    midnight = _midnight_ts(now_ts)
    if kind == "time":
        hm = _parse_hhmm(sched.get("trigger_time"))
        if hm is None:
            return None
        h, m = hm
        return midnight + h * 3600 + m * 60
    if kind in ("sunrise", "sunset"):
        if not weather_cache:
            return None
        key = "sunrise_ts" if kind == "sunrise" else "sunset_ts"
        base_ts = weather_cache.get(key)
        if not isinstance(base_ts, (int, float)):
            return None
        offset = int(sched.get("offset_min") or 0)
        return int(base_ts) + offset * 60
    return None


async def fire_due_schedules(
    *, store, outputs_service, weather_cache: dict[str, Any] | None,
    now_ts: int,
) -> int:
    """Walk every enabled schedule, fire each one whose effective
    fire-time is in the past AND that hasn't fired yet today. Returns
    the number of schedules fired this tick (for logging)."""
    schedules = await store.list_schedules(None)
    if not schedules:
        return 0
    today_dow = _day_of_week(now_ts)
    today_midnight = _midnight_ts(now_ts)
    fired = 0
    for sched in schedules:
        if not sched.get("enabled"):
            continue
        # Days-of-week mask. Monday is bit 0; bit set = allowed.
        days_mask = int(sched.get("days_mask") or 127)
        if not (days_mask & (1 << today_dow)):
            continue
        fire_ts = await compute_fire_time(
            sched, now_ts=now_ts, weather_cache=weather_cache,
        )
        if fire_ts is None or fire_ts > now_ts:
            continue
        last_run = sched.get("last_run_at") or 0
        # "Already fired today" check, defend against re-firing after
        # a daemon restart that lands within the same calendar day.
        if last_run >= today_midnight and last_run >= fire_ts:
            continue

        # Fire.
        output_id = sched["output_id"]
        on = sched.get("action") == "on"
        try:
            res = await outputs_service.toggle(
                output_id, on, by=f"schedule:{sched['id']}",
            )
            ok = bool(res.get("ok"))
            detail = res.get("detail") or ""
        except KeyError:
            ok = False
            detail = "output_not_found"
        except Exception as e:
            log.exception("schedule %d fire crashed", sched["id"])
            ok = False
            detail = f"{type(e).__name__}:{e}"
        result_str = "ok" if ok else f"fail:{detail or 'unknown'}"
        await store.mark_schedule_run(sched["id"], now_ts, result_str)
        log.info("schedule %d (%s %s @ %s) → %s",
                 sched["id"], output_id, sched["action"],
                 sched["trigger_kind"], result_str)
        fired += 1
    return fired


async def load_weather_cache(store) -> dict[str, Any] | None:
    """Helper: pull the cached current-weather blob (used for sunrise/
    sunset lookups). Returns None when weather isn't configured / no
    fetch has succeeded yet, schedules with sun-relative triggers
    silently skip until a weather fetch lands."""
    cached = await store.kv_get("weather:current")
    if cached is None:
        return None
    body, _updated_at = cached
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
