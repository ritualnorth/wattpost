"""In-memory log ring buffer.

Captures the daemon's recent log output so the Settings → Diagnostics
panel can show it without depending on systemd / journalctl / a log
file path. Plays nicely with both nohup-style dev startup and a real
systemd unit later.
"""
from __future__ import annotations

import logging
from collections import deque


class RingBufferHandler(logging.Handler):
    """Keep the last N log records in memory for the API to surface."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.buffer: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            })
        except Exception:
            # Never let log handling break the daemon.
            pass

    def lines(self) -> list[dict]:
        return list(self.buffer)


# Single process-wide instance. Installed by cli.cmd_serve and read by
# /api/system/logs.
LOG_RING = RingBufferHandler(capacity=500)


def install() -> None:
    """Attach the ring handler to the root logger. Idempotent."""
    root = logging.getLogger()
    if LOG_RING not in root.handlers:
        # Compact one-line format — Diagnostics renders it as monospace.
        LOG_RING.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(LOG_RING)
