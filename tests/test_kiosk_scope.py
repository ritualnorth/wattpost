"""Kiosk-credential allow-list (LAN kiosk tokens / kiosk sessions).

A kiosk credential (token or kiosk-origin session) may only reach a
read-only allow-list on the appliance. These tests pin the
security-relevant decisions:

  * the kiosk MUST be able to GET `/api/kiosk/config`, otherwise a wall
    display silently renders the default skin instead of the owner's
    chosen one;
  * a kiosk visitor must NEVER mutate, PATCH /api/kiosk/config is denied,
    so a guest can't change the wall display's skin;
  * non-allow-listed paths stay denied.
"""
from solar_monitor.api.app import KIOSK_GET_PATHS, kiosk_scope_allows


def test_kiosk_config_is_readable_under_kiosk_scope():
    # A kiosk reads its skin here.
    assert "/api/kiosk/config" in KIOSK_GET_PATHS
    assert kiosk_scope_allows("GET", "/api/kiosk/config") is True
    assert kiosk_scope_allows("HEAD", "/api/kiosk/config") is True


def test_kiosk_scope_is_read_only():
    # A kiosk guest cannot change the skin (or anything else).
    assert kiosk_scope_allows("PATCH", "/api/kiosk/config") is False
    assert kiosk_scope_allows("POST", "/api/kiosk/config") is False
    assert kiosk_scope_allows("DELETE", "/api/snapshot") is False
    assert kiosk_scope_allows("PUT", "/kiosk") is False


def test_kiosk_render_paths_allowed():
    # The data the kiosk SPA needs to render must all be reachable.
    for path in ("/kiosk", "/kiosk/van", "/api/snapshot", "/api/devices",
                 "/api/today", "/api/energy/today", "/api/weather",
                 "/web/static/app.js"):
        assert kiosk_scope_allows("GET", path) is True, path


def test_non_allowlisted_paths_denied():
    # Anything outside the allow-list is denied even for GET — a kiosk
    # guest must not reach owner-only endpoints.
    for path in ("/api/config", "/api/cloud/config", "/api/backup",
                 "/api/system/update", "/", "/api/kiosk"):
        assert kiosk_scope_allows("GET", path) is False, path
