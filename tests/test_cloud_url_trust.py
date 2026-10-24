"""URLs from the cloud reply (JWKS / OIDC) are only trusted on the paired cloud's host over https."""
import asyncio
import json

import httpx
import pytest

from solar_monitor import urlcheck
from solar_monitor.auth import oidc_config, oidc_rp

from _appharness import make_client

EP = "https://wattpost.cloud"


def _urls(host="wattpost.cloud", scheme="https"):
    base = f"{scheme}://{host}"
    return dict(client_id="apl_1_lan", redirect_uri=f"{base}/auth/callback",
                jwks_url=f"{base}/oidc/jwks",
                discovery_url=f"{base}/.well-known/openid-configuration")


@pytest.fixture
def oidc_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(oidc_config, "_KEY_DIR", tmp_path)
    monkeypatch.setattr(oidc_config, "OIDC_CONFIG_PATH", tmp_path / "oidc_client.json")
    return tmp_path


def test_same_cloud_host_rules():
    ok = urlcheck.same_cloud_host
    assert ok("https://wattpost.cloud/oidc/jwks", EP)
    assert ok("https://WattPost.Cloud:8443/x", EP)                     # port/case don't matter
    assert not ok("https://evil.example/oidc/jwks", EP)
    assert not ok("https://wattpost.cloud.evil.example/x", EP)
    assert not ok("https://wattpost.cloud@evil.example/x", EP)
    assert not ok("https://user:pw@wattpost.cloud/x", EP)
    assert not ok("http://wattpost.cloud/oidc/jwks", "http://wattpost.cloud")   # cleartext, not loopback
    assert not ok("ftp://wattpost.cloud/x", EP)
    assert not ok("//wattpost.cloud/x", EP)
    assert not ok("", EP)
    # development: http is fine on loopback, but only on the SAME host as the endpoint
    assert ok("http://127.0.0.1:8000/jwks", "http://127.0.0.1:8000")
    assert ok("http://localhost:8000/jwks", "http://localhost:9000")
    assert ok("http://[::1]:8000/jwks", "http://[::1]:8000")
    assert not ok("http://localhost:8000/jwks", "http://127.0.0.1:8000")
    assert not ok("https://wattpost.cloud/jwks", "http://127.0.0.1:8000")   # dev mismatch is refused


def test_endpoint_problem():
    p = urlcheck.endpoint_problem
    assert p("https://wattpost.cloud") is None
    assert p("https://wattpost.io/") is None
    assert p("http://127.0.0.1:8000") is None
    assert p("http://localhost") is None
    assert p("http://wattpost.cloud")
    assert p("http://192.168.1.5:8000")
    assert p("ftp://x") and p("wattpost.cloud") and p("https://")


def test_valid_cloud_reply_is_saved(oidc_dir):
    got = oidc_config.save_from_cloud(endpoint=EP, **_urls())
    assert got is not None and oidc_config.load().jwks_url == "https://wattpost.cloud/oidc/jwks"


def test_hostile_reply_keeps_previous_config(oidc_dir):
    oidc_config.save_from_cloud(endpoint=EP, **_urls())
    before = oidc_config.load()
    for evil in (
        _urls("evil.example"),
        _urls("wattpost.cloud", "http"),
        {**_urls(), "jwks_url": "https://evil.example/jwks"},
        {**_urls(), "discovery_url": "https://evil.example/.well-known/openid-configuration"},
        {**_urls(), "redirect_uri": "https://evil.example/auth/callback"},
        {**_urls(), "jwks_url": "javascript:alert(1)"},
        {**_urls(), "jwks_url": None},
    ):
        assert oidc_config.save_from_cloud(endpoint=EP, **evil) is None
        assert oidc_config.load() == before


def test_hostile_first_reply_saves_nothing(oidc_dir):
    assert oidc_config.save_from_cloud(endpoint=EP, **_urls("evil.example")) is None
    assert oidc_config.load() is None


def test_dev_cloud_on_other_host_is_refused_but_saved_config_still_works(oidc_dir):
    oidc_config.save(**_urls("wattpost.cloud"))              # already-saved config
    saved = oidc_config.load()
    # dev: endpoint is loopback, the cloud returns a jwks_url on another host
    assert oidc_config.save_from_cloud(endpoint="http://127.0.0.1:8000",
                                       **_urls("dev.wattpost.test")) is None
    assert oidc_config.load() == saved                       # untouched and still loadable
    # and a loopback dev cloud that stays on loopback works
    assert oidc_config.save_from_cloud(endpoint="http://127.0.0.1:8000",
                                       **_urls("127.0.0.1:8000", "http")) is not None


# ---- JWKS redirects ------------------------------------------------------------

def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_jwks_redirect_to_another_host_is_refused():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "wattpost.cloud":
            return httpx.Response(302, headers={"location": "https://evil.example/jwks"})
        return httpx.Response(200, json={"keys": [{"kid": "evil"}]})

    async def go():
        async with _client(handler) as c:
            with pytest.raises(RuntimeError):
                await oidc_rp._get_same_host(c, "https://wattpost.cloud/oidc/jwks")
    asyncio.run(go())
    assert seen == ["https://wattpost.cloud/oidc/jwks"]        # evil.example never contacted


def test_jwks_same_host_redirect_and_downgrade():
    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(308, headers={"location": "/new"})
        if req.url.path == "/down":
            return httpx.Response(302, headers={"location": "http://wattpost.cloud/new"})
        return httpx.Response(200, json={"keys": []})

    async def go():
        async with _client(handler) as c:
            r = await oidc_rp._get_same_host(c, "https://wattpost.cloud/old")
            assert r.status_code == 200
            with pytest.raises(RuntimeError):
                await oidc_rp._get_same_host(c, "https://wattpost.cloud/down")
    asyncio.run(go())


def test_fetch_jwks_does_not_follow_a_cross_host_redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(oidc_rp, "_jwks_cache", None)
    monkeypatch.setattr(oidc_rp, "JWKS_CACHE_PATH", tmp_path / "jwks.json")
    monkeypatch.setattr(oidc_rp, "_KEY_DIR", tmp_path)
    hits = []

    def handler(req):
        hits.append(req.url.host)
        if req.url.host == "wattpost.cloud":
            return httpx.Response(302, headers={"location": "https://evil.example/jwks"})
        return httpx.Response(200, json={"keys": [{"kid": "evil"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(oidc_rp.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    keys = asyncio.run(oidc_rp.fetch_jwks("https://wattpost.cloud/oidc/jwks", force=True))
    assert keys == [] and hits == ["wattpost.cloud"]


# ---- endpoint validation in the admin API ---------------------------------------

def _authed(monkeypatch, tmp_path):
    from solar_monitor import web_auth as wa
    c = make_client(monkeypatch, tmp_path)
    c.cookies.set(wa.SESSION_COOKIE_NAME, wa.issue_session())
    return c


def test_cleartext_cloud_endpoint_is_rejected_by_put_and_pair(monkeypatch, tmp_path):
    c = _authed(monkeypatch, tmp_path)
    for ep in ("http://wattpost.cloud", "http://192.168.1.9:8000", "gopher://x", "wattpost.cloud"):
        r = c.put("/api/cloud/config", json={"endpoint": ep, "heartbeat_minutes": 5})
        assert r.status_code == 400, (ep, r.text)
        r = c.post("/api/cloud/pair", json={"code": "ABC123", "endpoint": ep})
        assert r.status_code == 400, (ep, r.text)
