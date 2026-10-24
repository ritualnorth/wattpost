"""Files holding secrets are written 0600, atomically, whatever the umask."""
import os
import stat

import yaml

from solar_monitor import fsutil, web_auth as wa
from solar_monitor.api import cloud_admin

from _appharness import isolate_auth, ROOT


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_atomic_write_text_is_0600_even_over_a_0644_file_and_umask_000(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("old")
    os.chmod(p, 0o644)
    old = os.umask(0)
    try:
        fsutil.atomic_write_text(p, "new")
    finally:
        os.umask(old)
    assert p.read_text() == "new" and mode(p) == 0o600
    assert not (tmp_path / "f.yaml.tmp").exists()


def test_atomic_write_failure_leaves_original_and_no_tmp(tmp_path, monkeypatch):
    p = tmp_path / "f"
    p.write_text("keep")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    try:
        fsutil.atomic_write_text(p, "new")
    except OSError:
        pass
    monkeypatch.undo()
    assert p.read_text() == "keep" and not (tmp_path / "f.tmp").exists()


def test_config_save_helper_leaves_0600(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("cloud: {bearer_token: secret}\n")
    os.chmod(p, 0o644)
    cloud_admin._save_config(str(p), lambda raw: {**raw, "x": 1})
    assert mode(p) == 0o600
    assert yaml.safe_load(p.read_text())["x"] == 1


def test_sessions_json_is_0600(monkeypatch, tmp_path):
    isolate_auth(monkeypatch, tmp_path)
    old = os.umask(0o022)
    try:
        wa.issue_session()
    finally:
        os.umask(old)
    assert wa.SESSIONS_PATH.is_file() and mode(wa.SESSIONS_PATH) == 0o600


def test_load_config_rewrite_is_atomic_and_0600(tmp_path):
    from solar_monitor.config import load_config
    p = tmp_path / "config.yaml"
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    raw["cloud"] = {"endpoint": "https://wattpost.cloud", "bearer_token": "tok",
                    "tunnel_token": "legacy"}
    p.write_text(yaml.safe_dump(raw))
    os.chmod(p, 0o644)
    load_config(p)
    assert "tunnel_token" not in yaml.safe_load(p.read_text())["cloud"]
    assert mode(p) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
