"""Tests for the in-place hotspot-config apply path + the AP-raise
radio-ready wait. These cover behaviour added when the config PUT stopped
rebuilding the whole scheduler (v0.1.173/174):

  * HotspotService._wait_for_iface_ready — the bounded wait that stops the
    AP bring-up racing wlan0's unavailable→ready transition after
    `nmcli radio wifi on`.
  * hotspot_admin._bg_apply — the background reconcile a config PUT runs
    instead of a full hot-reload: it must restart the auto-handoff monitor
    (so a runtime auto_handoff/enabled toggle takes effect — the v0.1.173
    regression) and (re-)activate the AP only when enabled.

No real nmcli / radio / scheduler: collaborators are stubbed.
"""
import asyncio

from solar_monitor.config import HotspotCfg
from solar_monitor.hotspot.service import HotspotService
from solar_monitor.api.hotspot_admin import _bg_apply


# --- _wait_for_iface_ready -------------------------------------------------

async def t_wait_returns_once_iface_leaves_unavailable():
    svc = HotspotService(HotspotCfg(interface="wlan0"))
    # wlan0 reports 'unavailable' for the first two polls, then ready.
    seq = iter(["wlan0:unavailable", "wlan0:unavailable", "wlan0:disconnected"])
    calls = {"n": 0}

    async def fake_nmcli(*args):
        calls["n"] += 1
        try:
            return (0, next(seq), "")
        except StopIteration:
            return (0, "wlan0:disconnected", "")

    svc._nmcli = fake_nmcli
    ready = await svc._wait_for_iface_ready("wlan0", timeout=5.0, poll=0.0)
    assert ready is True
    assert calls["n"] >= 3, calls
    print("PASS wait_for_iface_ready: returns once wlan0 leaves 'unavailable'")


async def t_wait_times_out_if_never_ready():
    svc = HotspotService(HotspotCfg(interface="wlan0"))

    async def fake_nmcli(*args):
        return (0, "wlan0:unavailable", "")

    svc._nmcli = fake_nmcli
    ready = await svc._wait_for_iface_ready("wlan0", timeout=0.2, poll=0.05)
    assert ready is False
    print("PASS wait_for_iface_ready: times out (False) when iface never readies")


async def t_wait_ignores_other_interfaces():
    # A ready eth0 must not be mistaken for the requested wlan0.
    svc = HotspotService(HotspotCfg(interface="wlan0"))
    seq = iter([
        "eth0:connected\nwlan0:unavailable",
        "eth0:connected\nwlan0:disconnected",
    ])

    async def fake_nmcli(*args):
        try:
            return (0, next(seq), "")
        except StopIteration:
            return (0, "eth0:connected\nwlan0:disconnected", "")

    svc._nmcli = fake_nmcli
    ready = await svc._wait_for_iface_ready("wlan0", timeout=5.0, poll=0.0)
    assert ready is True
    print("PASS wait_for_iface_ready: keys on the requested iface, not eth0")


# --- _bg_apply (config-PUT reconcile) --------------------------------------

class FakeMonitor:
    def __init__(self):
        self.events = []

    async def stop(self):
        self.events.append("stop")

    async def start(self):
        self.events.append("start")


class FakeSvc:
    def __init__(self, cfg):
        self.cfg = cfg
        self.activated = 0

    @staticmethod
    def is_available(cfg):
        return True

    async def activate(self):
        self.activated += 1
        return {"ok": True, "error": ""}


class FakeScheduler:
    def __init__(self, monitor):
        self.hotspot_handoff = monitor


async def t_bg_apply_enabled_restarts_monitor_and_activates():
    mon, svc = FakeMonitor(), FakeSvc(HotspotCfg(enabled=True))
    await _bg_apply(FakeScheduler(mon), svc, svc.cfg)
    # The regression guard: the monitor is ALWAYS restarted so should_run()
    # is re-evaluated against the new cfg.
    assert mon.events == ["stop", "start"], mon.events
    assert svc.activated == 1
    print("PASS bg_apply: enabled → monitor restarted + AP (re)activated")


async def t_bg_apply_disabled_restarts_monitor_no_activate():
    mon, svc = FakeMonitor(), FakeSvc(HotspotCfg(enabled=False))
    await _bg_apply(FakeScheduler(mon), svc, svc.cfg)
    assert mon.events == ["stop", "start"], mon.events
    # enabled=false leaves a running AP alone — no activate() call.
    assert svc.activated == 0
    print("PASS bg_apply: disabled → monitor restarted, AP left alone")


async def t_bg_apply_survives_missing_monitor():
    # A scheduler without a handoff monitor (e.g. nmcli absent) must not raise.
    class NoMonScheduler:
        hotspot_handoff = None

    svc = FakeSvc(HotspotCfg(enabled=True))
    await _bg_apply(NoMonScheduler(), svc, svc.cfg)
    assert svc.activated == 1
    print("PASS bg_apply: no monitor present → still activates, no crash")


async def main():
    for t in (
        t_wait_returns_once_iface_leaves_unavailable,
        t_wait_times_out_if_never_ready,
        t_wait_ignores_other_interfaces,
        t_bg_apply_enabled_restarts_monitor_and_activates,
        t_bg_apply_disabled_restarts_monitor_no_activate,
        t_bg_apply_survives_missing_monitor,
    ):
        await t()
    print("\nALL HOTSPOT CONFIG-APPLY TESTS PASS")


def test_hotspot_config_apply():
    """pytest entry point — runs every scenario (each asserts inline)."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
