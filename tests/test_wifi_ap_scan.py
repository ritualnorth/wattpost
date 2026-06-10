"""AP-mode WiFi scan: drop the hotspot → scan → restore it (#3).

A single radio can't beacon an AP and scan at once, so a fresh scan in
hotspot mode means briefly tearing the AP down. The box is usually only
reachable *through* that AP, so the one thing that must never break is the
restore — these tests lock down that the AP comes back up no matter what
the scan does, and that we don't touch the AP when it wasn't up to begin
with. (The real radio bounce + a phone rejoining is on-device territory.)
"""
import asyncio
import json

import solar_monitor.helper_client as HC
from solar_monitor.api import hotspot_admin as H


class FakeHotspot:
    def __init__(self, active=True):
        self._active = active
        self.calls = []

    async def status(self):
        return {"active": self._active}

    async def deactivate(self):
        self.calls.append("deactivate")
        self._active = False

    async def activate(self):
        self.calls.append("activate")
        self._active = True


class FakeScheduler:
    def __init__(self, hotspot):
        self.hotspot = hotspot


def _state(hotspot):
    return {"scheduler": FakeScheduler(hotspot)}


def _reset():
    H._AP_SCAN.update({"state": "idle", "ts": 0.0, "networks": [], "error": None})


def test_bounce_restores_ap_and_caches(monkeypatch):
    _reset()
    hs = FakeHotspot(active=True)
    nets = [{"ssid": "Home", "signal": 80, "secure": True, "in_use": False}]
    monkeypatch.setattr(HC, "call", lambda action, **k: {"ok": True, "out": json.dumps(nets), "err": ""})
    asyncio.run(H._ap_bounce_scan(_state(hs)))
    assert hs.calls == ["deactivate", "activate"]   # dropped, then restored
    assert H._AP_SCAN["state"] == "done"
    assert H._AP_SCAN["networks"] == nets


def test_bounce_restores_ap_even_if_scan_explodes(monkeypatch):
    _reset()
    hs = FakeHotspot(active=True)

    def boom(*a, **k):
        raise RuntimeError("helper socket gone")

    monkeypatch.setattr(HC, "call", boom)
    asyncio.run(H._ap_bounce_scan(_state(hs)))
    # The AP MUST be back up even though the scan blew up mid-bounce.
    assert "activate" in hs.calls
    assert H._AP_SCAN["state"] == "error"
    assert H._AP_SCAN["error"]


def test_bounce_leaves_ap_alone_when_inactive(monkeypatch):
    _reset()
    hs = FakeHotspot(active=False)
    monkeypatch.setattr(HC, "call", lambda action, **k: {"ok": True, "out": "[]", "err": ""})
    asyncio.run(H._ap_bounce_scan(_state(hs)))
    assert hs.calls == []                            # never toggled the AP
    assert H._AP_SCAN["state"] == "done"


def test_scan_failure_surfaces_error_and_still_restores(monkeypatch):
    _reset()
    hs = FakeHotspot(active=True)
    monkeypatch.setattr(HC, "call", lambda action, **k: {"ok": False, "out": "", "err": "radio busy"})
    asyncio.run(H._ap_bounce_scan(_state(hs)))
    assert hs.calls == ["deactivate", "activate"]
    assert H._AP_SCAN["state"] == "error"
    assert "radio busy" in H._AP_SCAN["error"]


def test_get_scan_short_circuits_when_ap_active(monkeypatch):
    hs = FakeHotspot(active=True)
    monkeypatch.setattr(HC, "is_available", lambda: True)
    called = []
    monkeypatch.setattr(HC, "call", lambda *a, **k: (called.append(1), {"ok": True, "out": "[]", "err": ""})[1])
    fn = getattr(H.wifi_scan, "fn", H.wifi_scan)
    r = asyncio.run(fn(_state(hs)))
    assert r["hotspot_active"] is True
    assert r["networks"] == []
    assert called == []   # did not run the doomed live scan while AP up
