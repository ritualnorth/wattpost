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
