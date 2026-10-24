"""The anonymous first-run "set a password" endpoint closes once the box is claimed."""
from solar_monitor import web_auth as wa

from _appharness import make_client

PW = "generated-pw-123"


def _boot(monkeypatch, tmp_path):
    c = make_client(monkeypatch, tmp_path, password=PW)
    wa.FIRST_RUN_MARKER_PATH.write_text("auto-generated\n")
    return c


def test_first_run_works_while_untouched(monkeypatch, tmp_path):
    c = _boot(monkeypatch, tmp_path)
    r = c.post("/api/setup/first-run-password", json={"password": "my-own-password"})
    assert r.status_code == 200
    assert not wa.FIRST_RUN_MARKER_PATH.exists()
    assert wa.verify_password("my-own-password")


def test_login_with_generated_password_closes_first_run(monkeypatch, tmp_path):
    c = _boot(monkeypatch, tmp_path)
    assert c.post("/api/login", json={"password": PW}).status_code == 200
    assert not wa.FIRST_RUN_MARKER_PATH.exists()
    r = c.post("/api/setup/first-run-password", json={"password": "attacker-chosen-1"})
    assert r.status_code == 409
    assert wa.verify_password(PW)
    assert not wa.verify_password("attacker-chosen-1")


def test_existing_local_session_closes_stale_marker(monkeypatch, tmp_path):
    """Upgrade case: owner logged in under an older build that left the marker."""
    c = _boot(monkeypatch, tmp_path)
    wa.issue_session()
    r = c.post("/api/setup/first-run-password", json={"password": "attacker-chosen-1"})
    assert r.status_code == 409
    assert not wa.FIRST_RUN_MARKER_PATH.exists()


def test_kiosk_session_does_not_close_first_run(monkeypatch, tmp_path):
    _boot(monkeypatch, tmp_path)
    wa.issue_session(origin="kiosk")
    assert wa.is_first_run()


def test_first_run_endpoint_is_throttled(monkeypatch, tmp_path):
    c = _boot(monkeypatch, tmp_path)
    wa.FIRST_RUN_MARKER_PATH.unlink()
    codes = [c.post("/api/setup/first-run-password",
                    json={"password": "attacker-chosen-1"}).status_code
             for _ in range(wa._LOGIN_FREE_ATTEMPTS + 2)]
    assert codes[0] == 409 and codes[-1] == 429
