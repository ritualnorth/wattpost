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


# ---------------------------------------------------------------------
# Broker-auth verify ring. Records every request that arrives bearing
# an X-WP-Broker-Auth header so we can see, after the fact, which
# requests reached the appliance through the broker and how the HMAC
# verdict came out. Critical for diagnosing white-page / 404 incidents
# on `<slug>.wattpost.cloud` without resorting to ssh-into-Caddy log
# grepping. Capacity 200 ≈ 30-60 min of broker traffic at typical
# rates; bounded to keep memory flat on the appliance.

BROKER_AUTH_RING: deque[dict] = deque(maxlen=200)


def record_broker_auth(
    *,
    path: str,
    verdict: str,
    header_age_s: float | None = None,
    cf_ray: str | None = None,
    method: str = "GET",
    header_prefix: str | None = None,
) -> None:
    """Append a broker-auth verify result. Verdicts:
      ok          — HMAC matched, timestamp fresh
      no-secret   — appliance has no sso_secret (pre-pair / drift)
      bad-format  — header malformed (no `.` separator, bad b64, etc)
      expired     — timestamp outside the ±30 s freshness window
      bad-mac     — signature didn't match the expected HMAC

    `header_age_s` is `now - header_ts` (seconds) when computable. For
    `expired` it tells you which way the skew leans; for `ok` it
    surfaces clock drift before it becomes a verify failure.

    `header_prefix` is the first ~80 chars of the raw header bytes,
    captured ONLY on non-ok verdicts. Useful for diagnosing the exact
    shape an attacker (or a buggy cloud) sent — the ts + scope + sig
    pattern is well-formed enough that 80 chars covers it. Stays
    local: the ring is exposed via /api/diagnostics/broker-auth
    behind auth, never leaves the appliance.
    """
    import time as _t
    try:
        BROKER_AUTH_RING.append({
            "ts": _t.time(),
            "path": path,
            "method": method,
            "verdict": verdict,
            "header_age_s": header_age_s,
            "cf_ray": cf_ray,
            "header_prefix": header_prefix,
        })
    except Exception:
        # Never let diagnostics break the request path.
        pass


def recent_broker_auth() -> list[dict]:
    return list(BROKER_AUTH_RING)
