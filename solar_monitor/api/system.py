"""System endpoints — disk usage, uptime, Tailscale state.

Kept out of api/app.py so the route factory stays readable. The
restart + logs endpoints already in app.py would naturally live here
too — leaving them in place to avoid a churn move; this file's the
right home for any future system-level handlers.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import logging
import platform
import shutil
import sys
import time
from typing import Any

from litestar import get, post
from litestar.datastructures import State
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


# ---------- self-update check ----------

@get("/api/system/update")
async def update_state(state: State) -> dict[str, Any]:
    """Current vs latest version of WattPost, from the daily manifest
    poll. UI uses this to surface "Update available" on Settings →
    About."""
    scheduler = state["scheduler"]
    updater = getattr(scheduler, "_updater", None)
    if updater is None:
        from .. import __version__ as v
        return {
            "current_version": v,
            "latest_version":  None,
            "has_update":      False,
            "last_checked_at": None,
            "last_error":      "update checker not running",
        }
    return updater.state.as_dict()


@post("/api/system/update/check", status_code=202)
async def update_check_now(state: State) -> dict[str, Any]:
    """Force a one-off manifest fetch — Settings UI's "Check now"
    button. Independent of the 24h background loop."""
    scheduler = state["scheduler"]
    updater = getattr(scheduler, "_updater", None)
    if updater is None:
        raise HTTPException(status_code=500, detail="update checker not running")
    await updater.check_once()
    return updater.state.as_dict()


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


async def _tailscale_serve_status() -> dict[str, Any]:
    """Check whether `tailscale serve` is already serving our app on
    443. `tailscale serve status --json` returns the current config."""
    rc, out, _err = await _run("tailscale", "serve", "status", "--json", timeout=5.0)
    if rc != 0 or not out.strip():
        return {"https": False}
    try:
        cfg = json.loads(out)
    except Exception:
        return {"https": False}
    # The config shape: { "TCP": { "443": {...} }, "Web": { "...": { "Handlers": ... } } }
    https_active = bool((cfg.get("TCP") or {}).get("443"))
    return {"https": https_active, "raw": cfg if https_active else None}


@get("/api/system/tailscale/status")
async def tailscale_status() -> dict[str, Any]:
    """High-level state the UI needs for the Network block:
       installed / running / logged_in / ip / hostname / magicdnssuffix +
       whether the Serve HTTPS endpoint is exposing this dashboard.
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
        return {"installed": True, "running": False, "logged_in": False}
    backend = snap.get("BackendState", "Stopped")
    self_node = snap.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    dns_name = (self_node.get("DNSName") or "").rstrip(".")
    serve = await _tailscale_serve_status() if backend == "Running" else {"https": False}
    return {
        "installed":  True,
        "running":    backend in ("Running", "Starting"),
        "logged_in":  backend == "Running",
        "backend":    backend,
        "ipv4":       next((i for i in ips if ":" not in i), None),
        "hostname":   self_node.get("HostName"),
        "dns_name":   dns_name or None,
        "magicdns":   snap.get("MagicDNSSuffix"),
        "auth_url":   snap.get("AuthURL"),
        "https":      serve["https"],
        "https_url":  f"https://{dns_name}/" if (serve["https"] and dns_name) else None,
    }


def _ts_priv(*args: str) -> tuple[str, ...]:
    """Prefix a tailscale subcommand with `sudo -n` so the daemon (which
    runs as the wattpost system user under systemd) can invoke up /
    logout / serve. Install script drops a sudoers entry granting these
    three specifically. `-n` means non-interactive — if the rule isn't
    present we fail fast instead of hanging on a password prompt.
    In a dev shell the user is usually already root, so `sudo -n` is a
    cheap no-op."""
    return ("sudo", "-n", "tailscale", *args)


def _is_password_required(err: str) -> bool:
    """Detect the two ways sudo -n refuses non-interactively: 'a password
    is required' (no matching NOPASSWD rule) and 'no tty present' (rare
    legacy variant)."""
    e = (err or "").lower()
    return "password is required" in e or "no tty" in e


def _sudo_hint() -> str:
    """Customer-vs-dev-aware error message for sudo failures on
    tailscale up / logout / serve. The production install.sh creates a
    `wattpost` system user and drops a NOPASSWD sudoers entry for it;
    a dev running the daemon as their own login user won't have that
    entry and gets a different fix."""
    user = getpass.getuser()
    if user == "wattpost":
        return ("Tailscale needs root, and the daemon's sudoers entry is "
                "missing or has been removed. Re-run packaging/install.sh "
                "to reinstall it.")
    return (f"Tailscale needs root. The daemon is running as `{user}`, "
            f"not the production `wattpost` user, so install.sh's sudoers "
            f"entry doesn't apply. Run `sudo bash packaging/dev-sudoers.sh` "
            f"to grant `{user}` the same passwordless access.")


async def _tailscale_serve_https() -> tuple[bool, str | None]:
    """Once we're authenticated, expose the dashboard at
    https://<hostname>.<tailnet>.ts.net/ via Tailscale Serve. This is
    Tailscale's free Let's Encrypt cert path — no manual cert
    management, no warning bypass. Idempotent: `tailscale serve` with
    the same args is a no-op if already serving.

    Returns `(True, None)` on success and `(False, hint)` otherwise so
    the caller decides whether to escalate (the explicit Enable-HTTPS
    button raises 500; the post-up best-effort call just logs)."""
    rc, _out, err = await _run(
        *_ts_priv("serve", "--bg", "--https=443", "http://127.0.0.1:8000"),
        timeout=15.0,
    )
    if rc == 0:
        return True, None
    err_msg = (err or "").strip()
    if _is_password_required(err_msg):
        return False, _sudo_hint()
    return False, f"tailscale serve failed: {err_msg}"


@post("/api/system/tailscale/up", status_code=202)
async def tailscale_up() -> dict[str, Any]:
    """Bring the tailnet up. Returns the auth URL the user must visit
    to log in (or {ok:true, already_authed: true} if we were already
    authed). `tailscale up` blocks until login completes so we kick it
    off in the background and poll status for the AuthURL.
    On successful login we also fire `tailscale serve` to expose the
    dashboard over HTTPS without a self-signed-cert warning."""
    if not _tailscale_installed():
        raise HTTPException(status_code=400, detail="tailscale not installed")

    # Run `tailscale up` in the background — it blocks until the user
    # finishes auth in their browser, but we need to keep polling for
    # the AuthURL it emits early. Keep a handle so we can detect an
    # immediate failure (most importantly: sudo refused non-interactively).
    up_task = asyncio.create_task(_run(
        *_ts_priv(
            "up",
            "--reset",
            "--accept-routes",
            "--accept-dns=false",
            "--ssh=false",
            "--hostname=wattpost",
        ),
        timeout=60.0,
    ))

    for _ in range(12):
        await asyncio.sleep(0.5)

        # If `tailscale up` already exited with a non-zero rc, something
        # went wrong before auth started — almost always sudo. Surface
        # it before the poll-loop times out with a generic hint.
        if up_task.done():
            try:
                rc, _out, err = up_task.result()
            except Exception as e:
                raise HTTPException(status_code=500,
                                    detail=f"tailscale up crashed: {e}")
            if rc != 0:
                err_msg = (err or "").strip()
                if _is_password_required(err_msg):
                    raise HTTPException(status_code=500, detail=_sudo_hint())
                raise HTTPException(status_code=500,
                                    detail=f"tailscale up failed: {err_msg}")

        snap = await _tailscale_status_json()
        if not snap:
            continue
        if snap.get("BackendState") == "Running":
            self_node = snap.get("Self") or {}
            ips = self_node.get("TailscaleIPs") or []
            # Best-effort HTTPS serve; not fatal if it fails — the
            # user can hit Enable HTTPS manually and get a proper
            # error message there if sudoers is wrong.
            async def _best_effort_serve() -> None:
                ok, hint = await _tailscale_serve_https()
                if not ok:
                    log.info("tailscale serve not (re)started: %s", hint)
            asyncio.create_task(_best_effort_serve())
            return {"ok": True, "already_authed": True,
                    "ipv4": next((i for i in ips if ":" not in i), None)}
        url = snap.get("AuthURL")
        if url:
            return {"ok": True, "auth_url": url}
    return {"ok": True, "auth_url": None,
            "hint": "Run `sudo tailscale up` from SSH if the auth URL doesn't appear here."}


@post("/api/system/tailscale/down", status_code=200)
async def tailscale_down() -> dict[str, Any]:
    if not _tailscale_installed():
        raise HTTPException(status_code=400, detail="tailscale not installed")
    rc, _out, err = await _run(*_ts_priv("logout"))
    if rc != 0:
        msg = (err or "").strip()
        if _is_password_required(msg):
            raise HTTPException(status_code=500, detail=_sudo_hint())
        raise HTTPException(status_code=500, detail=f"logout failed: {msg}")
    return {"ok": True}


@post("/api/system/tailscale/serve", status_code=202)
async def tailscale_serve() -> dict[str, Any]:
    """Manual trigger for the HTTPS Serve config — useful if the
    user's tailnet was already up when the daemon started (we only
    auto-serve right after a fresh login). Unlike the post-up
    best-effort call, this one raises 500 on failure so the Enable
    HTTPS button shows a real error."""
    if not _tailscale_installed():
        raise HTTPException(status_code=400, detail="tailscale not installed")
    ok, hint = await _tailscale_serve_https()
    if not ok:
        raise HTTPException(status_code=500, detail=hint or "tailscale serve failed")
    return {"ok": True}
