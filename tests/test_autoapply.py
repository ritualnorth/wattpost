"""Tests for the auto-apply policy gate (Phase C, cloud#15).

Each gate gets a case, in particular: an unverified release must never apply
(fail-closed), and a not-yet-baked release waits.
"""
from datetime import datetime, timedelta, timezone

from solar_monitor.update.autoapply import should_auto_apply

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)


def _kwargs(**over):
    base = dict(
        auto_apply_enabled=True,
        has_update=True,
        release_channel="stable",
        configured_channel="stable",
        signature_verified=True,
        published_at=NOW - timedelta(hours=72),  # well past the 48h bake
        now=NOW,
        min_age_hours=48,
    )
    base.update(over)
    return base


def test_all_gates_pass():
    ok, reason = should_auto_apply(**_kwargs())
    assert ok is True and reason == "ok"


def test_disabled_blocks():
    ok, reason = should_auto_apply(**_kwargs(auto_apply_enabled=False))
    assert ok is False and "disabled" in reason


def test_no_update_blocks():
    ok, reason = should_auto_apply(**_kwargs(has_update=False))
    assert ok is False and "up to date" in reason


def test_channel_mismatch_blocks():
    ok, reason = should_auto_apply(**_kwargs(release_channel="beta"))
    assert ok is False and "channel mismatch" in reason


def test_unverified_signature_never_applies():
    # The load-bearing gate: every other condition is satisfied.
    ok, reason = should_auto_apply(**_kwargs(signature_verified=False))
    assert ok is False and "not verified" in reason


def test_missing_timestamp_blocks():
    ok, reason = should_auto_apply(**_kwargs(published_at=None))
    assert ok is False and "no publish timestamp" in reason


def test_not_baked_yet_waits():
    ok, reason = should_auto_apply(**_kwargs(published_at=NOW - timedelta(hours=2)))
    assert ok is False and "not baked" in reason


def test_zero_age_applies_immediately():
    ok, _ = should_auto_apply(**_kwargs(min_age_hours=0,
                                        published_at=NOW - timedelta(minutes=1)))
    assert ok is True
