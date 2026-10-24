"""Loopback trust is only for genuinely local requests."""
from solar_monitor import web_auth as wa


def _scope(host, headers=()):
    return {"client": (host, 1234), "headers": list(headers)}


def test_plain_loopback_trusted():
    assert wa.is_loopback_source(_scope("127.0.0.1"))
    assert wa.is_loopback_source(_scope("::1"))


def test_lan_client_never_loopback():
    assert not wa.is_loopback_source(_scope("192.168.1.20"))


def test_loopback_relayed_by_proxy_not_trusted():
    for h in (b"x-forwarded-for", b"x-real-ip", b"forwarded", b"cf-ray",
              b"cf-connecting-ip", b"x-forwarded-host"):
        assert not wa.is_loopback_source(_scope("127.0.0.1", [(h, b"1.2.3.4")])), h


def test_missing_client_not_loopback():
    assert not wa.is_loopback_source({"headers": []})


# ---- same-host reverse proxies ---------------------------------------------------

import pytest

PROXY_MARKERS = [
    b"via", b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto",
    b"x-forwarded-port", b"x-forwarded-server", b"x-forwarded-prefix", b"x-forwarded-anything",
    b"x-real-ip", b"true-client-ip", b"x-original-forwarded-for", b"x-client-ip",
    b"fastly-client-ip", b"x-cluster-client-ip", b"cf-connecting-ip", b"cf-ray",
    b"cf-ipcountry", b"cf-visitor", b"cf-anything", b"CF-Connecting-IP", b"X-Real-IP",
]


@pytest.mark.parametrize("header", PROXY_MARKERS)
def test_every_proxy_marker_header_means_not_local(header):
    assert not wa.is_loopback_source(_scope("127.0.0.1", [(header, b"1.2.3.4")]))
    assert not wa.is_loopback_source(_scope("::1", [(header, b"1.2.3.4")]))


def test_foreign_origin_or_referer_means_not_local():
    for name in (b"origin", b"referer", b"Origin"):
        for val in (b"https://evil.example", b"https://evil.example/path?x=1",
                    b"http://192.168.1.20:8080", b"null", b"http://127.0.0.1.evil.example/",
                    b"http://localhost@evil.example/"):
            assert not wa.is_loopback_source(_scope("127.0.0.1", [(name, val)])), (name, val)


def test_local_browser_origin_or_referer_is_still_local():
    for name in (b"origin", b"referer"):
        for val in (b"http://localhost:8080", b"http://127.0.0.1:8080/kiosk",
                    b"http://[::1]:8080/", b"http://localhost/"):
            assert wa.is_loopback_source(_scope("127.0.0.1", [(name, val)])), (name, val)
    # unrelated headers don't matter
    assert wa.is_loopback_source(_scope("127.0.0.1", [(b"user-agent", b"curl"), (b"host", b"x")]))


def test_trust_loopback_switch(monkeypatch):
    assert wa.TRUST_LOOPBACK is True                           # default
    monkeypatch.setattr(wa, "TRUST_LOOPBACK", False)
    assert not wa.is_loopback_source(_scope("127.0.0.1"))
    assert not wa.is_loopback_source(_scope("::1"))


def test_config_option_defaults_true_and_reaches_the_middleware(monkeypatch, tmp_path):
    from solar_monitor.config import WebCfg
    from _appharness import make_client
    assert WebCfg().trust_loopback is True
    monkeypatch.setattr(wa, "TRUST_LOOPBACK", True)
    make_client(monkeypatch, tmp_path, web={"trust_loopback": False})
    assert wa.TRUST_LOOPBACK is False
    make_client(monkeypatch, tmp_path, web={"trust_loopback": True})
    assert wa.TRUST_LOOPBACK is True
    monkeypatch.setattr(wa, "TRUST_LOOPBACK", True)


def test_middleware_loopback_peer_with_and_without_switch(monkeypatch, tmp_path):
    """End to end through the real middleware with a loopback TCP peer."""
    from _appharness import make_client
    app = make_client(monkeypatch, tmp_path).app

    async def _call(headers, trust):
        monkeypatch.setattr(wa, "TRUST_LOOPBACK", trust)
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(m):
            sent.append(m)
        scope = {"type": "http", "method": "GET", "path": "/api/system/backup",
                 "raw_path": b"/api/system/backup", "query_string": b"", "root_path": "",
                 "scheme": "http", "http_version": "1.1", "server": ("127.0.0.1", 8080),
                 "client": ("127.0.0.1", 5000), "headers": headers}
        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")
    import asyncio
    assert asyncio.run(_call([], True)) != 401                      # loopback owner
    assert asyncio.run(_call([(b"x-real-ip", b"8.8.8.8")], True)) == 401
    assert asyncio.run(_call([(b"origin", b"https://evil.example")], True)) == 401
    assert asyncio.run(_call([], False)) == 401                     # switch off: no special case
    monkeypatch.setattr(wa, "TRUST_LOOPBACK", True)
