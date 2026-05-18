"""Shared "device went silent" plumbing for Victron BLE drivers (#171).

Every Victron driver reads via `transport.get_latest()`, which returns
None when no advertisement has landed within STALE_AFTER_SECONDS. The
naive handling — `return result` with just an `_errors` string — leaves
`/api/devices` returning the *previous* successful row from the
`latest` table forever, so the dashboard shows frozen values (e.g.
"15 A bulk") long after a charger has actually been switched off.

The fix: when stale, drivers still emit a numeric
`advertisement_age_s` (always-fresh real age, not the frozen value
from when the last advert landed). record_poll writes that into the
`latest` table on every cycle, so the dashboard can detect "this
device went silent N minutes ago" and grey it out / show a Silent
badge.
"""
from __future__ import annotations

from typing import Any


# Sentinel age written when the transport has never received an
# advertisement (`_latest_at == 0`). We don't know the true age but
# the dashboard's threshold is just `> 60 s`, and we need *some*
# numeric value to overwrite any stale row from the previous daemon
# run that might still be in the `latest` table. 24 h reads as
# "Silent — last heard a day ago" which is honest given we have no
# better measurement.
NEVER_SEEN_SENTINEL_S = 86400


def stamp_advertisement_age(result: dict[str, Any], transport: Any) -> None:
    """Add `advertisement_age_s` to `result` based on the transport's
    last_advertisement_age_s() — works whether the device is currently
    fresh or has gone silent. Drivers should call this on every poll
    cycle (fresh and stale paths) so the latest table reflects current
    silence-age rather than the last fresh-decode age.

    Critical: even when the transport reports None (no advert seen
    since daemon start), write the sentinel — otherwise the previous
    daemon's frozen `advertisement_age_s` from the `latest` table is
    never overwritten, and the dashboard keeps showing the device as
    fresh-but-old. See the post-restart edge case the original fix
    missed."""
    fn = getattr(transport, "last_advertisement_age_s", None)
    if fn is None:
        return
    try:
        age = fn()
    except Exception:
        return
    if age is None:
        result["advertisement_age_s"] = NEVER_SEEN_SENTINEL_S
        return
    result["advertisement_age_s"] = int(age)


def mark_silent(result: dict[str, Any], transport: Any) -> dict[str, Any]:
    """The 'no fresh advertisement' return path for every Victron
    driver. Sets advertisement_age_s (numeric — will be persisted by
    record_poll, so the dashboard can detect staleness) and a
    descriptive _errors entry.

    Drivers call this instead of:
        result["_errors"] = ["no advertisement received yet (or stale)"]
        return result
    """
    stamp_advertisement_age(result, transport)
    age = result.get("advertisement_age_s")
    if age is None:
        result["_errors"] = ["no advertisement received yet"]
    else:
        result["_errors"] = [
            f"no fresh advertisement (last seen {age}s ago)"
        ]
    return result
