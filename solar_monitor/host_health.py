"""Lightweight host-health snapshot for #267 cloud device-health view.

stdlib-only so it can ship on the appliance without an extra dep.
Returns a single flat dict the heartbeat ships in extras; the cloud
renders it as a "Device health" card on /app/site/{id} and uses it
to surface warning chips (disk full, memory pressure) on the fleet
view.

Failure mode is silent: every reader is wrapped so a missing /proc
entry (e.g. inside a stripped container) returns the partial snapshot
rather than throwing. The cloud renders "—" for missing keys.
"""
from __future__ import annotations

import os
import shutil
import socket
import time
from typing import Any

# Daemon start time — same module-level pin as api/system.py so the
# uptime reported in the heartbeat matches what Settings → About shows.
# (Imported there too so an "import ordering" surprise doesn't reset it.)
_DAEMON_STARTED_AT = time.time()


def _uptime_seconds() -> float:
    return time.time() - _DAEMON_STARTED_AT


def _disk(path: str = "/") -> dict[str, Any]:
    try:
        u = shutil.disk_usage(path)
        return {
            "total_bytes": u.total,
            "used_bytes":  u.used,
            "free_bytes":  u.free,
            "percent":     round(u.used / u.total * 100, 1) if u.total else None,
        }
    except OSError:
        return {}


def _memory() -> dict[str, Any]:
    """MemTotal + MemAvailable from /proc/meminfo. MemAvailable is the
    right "free for new processes" number, not MemFree (which doesn't
    count reclaimable cache). Linux-only — returns {} on non-Linux."""
    try:
        kv: dict[str, int] = {}
        with open("/proc/meminfo", "r") as f:
            for ln in f:
                key, _, rest = ln.partition(":")
                parts = rest.strip().split()
                if not parts:
                    continue
                try:
                    val = int(parts[0])
                except ValueError:
                    continue
                # All meminfo values are in kB; convert to bytes once
                kv[key] = val * 1024
        total = kv.get("MemTotal")
        avail = kv.get("MemAvailable")
        if total is None:
            return {}
        used = (total - avail) if avail is not None else None
        return {
            "total_bytes":     total,
            "available_bytes": avail,
            "used_bytes":      used,
            "percent": (round(used / total * 100, 1)
                        if used is not None and total else None),
        }
    except OSError:
        return {}


def _loadavg() -> dict[str, float]:
    """1/5/15-min process queue length, normalized by CPU count so the
    cloud can render "X% of N cores" without re-counting."""
    try:
        one, five, fifteen = os.getloadavg()
        cpu = os.cpu_count() or 1
        return {
            "load_1m":  round(one, 2),
            "load_5m":  round(five, 2),
            "load_15m": round(fifteen, 2),
            "cpu_count": cpu,
            # Normalized: 1.0 means "fully utilising every core".
            "load_1m_per_core":  round(one / cpu, 3),
            "load_5m_per_core":  round(five / cpu, 3),
        }
    except OSError:
        return {}


def _hostname() -> str | None:
    try:
        return socket.gethostname() or None
    except OSError:
        return None


def _lan_ip() -> str | None:
    """Best-effort LAN IP via the connected-socket trick. Doesn't
    actually send a packet — the kernel just picks the source IP it
    would use to reach 8.8.8.8 (or any non-link-local). Returns None
    when no default route exists (rare for a paired appliance — it
    needs internet for the heartbeat anyway)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def snapshot() -> dict[str, Any]:
    """Single dict suitable for inclusion in heartbeat extras under
    the `host_health` key. Cheap (<5ms typical); safe to call once
    per heartbeat (~5min)."""
    return {
        "uptime_seconds": int(_uptime_seconds()),
        "hostname":       _hostname(),
        "lan_ip":         _lan_ip(),
        "disk":           _disk("/"),
        "memory":         _memory(),
        "loadavg":        _loadavg(),
    }
