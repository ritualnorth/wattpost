"""Selective restore (#26): _stage_and_swap honours a `components` set so a
user can restore just the data (history), just config, etc. — driven with a
synthetic backup tarball and temp targets, no real /etc or /var paths."""
import io
import tarfile
import tempfile
from pathlib import Path

from solar_monitor.api import backup as B


def _make_backup(db=b"NEWDB", config="db_path: restored.db\n",
                 pw_hash="NEWHASH", pw_plain="newplain") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, data: bytes):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("data.sqlite", db if isinstance(db, bytes) else db.encode())
        add("config/config.yaml", config.encode())
        add("config/web-password.hash", pw_hash.encode())
        add("config/web-password", pw_plain.encode())
    return buf.getvalue()


def _run(components, td: Path):
    """Stage+swap into temp targets, returning (db, cfg, pw_hash) paths."""
    db_t = td / "data.sqlite"
    cfg_t = td / "config.yaml"
    # Redirect the module's fixed web-password paths into the tempdir.
    B.WEB_PASSWORD_HASH_PATH = td / "web-password.hash"
    B.WEB_PASSWORD_PLAIN_PATH = td / "web-password"
    summary = B._stage_and_swap(_make_backup(), db_t, cfg_t, components=components)
    return db_t, cfg_t, B.WEB_PASSWORD_HASH_PATH, summary


def test_data_only():
    with tempfile.TemporaryDirectory() as d:
        db_t, cfg_t, pw_t, s = _run({"data"}, Path(d))
        assert db_t.read_bytes() == b"NEWDB"
        assert not cfg_t.exists(), "config must NOT be restored when data-only"
        assert not pw_t.exists(), "password must NOT be restored when data-only"
        assert s["restored_components"] == ["data"]
    print("PASS data-only: DB restored; config + password left alone")


def test_config_only():
    with tempfile.TemporaryDirectory() as d:
        db_t, cfg_t, pw_t, s = _run({"config"}, Path(d))
        assert not db_t.exists(), "history must NOT be touched when config-only"
        assert cfg_t.exists() and "restored" in cfg_t.read_text()
        assert not pw_t.exists()
    print("PASS config-only: config restored; history + password left alone")


def test_default_restores_everything():
    with tempfile.TemporaryDirectory() as d:
        db_t, cfg_t, pw_t, s = _run(None, Path(d))
        assert db_t.read_bytes() == b"NEWDB"
        assert cfg_t.exists()
        assert s["restored_components"] == ["config", "data", "password"]
        # Password is selected, but a *fresh* target declines the restored
        # hash (#297-2: don't trust a backup's password on a clean install —
        # the first-boot generator mints a new one instead).
        assert s.get("fresh_install_password_regen") is True
        assert not pw_t.exists()
    print("PASS default(None): data + config restored; password declined on fresh install")


def test_allowlist_covers_config_fields():
    # Regression guard for the bug this file surfaced: every top-level Config
    # field must be restorable, else a restore silently drops it (it dropped
    # `hotspot` + `update`). Add new Config fields to _RESTORE_ALLOWED_TOPLEVEL.
    import msgspec
    from solar_monitor.config import Config
    fields = {f.encode_name for f in msgspec.structs.fields(Config)}
    missing = fields - set(B._RESTORE_ALLOWED_TOPLEVEL)
    assert not missing, f"Config fields dropped on restore: {sorted(missing)}"
    print("PASS allow-list: every Config top-level field is restorable")


def test_all():
    test_data_only()
    test_config_only()
    test_default_restores_everything()
    test_allowlist_covers_config_fields()
    print("\nALL SELECTIVE-RESTORE SCENARIOS PASS")


if __name__ == "__main__":
    test_all()
