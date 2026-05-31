"""Captive-portal tests: probe-route responses + DNS drop-in management.

No real AP / nmcli needed — route handlers are called directly with a
stubbed app State, and the drop-in writer is pointed at a temp dir.
(Real captive behaviour — a phone joining the AP and its OS popping the
portal — needs on-device validation; this locks down the HTTP contract
and the file management that drive it.)
"""
import asyncio
import os
import tempfile
import types

from litestar.datastructures import State

from solar_monitor.api import captive as C
from solar_monitor.hotspot import service as S
from solar_monitor.hotspot.service import HotspotService
from solar_monitor.config import HotspotCfg


def _fn(handler):
    # @get(...) wraps the coroutine; the function is on .fn
    return getattr(handler, "fn", handler)


def _state(captive_active, has_svc=True):
    svc = types.SimpleNamespace(captive_active=captive_active) if has_svc else None
    return State({"scheduler": types.SimpleNamespace(hotspot=svc)})


def _body_str(resp):
    c = resp.content
    return c.decode() if isinstance(c, (bytes, bytearray)) else str(c)


def _location(resp):
    h = resp.headers
    try:
        return h.get("Location")
    except AttributeError:
        return dict(h).get("Location")


async def t_inactive_benign():
    st = _state(False)
    assert (await _fn(C.android_204)(state=st)).status_code == 204
    assert "Success" in _body_str(await _fn(C.apple_detect)(state=st))
    assert "Microsoft Connect Test" in _body_str(await _fn(C.win_connecttest)(state=st))
    assert "Microsoft NCSI" in _body_str(await _fn(C.win_ncsi)(state=st))
    print("PASS inactive: probes get the normal 'you're online' answers")


async def t_active_redirects():
    st = _state(True)
    want = f"http://{S.AP_GATEWAY}/"
    for h in (C.android_204, C.apple_detect, C.win_connecttest, C.win_ncsi, C.ubuntu_canonical):
        r = await _fn(h)(state=st)
        assert r.status_code == 302, (h, r.status_code)
        assert _location(r) == want, (h, _location(r))
    print(f"PASS active: every probe 302-redirects to the portal ({want})")


async def t_no_service_is_benign():
    st = _state(False, has_svc=False)
    assert (await _fn(C.android_204)(state=st)).status_code == 204
    print("PASS no-hotspot: probes stay benign when there's no hotspot service")


async def t_dropin_write_remove():
    with tempfile.TemporaryDirectory() as tmp:
        S._DNSMASQ_SHARED_DIR = tmp
        svc = HotspotService(HotspotCfg(captive_portal=True))
        svc._write_captive_dropin()
        path = os.path.join(tmp, S._CAPTIVE_DROPIN)
        assert svc.captive_active and os.path.exists(path)
        assert f"address=/#/{S.AP_GATEWAY}" in open(path).read()
        svc._remove_captive_dropin()
        assert not svc.captive_active and not os.path.exists(path)
    print("PASS drop-in: write arms catch-all DNS; remove disarms + deletes")


async def t_dropin_unwritable_is_graceful():
    S._DNSMASQ_SHARED_DIR = "/proc/nonexistent/cannot-write-here"
    svc = HotspotService(HotspotCfg(captive_portal=True))
    svc._write_captive_dropin()   # must not raise
    assert not svc.captive_active
    print("PASS drop-in unwritable: graceful no-op, captive stays inactive")


async def main():
    for t in (t_inactive_benign, t_active_redirects, t_no_service_is_benign,
              t_dropin_write_remove, t_dropin_unwritable_is_graceful):
        await t()
    print("\nALL CAPTIVE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
