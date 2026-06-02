"""Tests for the release-channel selector (#11).

Covers the appliance-side channel plumbing without hitting the network:
normalisation of arbitrary input, the ?channel= URL the daily poll
builds, and that switching channels invalidates the cached "latest" so
has_update recomputes against the new stream.
"""
import asyncio

from solar_monitor.update.checker import (
    UpdateChecker,
    VALID_CHANNELS,
    DEFAULT_CHANNEL,
    normalize_channel,
)


def test_valid_channels_and_default():
    assert VALID_CHANNELS == ("stable", "beta", "edge")
    assert DEFAULT_CHANNEL == "stable"


def test_normalize_channel_coerces_garbage_to_stable():
    assert normalize_channel("beta") == "beta"
    assert normalize_channel("  EDGE ") == "edge"      # trim + lowercase
    assert normalize_channel("nonsense") == "stable"   # unknown -> default
    assert normalize_channel("") == "stable"
    assert normalize_channel(None) == "stable"


def test_default_checker_is_stable():
    c = UpdateChecker()
    assert c.channel == "stable"
    assert c.state.channel == "stable"
    assert c.state.as_dict()["channel"] == "stable"


def test_checker_normalizes_bad_channel_at_construction():
    c = UpdateChecker(channel="garbage")
    assert c.channel == "stable"


def test_channel_url_appends_query_param():
    c = UpdateChecker(channel="beta")
    url = c._channel_url("https://wattpost.cloud/api/releases/latest")
    assert url == "https://wattpost.cloud/api/releases/latest?channel=beta"
    # Respects an existing query string with & instead of ?
    url2 = c._channel_url("https://example/x?foo=1")
    assert url2 == "https://example/x?foo=1&channel=beta"


def test_set_channel_updates_state_and_clears_latest():
    c = UpdateChecker(channel="stable")
    # Pretend a prior stable poll found a newer version.
    c.state.latest_version = "9.9.9"
    c.state.release_url = "/docs/release-notes"
    returned = c.set_channel("beta")
    assert returned == "beta"
    assert c.channel == "beta"
    assert c.state.channel == "beta"
    # Cached "latest" is wiped so has_update can't report a stale
    # stable version against the freshly-selected beta channel.
    assert c.state.latest_version is None
    assert c.state.release_url is None
    assert c.state.has_update is False


def test_set_channel_normalizes():
    c = UpdateChecker()
    assert c.set_channel("EDGE") == "edge"
    assert c.set_channel("junk") == "stable"


def test_check_once_fetches_channel_specific_url(monkeypatch):
    """check_once should GET the ?channel= URL for the active channel."""
    c = UpdateChecker(channel="beta")
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"version": "0.2.0-rc1",
                                "released_at": "2026-06-02T00:00:00Z",
                                "release_url": "/docs/release-notes"}
        @property
        def text(self): return "# changelog"

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            seen.setdefault("urls", []).append(url)
            return _Resp()
        async def post(self, *a, **k): return _Resp()

    monkeypatch.setattr("solar_monitor.update.checker.httpx.AsyncClient", _Client)
    # No install beacon noise.
    c.telemetry_enabled = False
    asyncio.run(c.check_once())
    assert any("channel=beta" in u for u in seen["urls"])
    assert c.state.latest_version == "0.2.0-rc1"


# --- cloud-delivered channel (heartbeat apply, "cloud wins when set") ---

import types

from solar_monitor.cloud.service import CloudService


def _svc_with_updater(channel="stable"):
    upd = UpdateChecker(channel=channel)
    sched = types.SimpleNamespace(_updater=upd)
    # Bind only the attribute the method touches; call the method unbound
    # so we don't need the full CloudService constructor.
    svc = types.SimpleNamespace(scheduler=sched)
    return svc, upd


def test_cloud_sets_channel_and_reports_change():
    svc, upd = _svc_with_updater("stable")
    changed = CloudService._apply_cloud_update_channel(svc, "beta")
    assert changed is True
    assert upd.channel == "beta"
    # set_channel cleared the cached latest so has_update recomputes.
    assert upd.state.latest_version is None


def test_cloud_absent_channel_is_noop():
    """None = cloud hasn't overridden; the appliance's local channel stands."""
    svc, upd = _svc_with_updater("edge")
    assert CloudService._apply_cloud_update_channel(svc, None) is False
    assert upd.channel == "edge"


def test_cloud_same_channel_is_noop():
    svc, upd = _svc_with_updater("beta")
    assert CloudService._apply_cloud_update_channel(svc, "beta") is False


def test_cloud_garbage_channel_normalizes_to_stable():
    svc, upd = _svc_with_updater("stable")
    # "garbage" -> stable, which equals current -> no change reported.
    assert CloudService._apply_cloud_update_channel(svc, "garbage") is False
    assert upd.channel == "stable"


def test_cloud_missing_updater_is_safe():
    svc = types.SimpleNamespace(scheduler=types.SimpleNamespace(_updater=None))
    assert CloudService._apply_cloud_update_channel(svc, "beta") is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and "monkeypatch" not in fn.__code__.co_varnames:
            fn()
    print("ALL UPDATE-CHANNEL TESTS PASS (non-monkeypatch subset)")
