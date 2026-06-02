"""Backup / restore round-trip + restore-time hardening.

Exercises the real archive path used by both the manual download and
the scheduled BackupService:
  * build_archive_bytes() produces a valid tar.gz with the DB + config,
  * _verify_archive() accepts it and rejects junk,
  * the SQLite snapshot inside round-trips intact (a known row survives
    backup -> extract),
  * the restore-time config sanitiser (#297) drops non-allowlisted
    top-level keys and redacts credential-looking fields.

The actual file-swap + daemon re-exec is system-level and out of scope
for a unit test; everything that determines whether a restore is *safe*
and *lossless* is covered here.
"""
import io
import sqlite3
import tarfile

import pytest

from solar_monitor.api.backup import (
    build_archive_bytes,
    _verify_archive,
    _sanitize_restored_config,
)


def _make_db(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE marker (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO marker VALUES ('canary', 'survived-42')")
    con.commit()
    con.close()


def test_backup_archive_roundtrips_db_and_config(tmp_path):
    db = tmp_path / "solar.db"
    _make_db(db)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("transports: []\ndevices: []\nexporters: []\n")

    blob = build_archive_bytes(db, cfg)
    assert isinstance(blob, (bytes, bytearray)) and len(blob) > 0

    # Pre-flight verify accepts our own archive.
    _verify_archive(blob)  # raises on anything unsafe

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = set(tar.getnames())
        assert {"data.sqlite", "config/config.yaml", "MANIFEST"} <= names
        out = tmp_path / "extracted"
        out.mkdir()
        tar.extract("data.sqlite", path=out)

    con = sqlite3.connect(str(out / "data.sqlite"))
    row = con.execute("SELECT v FROM marker WHERE k='canary'").fetchone()
    con.close()
    assert row is not None and row[0] == "survived-42"


def test_verify_rejects_non_backup():
    with pytest.raises(Exception):
        _verify_archive(b"this is not a tar.gz")


def test_restore_sanitiser_drops_unknown_keys_and_redacts_secrets():
    dropped: list = []
    redacted: list = []
    raw = {
        "transports": [],
        "devices": [],
        # A compromised cloud account can't smuggle a new top-level key
        # past the allowlist.
        "mqtt_out": {"host": "attacker.example", "topic": "exfil"},
        # Credentials inside an allowlisted block are zeroed so the
        # operator must re-enter them.
        "cloud": {"endpoint": "https://wattpost.cloud", "bearer_token": "supersecret"},
    }
    out = _sanitize_restored_config(raw, dropped=dropped, redacted=redacted)

    assert "mqtt_out" not in out and "mqtt_out" in dropped
    assert "transports" in out and "devices" in out
    assert out["cloud"]["bearer_token"] == ""          # redacted
    assert out["cloud"]["endpoint"] == "https://wattpost.cloud"  # kept
    assert any("bearer_token" in r for r in redacted)


if __name__ == "__main__":
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    test_backup_archive_roundtrips_db_and_config(d)
    test_verify_rejects_non_backup()
    test_restore_sanitiser_drops_unknown_keys_and_redacts_secrets()
    print("ALL BACKUP/RESTORE TESTS PASS")
