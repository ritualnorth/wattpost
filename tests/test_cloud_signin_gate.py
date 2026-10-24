"""The login page's "Sign in with WattPost cloud" button is off unless `web.cloud_signin` is set.

The flow returns the browser to <slug>.wattpost.cloud, which stopped resolving
with the tunnel, so it cannot complete; the appliance must not offer it.
"""
from _appharness import make_client

from solar_monitor.auth import oidc_config


def _save_oidc(monkeypatch, tmp_path, redirect="https://slug.wattpost.cloud/auth/callback"):
    monkeypatch.setattr(oidc_config, "_KEY_DIR", tmp_path)
    monkeypatch.setattr(oidc_config, "OIDC_CONFIG_PATH", tmp_path / "oidc_client.json")
    oidc_config.save(client_id="apl_1_lan", redirect_uri=redirect,
                     jwks_url="https://wattpost.cloud/oidc/jwks",
                     discovery_url="https://wattpost.cloud/.well-known/openid-configuration")


def _available(c):
    r = c.get("/api/system/oidc-available")
    assert r.status_code == 200, r.text
    return r.json()["available"]


def test_off_by_default_even_with_a_saved_oidc_config(monkeypatch, tmp_path):
    _save_oidc(monkeypatch, tmp_path)
    with make_client(monkeypatch, tmp_path) as c:
        assert _available(c) is False
        assert c.get("/auth/lan/login", follow_redirects=False).status_code == 404


def test_on_only_with_the_option_and_a_callback(monkeypatch, tmp_path):
    _save_oidc(monkeypatch, tmp_path)
    with make_client(monkeypatch, tmp_path, web={"cloud_signin": True}) as c:
        assert _available(c) is True
        assert c.get("/auth/lan/login", follow_redirects=False).status_code == 302


def test_blank_callback_means_unavailable_even_when_enabled(monkeypatch, tmp_path):
    _save_oidc(monkeypatch, tmp_path, redirect="")
    with make_client(monkeypatch, tmp_path, web={"cloud_signin": True}) as c:
        assert _available(c) is False
        assert c.get("/auth/lan/login", follow_redirects=False).status_code == 404
