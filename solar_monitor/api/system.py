"""System endpoints — disk usage, uptime, Tailscale state.

Kept out of api/app.py so the route factory stays readable. The
restart + logs endpoints already in app.py would naturally live here
too — leaving them in place to avoid a churn move; this file's the
right home for any future system-level handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
import sys
import time
from typing import Any

from litestar import get, post
from litestar.exceptions import HTTPException

log = logging.getLogger(__name__)


# ---------- disk / uptime ----------

def _disk_usage(path: str = "/") -> dict[str, Any]:
    """Total / used / free bytes for the partition holding `path`.
    Defaults to root which is where /opt/wattpost, /etc/wattpost, and
    /var/lib/wattpost all live in the systemd-installed layout."""
    u = shutil.disk_usage(path)
    return {
        "path":     path,
        "total":    u.total,
        "used":     u.used,
        "free":     u.free,
        "percent":  round(u.used / u.total * 100, 1) if u.total else None,
    }


def _proc_uptime_seconds() -> float | None:
    """Read /proc/uptime — works on Linux, returns None elsewhere."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


@get("/api/system/info")
async def system_info() -> dict[str, Any]:
    """One-shot system status payload for Settings → About."""
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "platform": platform.platform(terse=True),
        "uptime_seconds": _proc_uptime_seconds(),
        "disk": _disk_usage("/"),
        # Database lives on its own logical path; surface it separately
        # when the bind to /var/lib/wattpost is on a different volume
        # (e.g. an external USB SSD on a Pi).
        "disk_state": _disk_usage("/var/lib/wattpost")
                      if _disk_usage_exists("/var/lib/wattpost") else None,
    }


def _disk_usage_exists(path: str) -> bool:
    try:
        shutil.disk_usage(path)
        return True
    except Exception:
        return False


# ---------- Tailscale ----------

async def _run(*cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """asyncio subprocess helper. Returns (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    except FileNotFoundError:
        return 127, "", "command not found"
    except asyncio.TimeoutError:
        return -1, "", f"timed out after {timeout}s"


def _tailscale_installed() -> bool:
    return shutil.which("tailscale") is not None


async def _tailscale_status_json() -> dict[str, Any] | None:
    rc, out, _err = await _run("tailscale", "status", "--json", "--peers=false")
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


@get("/api/system/tailscale/status")
async def tailscale_status() -> dict[str, Any]:
    """High-level state the UI needs for the Network block:
       installed / running / logged_in / ip / hostname / magicdnssuffix.
       Falls back to non-running fields when tailscaled isn't installed
       or the user hasn't logged in yet."""
    if not _tailscale_installed():
        return {
            "installed": False,
            "running": False,
            "logged_in": False,
            "install_hint": "curl -fsSL https://tailscale.com/install.sh | sh",
        }
    snap = await _tailscale_status_json()
    if snap is None:
        # tailscaled may not be running yet, or status returned non-JSON
        return {"installed": True, "running": False, "logged_in": False}
    backend = snap.get("BackendState", "Stopped")
    self_node = snap.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    return {
        "installed":  True,
        "running":    backend in ("Running", "Starting"),
        "logged_in":  backend == "Running",
        "backend":    backend,
        "ipv4":       next((i for i in ips if ":" not in i), None),
        "hostname":   self_node.get("HostName"),
        "dns_name":   self_node.get("DNSName"),  # e.g. wattpost.tail-scale.ts.net
        "magicdns":   snap.get("MagicDNSSuffix"),
        "auth_url":   snap.get("AuthURL"),
    }


@post("/api/system/tailscale/up", status_code=202)
async def tailscale_up() -> dict[str, Any]:
    """Bring the tailnet up. Returns the auth URL the user must visit
    to log in (or {ok:true} if we were already authed). `tailscale up`
    blocks until login completes so we kick it off in the background
    and poll status for the AuthURL."""
    if not _tailscale_installed():
        raise HTTPException(status_code=400, detail="tailscale not installed")

    # Fire `tailscale up` as a non-blocking background process. We pass
    # --reset so a previous half-configured state doesn't stick.
    asyncio.create_task(_run(
        "tailscale", "up",
        "--reset",
        "--accept-routes",
        "--accept-dns=false",     # leave the user's DNS alone
        "--ssh=false",            # don't expose the appliance as an SSH host
        "--hostname=wattpost",
        timeout=60.0,
    ))

    # Poll status for up to ~6 s waiting for an AuthURL.
    for _ in range(12):
        await asyncio.sleep(0.5)
        snap = await _tailscale_status_json()
        if not snap:
            continue
        if snap.get("BackendState") == "Running":
            self_node = snap.get("Self") or {}
            ips = self_node.get("TailscaleIPs") or []
            return {"ok": True, "already_authed": True,
                    "ipv4": next((i for i in ips if ":" not in i), None)}
        url = snap.get("AuthURL")
        if url:
            return {"ok": True, "auth_url": url}
    return {"ok": True, "auth_url": None, "hint": "Run `tailscale up` from SSH if the auth URL doesn't appear here."}


@post("/api/system/tailscale/down", status_code=200)
async def tailscale_down() -> dict[str, Any]:
    if not _tailscale_installed():
        raise HTTPException(status_code=400, detail="tailscale not installed")
    rc, _out, err = await _run("tailscale", "logout")
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"logout failed: {err.strip()}")
    return {"ok": True}
