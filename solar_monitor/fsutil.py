"""Small filesystem helpers shared by the config / secret writers."""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, mode: int = 0o600,
                      encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically (tmp + fsync + rename) with `mode`
    set when the tmp file is CREATED, never widened afterwards. A plain
    `Path.write_text` + `replace` leaves the file at the umask default
    (typically 0644): wrong for config.yaml (bearer token, SMTP/MQTT creds)
    and sessions.json (live session tokens)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()            # a stale tmp could carry an old, wider mode
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)     # O_CREAT is subject to the umask; make it exact
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
