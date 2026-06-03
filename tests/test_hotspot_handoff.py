"""Scenario tests for AutoHandoffMonitor.tick — the auto-handoff policy.

Drives tick() with a stubbed service (controllable lan_kind / active
state) so the full state machine is exercised with no real nmcli / radio.
"""
import asyncio
from solar_monitor.config import HotspotCfg
from solar_monitor.hotspot import handoff as H
from solar_monitor.hotspot.handoff import AutoHandoffMonitor
from solar_monitor.hotspot.service import HotspotService

# Pretend nmcli is present so the policy actually runs on this host.
HotspotService.is_available = staticmethod(lambda cfg: True)
# Keep the first-boot "networked" marker off the real state dir during tests.
from pathlib import Path as _Path
H.NETWORKED_MARKER = _Path("/tmp/wp-test-networked-marker")
try:
    H.NETWORKED_MARKER.unlink()
except FileNotFoundError:
    pass


class FakeService:
    def __init__(self, cfg):
        self.cfg = cfg
        self._active = False
        self.lan = None
        self.calls = []

    async def _is_active(self):
        return self._active

    async def lan_kind(self):
        return self.lan

    async def activate(self):
        self.calls.append("activate"); self._active = True
        return {"ok": True, "error": ""}

    async def deactivate(self):
        self.calls.append("deactivate"); self._active = False
        return {"ok": True, "error": ""}


def mon(cfg, networked=True):
    # Default: pretend the box has already been on a network, so onboarding is
    # off and these scenarios isolate the auto_handoff policy. Onboarding
    # tests pass networked=False to exercise the fresh-box path.
    svc = FakeService(cfg)
    m = AutoHandoffMonitor(svc)
    m._networked = networked
    return m, svc


async def t_not_opted_in():
    m, svc = mon(HotspotCfg(auto_handoff=False))  # no cloud either
    svc.lan = None
    for _ in range(5):
        assert await m.tick() == "off"
    assert svc.calls == []
    print("PASS not-opted-in: never raises")


async def t_local_flag_raises_after_grace():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc.lan = None
    r1 = await m.tick(); r2 = await m.tick()
    assert (r1, r2) == ("wait", "raise"), (r1, r2)
    assert svc.calls == ["activate"] and svc._active
    print(f"PASS local-flag: offline raises after GRACE_CHECKS={H.GRACE_CHECKS}")


async def t_eth_return_drops():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc.lan = None
    await m.tick(); await m.tick()          # raise
    assert svc._active
    svc.lan = "eth"
    r = await m.tick()
    assert r == "drop:eth" and not svc._active and svc.calls[-1] == "deactivate"
    print("PASS eth-return: AP dropped immediately when ethernet appears")


async def t_single_radio_probe_drop():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc.lan = None
    await m.tick(); await m.tick()          # raise
    holds = 0
    for _ in range(H.RETRY_AFTER_POLLS):
        r = await m.tick()
        if r == "hold":
            holds += 1
        elif r == "probe-drop":
            break
    assert r == "probe-drop" and not svc._active, r
    assert holds == H.RETRY_AFTER_POLLS - 1
    print(f"PASS single-radio: probe-drop after RETRY_AFTER_POLLS={H.RETRY_AFTER_POLLS} holds")


async def t_flag_off_cleans_up():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc.lan = None
    await m.tick(); r = await m.tick()
    assert r == "raise" and svc._active, r
    # Operator turns auto-handoff off → the AP we raised is cleaned up.
    svc.cfg.auto_handoff = False
    r = await m.tick()
    assert r == "off" and not svc._active, r
    print("PASS flag-off: turning auto_handoff off drops the monitor-raised AP")


async def t_enabled_is_skipped():
    m, svc = mon(HotspotCfg(auto_handoff=True, enabled=True))
    svc.lan = None
    assert await m.tick() == "skip:enabled"
    assert svc.calls == []
    print("PASS enabled: always-on AP is not managed by handoff")


async def t_manual_ap_untouched():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc._active = True            # human raised it; monitor didn't
    svc.lan = "eth"
    r = await m.tick()
    assert r == "skip:manual" and svc._active and "deactivate" not in svc.calls
    print("PASS manual: a human-raised AP is never dropped by handoff")


async def t_lan_present_noop():
    m, svc = mon(HotspotCfg(auto_handoff=True))
    svc.lan = "wifi"
    for _ in range(3):
        assert await m.tick() == "ok"
    assert svc.calls == []
    print("PASS lan-present: connected → AP never raised")


async def t_onboarding_raises_fresh_box():
    # A never-networked box (onboarding on) raises the setup AP even with
    # auto_handoff off — the headless van/off-grid first-boot path.
    m, svc = mon(HotspotCfg(auto_handoff=False, onboarding=True), networked=False)
    svc.lan = None
    r1 = await m.tick(); r2 = await m.tick()
    assert (r1, r2) == ("wait", "raise"), (r1, r2)
    assert svc._active and svc.calls == ["activate"]
    print("PASS onboarding: fresh box raises setup AP even with auto_handoff off")


async def t_onboarding_no_probe_drop_blip():
    # While onboarding, the single-radio probe-drop is skipped — a fresh box
    # has no known WiFi to find, so the setup AP must stay rock-stable.
    m, svc = mon(HotspotCfg(auto_handoff=False, onboarding=True), networked=False)
    svc.lan = None
    await m.tick(); await m.tick()          # raise
    for _ in range(H.RETRY_AFTER_POLLS + 2):
        assert await m.tick() == "hold:onboarding"
    assert svc._active and "deactivate" not in svc.calls
    print("PASS onboarding: setup AP stays stable (no probe-drop blip)")


async def t_onboarding_latches_off_after_lan():
    # Once a LAN is seen, onboarding completes; going offline later does NOT
    # re-raise an AP (that path is auto_handoff, opt-in) — no surprise AP.
    m, svc = mon(HotspotCfg(auto_handoff=False, onboarding=True), networked=False)
    svc.lan = "eth"
    assert await m.tick() == "ok"
    assert m._networked
    svc.lan = None
    for _ in range(5):
        assert await m.tick() == "off"
    assert svc.calls == []
    print("PASS onboarding: latches off after first LAN; no surprise AP later")


async def main():
    for t in (t_not_opted_in, t_local_flag_raises_after_grace, t_eth_return_drops,
              t_single_radio_probe_drop, t_flag_off_cleans_up, t_enabled_is_skipped,
              t_manual_ap_untouched, t_lan_present_noop,
              t_onboarding_raises_fresh_box, t_onboarding_no_probe_drop_blip,
              t_onboarding_latches_off_after_lan):
        await t()
    print("\nALL HANDOFF SCENARIOS PASS")


def test_auto_handoff_scenarios():
    """pytest entry point — runs every scenario (each asserts inline)."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
