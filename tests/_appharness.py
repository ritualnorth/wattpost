"""Build the real litestar app (middleware included) for HTTP-level tests.

No lifespan is run (no scheduler, no pollers); only routing + middleware +
handlers that don't need the store are exercised. Auth state files are
redirected into tmp_path so nothing touches /etc/wattpost.
"""
import shutil
from pathlib import Path

from litestar.testing import TestClient

from solar_monitor import web_auth as wa
from solar_monitor.api.app import build_app
from solar_monitor.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def isolate_auth(monkeypatch, tmp_path):
    d = tmp_path / "etc"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(wa, "_PASSWORD_DIR", d)
    monkeypatch.setattr(wa, "PASSWORD_HASH_PATH", d / "web-password.hash")
    monkeypatch.setattr(wa, "PASSWORD_PLAINTEXT_PATH", d / "web-password")
    monkeypatch.setattr(wa, "FIRST_RUN_MARKER_PATH", d / "web-password.autogen")
    monkeypatch.setattr(wa, "SESSIONS_PATH", d / "sessions.json")
    monkeypatch.setattr(wa, "_SESSIONS", {})
    monkeypatch.setattr(wa, "_LOGIN_FAILS", {})
    monkeypatch.delenv("WATTPOST_DEMO", raising=False)
    return d


def make_client(monkeypatch, tmp_path, *, web=None, password="correct-horse-1"):
    """web: optional dict of web.* overrides (public_view, trust_loopback...)."""
    isolate_auth(monkeypatch, tmp_path)
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg_path)
    cfg = load_config(str(cfg_path))
    if web:
        import msgspec
        base = cfg.web if getattr(cfg, "web", None) else None
        if base is None:
            from solar_monitor.config import WebCfg
            base = WebCfg()
        cfg.web = msgspec.structs.replace(base, **web)
    if password is not None:
        wa.write_password_hash(password)
    app = build_app(cfg, str(tmp_path / "db.sqlite"), config_path=str(cfg_path))
    # on_startup (not run here) normally fills these in
    app.state["config"] = cfg
    app.state["config_path"] = str(cfg_path)
    return TestClient(app)
