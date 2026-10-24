"""`web.public_view: true` exposes dashboard reads only, never config/credentials/control."""

from solar_monitor.api.app import public_view_allows

from _appharness import make_client

SENSITIVE_GET = [
    "/api/system/backup", "/api/system/backup/file/x.tar.gz", "/api/system/backup/schedule",
    "/api/system/backup/cloud-list", "/api/system/diagnostics", "/api/system/logs",
    "/api/system/netsec", "/api/system/kiosk-tokens", "/api/system/integrations",
    "/api/system/info", "/api/system/update", "/api/system/update/log", "/api/system/slots",
    "/api/system/history_settings", "/api/system/discovery", "/api/system/local-telemetry",
    "/api/network/status", "/api/network/wifi/scan", "/api/setup/transports",
    "/api/setup/known_devices", "/api/setup/ble_scan", "/api/cloud/config",
    "/api/exporters/mqtt/config", "/api/exporters/prometheus/config",
    "/api/forecast/config", "/api/weather/config", "/api/gps/config", "/api/hotspot/status",
    "/api/location/status", "/api/mqtt_in/status", "/api/alerts",
    "/api/devices/bank/settings", "/api/devices/bank/settings/x",
    # evasion attempts
    "//api/system/backup", "/api/system//backup", "/api/system/backup/",
]
DASHBOARD_GET = [
    "/api/snapshot", "/api/devices", "/api/devices/bank/latest", "/api/devices/bank/history",
    "/api/today", "/api/energy/today", "/api/poll_run", "/api/stream", "/api/kiosk/config",
    "/api/forecast/pv", "/api/weather/current", "/api/system/device-health",
    "/api/system/host-history", "/api/health", "/api/branding", "/api/battery-health",
    "/api/runtime-forecast", "/api/load_heatmap", "/api/outputs",
]


def test_policy_function():
    for p in SENSITIVE_GET:
        assert not public_view_allows("GET", p), p
    for p in DASHBOARD_GET:
        assert public_view_allows("GET", p), p
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not public_view_allows(m, "/api/snapshot")


def test_anonymous_sensitive_reads_are_401_even_in_public_view(monkeypatch, tmp_path):
    c = make_client(monkeypatch, tmp_path, web={"public_view": True})
    for p in SENSITIVE_GET:
        r = c.get(p, follow_redirects=False)
        assert r.status_code in (401, 404, 405), (p, r.status_code)
        assert r.status_code != 200
    # the real attack target, spelled out
    assert c.get("/api/system/backup").status_code == 401
    assert c.get("/api/system/diagnostics").status_code == 401
    assert c.get("/api/system/logs").status_code == 401


def test_anonymous_dashboard_reads_still_pass_the_gate(monkeypatch, tmp_path):
    c = make_client(monkeypatch, tmp_path, web={"public_view": True})
    for p in ("/api/snapshot", "/api/devices", "/api/today", "/api/kiosk/config",
              "/api/energy/today", "/api/poll_run"):
        r = c.get(p)
        assert r.status_code != 401, p           # not stopped by auth (handler may 500: no store here)


def test_without_public_view_everything_is_401(monkeypatch, tmp_path):
    c = make_client(monkeypatch, tmp_path)
    assert c.get("/api/snapshot").status_code == 401
    assert c.get("/api/system/backup").status_code == 401


def test_session_still_reaches_sensitive_reads(monkeypatch, tmp_path):
    from solar_monitor import web_auth as wa
    c = make_client(monkeypatch, tmp_path, web={"public_view": True})
    c.cookies.set(wa.SESSION_COOKIE_NAME, wa.issue_session())
    assert c.get("/api/system/info").status_code == 200
