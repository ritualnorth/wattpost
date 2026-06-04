"""Cloud-broker KIOSK-scope allow-list (#225, Milestone D).

The brokered cloud kiosk (`<slug>.wattpost.cloud/kiosk`) is the appliance's
own SPA served through the tunnel with a `scope=kiosk` broker header. The
appliance enforces a read-only allow-list for that scope. These tests pin
the security-relevant decisions:

  * the kiosk MUST be able to GET `/api/kiosk/config` — otherwise a shared
    kiosk silently renders the default skin instead of the owner's chosen
    one (the Milestone D bug this guards against);
  * a kiosk visitor must NEVER mutate — PATCH /api/kiosk/config is denied,
    so a guest can't change the wall display's skin;
  * non-allow-listed paths stay denied.
"""
from solar_monitor.api.app import KIOSK_BROKER_GET_PATHS, kiosk_scope_allows


def test_kiosk_config_is_readable_under_kiosk_scope():
    # The whole point of Milestone D: a brokered kiosk reads its skin here.
    assert "/api/kiosk/config" in KIOSK_BROKER_GET_PATHS
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


# --- staff_read broker scope (#10, consent-gated remote access) ---
import base64 as _b64, hashlib as _hl, hmac as _hm, time as _t
from solar_monitor.web_auth import verify_broker_auth_verdict


def _sign(scope, secret_hex):
    ts = str(int(_t.time()))
    body = f"{ts}.{scope}"
    sig = _hm.new(bytes.fromhex(secret_hex), body.encode(), _hl.sha256).digest()
    return f"{ts}.{scope}." + _b64.urlsafe_b64encode(sig).decode().rstrip("=")


def test_staff_read_scope_verifies():
    sec = "ab" * 32
    verdict, _age, scope = verify_broker_auth_verdict(_sign("staff_read", sec), sec)
    assert verdict == "ok" and scope == "staff_read"


def test_known_broker_scopes_accepted():
    sec = "cd" * 32
    for s in ("user", "kiosk", "staff_read"):
        verdict, _a, scope = verify_broker_auth_verdict(_sign(s, sec), sec)
        assert verdict == "ok" and scope == s, s


def test_unknown_broker_scope_rejected():
    sec = "ef" * 32
    verdict, _a, _s = verify_broker_auth_verdict(_sign("staff_write", sec), sec)
    assert verdict == "bad-format", "an unknown scope must not verify"
