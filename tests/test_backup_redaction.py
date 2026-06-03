"""Security guarantee: a built backup tarball must NOT carry the user's
plaintext secrets. Third-party creds (SMTP/MQTT/Solcast/hotspot) and the
plaintext dashboard password are redacted/omitted on the way out; only the
appliance's own cloud pairing tokens are kept (cloud-issued, revocable, and
needed to recover identity + history on a fresh-Pi restore)."""
import io
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from solar_monitor.api import backup as B

_CONFIG = """\
label: x
exporters:
  - type: mqtt
    host: broker.local
    password: SUPERSECRET
weather:
  api_key: SOLCASTKEY
hotspot:
  ssid: WattPost-Setup
  password: WIFIPASS
cloud:
  endpoint: https://wattpost.cloud
  bearer_token: PAIRINGTOKEN
"""


def test_backup_redacts_secrets():
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        db = dp / "data.db"
        c = sqlite3.connect(db); c.execute("create table t(x)"); c.commit(); c.close()
        cfg = dp / "config.yaml"
        cfg.write_text(_CONFIG)
        # Plaintext + hashed password files "present" on the box.
        B.WEB_PASSWORD_PLAIN_PATH = dp / "web-password"
        B.WEB_PASSWORD_PLAIN_PATH.write_text("PLAINPW")
        B.WEB_PASSWORD_HASH_PATH = dp / "web-password.hash"
        B.WEB_PASSWORD_HASH_PATH.write_text("$argon2id$fakehash")

        blob = B.build_archive_bytes(db, cfg)
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            names = tar.getnames()
            cfg_txt = tar.extractfile("config/config.yaml").read().decode()

        # Plaintext password file must be gone; hash is fine.
        assert "config/web-password" not in names, "plaintext password leaked into backup!"
        assert "config/web-password.hash" in names

        # Third-party secrets redacted in the config + absent from the whole blob.
        for secret in (b"SUPERSECRET", b"SOLCASTKEY", b"WIFIPASS", b"PLAINPW"):
            assert secret not in blob, f"{secret!r} leaked into the backup tarball!"

        # Cloud pairing token kept (recovery continuity).
        assert "PAIRINGTOKEN" in cfg_txt, "cloud pairing block should be preserved"
        print("PASS backup redaction: 3rd-party secrets + plaintext password stripped; cloud token kept")


if __name__ == "__main__":
    test_backup_redacts_secrets()
    print("\nBACKUP REDACTION OK")
