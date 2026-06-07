"""Auto-apply policy (Phase C, cloud#15).

A pure decision: given what the daily check found, whether the release
verified, and the user's settings — should the daemon apply it *now*? No I/O,
no side effects. The scheduler calls this and, only on a True, fires
wattpost-update (which re-verifies and does the atomic swap with health
rollback).

Gates, in order (first failure wins, with a human-readable reason):
  1. auto-apply is opted in
  2. there is a newer release (the checker already computed has_update)
  3. the release is on the channel this box follows
  4. the release signature verifies against the pinned key (fail-closed)
  5. the release has "baked" >= min_age_hours (the canary window)

Gate 4 means an unverified or unsigned release can never auto-apply, even with
every other condition satisfied.
"""
from __future__ import annotations

from datetime import datetime


def should_auto_apply(
    *,
    auto_apply_enabled: bool,
    has_update: bool,
    release_channel: str,
    configured_channel: str,
    signature_verified: bool,
    published_at: datetime | None,
    now: datetime,
    min_age_hours: int,
) -> tuple[bool, str]:
    """Return ``(apply_now, reason)``. ``apply_now`` is True only when every
    gate passes; ``reason`` explains the first failing gate (or ``"ok"``)."""
    if not auto_apply_enabled:
        return (False, "auto-apply disabled")
    if not has_update:
        return (False, "already up to date")
    if release_channel != configured_channel:
        return (False, f"channel mismatch (release={release_channel}, box={configured_channel})")
    if not signature_verified:
        return (False, "release signature not verified")
    if published_at is None:
        return (False, "release has no publish timestamp")
    age_hours = (now - published_at).total_seconds() / 3600.0
    if age_hours < min_age_hours:
        return (False, f"not baked yet ({age_hours:.0f}h < {min_age_hours}h)")
    return (True, "ok")
