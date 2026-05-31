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


def mon(cfg, mode_getter=None):
    svc = FakeService(cfg)
    m = AutoHandoffMonitor(svc, mode_getter=mode_getter)
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


async def t_cloud_mode_convenience():
    mode = {"v": "van"}
    async def getter(): return mode["v"]
    m, svc = mon(HotspotCfg(auto_handoff=False), mode_getter=getter)
    svc.lan = None
    await m.tick(); r = await m.tick()
    assert r == "raise" and svc._active, r
    # Cloud flips to home → no longer effective → our AP is cleaned up.
    mode["v"] = "home"
    r = await m.tick()
    assert r == "off" and not svc._active, r
    print("PASS cloud-mode: van implies handoff; home cleans up the raised AP")


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


async def main():
    for t in (t_not_opted_in, t_local_flag_raises_after_grace, t_eth_return_drops,
              t_single_radio_probe_drop, t_cloud_mode_convenience, t_enabled_is_skipped,
              t_manual_ap_untouched, t_lan_present_noop):
        await t()
    print("\nALL HANDOFF SCENARIOS PASS")

asyncio.run(main())
