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
import os
import platform
import shutil
import sys
import time
import time
from typing import Any

from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response

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


_DAEMON_STARTED_AT = time.time()


def _proc_uptime_seconds() -> float | None:
    """Return how long the WattPost daemon has been running.

    Used to be a /proc/uptime read — which on bare metal gave the
    box's uptime (fine) but in a Docker container with host /proc
    leakage gave the host's uptime (e.g. '3d 23h' on a laptop
    that's just had the container restarted ten minutes ago). The
    daemon process start time is what users actually want to see.
    """
    return time.time() - _DAEMON_STARTED_AT


@get("/api/system/kiosk")
async def kiosk_status(state: State) -> dict[str, Any]:
    """Returns the current kiosk share state for the Settings panel.

    `share_url` is the public URL the user copy-pastes to wherever
    they want to display the kiosk view. `null` when no cloud tunnel
    is provisioned (the appliance needs a slug for the URL to point
    anywhere — pair to enable).
    """
    config = state["config"]
    if config.cloud is None:
        return {"share_url": None, "enabled": False}
    hostname = config.cloud.tunnel_hostname or ""
    slug = hostname.split(".", 1)[0] if hostname else ""
    tok  = config.cloud.kiosk_token or ""
    share_url = None
    if slug and tok:
        share_url = f"https://{slug}.wattpost.cloud/kiosk?key={tok}"
    return {
        "share_url": share_url,
        "enabled":   bool(slug and tok),
    }


@post("/api/system/kiosk/rotate", status_code=200)
async def rotate_kiosk_token(state: State) -> dict[str, Any]:
    """Generate a new kiosk_token, persist to config, return the new
    public share URL. Old token instantly stops working — any
    already-shared URLs break, which IS the intent ("I leaked the
    URL, kill it").

    Auth-gated by the normal session middleware (Settings tab
    requires sign-in per v0.0.58)."""
    import secrets as _secrets
    from .. import config as _config_mod
    config = state["config"]
    config_path = state.get("config_path", "config.yaml")
    if config.cloud is None:
        raise HTTPException(status_code=400,
                            detail="cloud not configured — pair first")
    new_tok = _secrets.token_urlsafe(24)
    config.cloud.kiosk_token = new_tok
    # Persist via the same _save_config helper that cloud_admin uses;
    # mutator preserves every other field.
    from .cloud_admin import _serialize_cloud, _save_config
    def _mutate(raw):
        raw["cloud"] = _serialize_cloud(config.cloud)
        return raw
    _save_config(config_path, _mutate)
    log.info("kiosk token rotated")
    # Construct the public URL the user should now share. Assumes the
    # cloud broker subdomain `<slug>.wattpost.cloud` — the appliance
    # doesn't directly know its slug; pass the tunnel_hostname's
    # slug-half if available.
    share_url = None
    hostname = config.cloud.tunnel_hostname or ""
    slug = hostname.split(".", 1)[0] if hostname else ""
    if slug:
        share_url = f"https://{slug}.wattpost.cloud/kiosk?key={new_tok}"
    return {"ok": True, "kiosk_token": new_tok, "share_url": share_url}


@get("/api/system/auth-status")
async def auth_status(request: Request, state: State) -> dict[str, Any]:
    """Read-only signal of whether the current request is authed,
    and by what mechanism. Three positive cases:

      1. Local session cookie — set by /api/login after a password
         sign-in. origin="local".
      2. SSO session cookie — set by /sso after a cloud-minted token
         (e.g. dashboard "Open" button → broker-redirect-with-token).
         origin="sso".
      3. Broker HMAC header — every request via the cloud broker
         (<slug>.wattpost.cloud) carries X-WP-Broker-Auth signed by
         the per-appliance sso_secret. Stateless, per-request.
         origin="broker".

    The SPA uses this to gate Settings/Setup (skip the bounce-to-
    /login redirect when the request is already broker-authed) and
    to decide whether to show a Sign Out button.

    Required for cloud broker UX: without case 3, broker-authed
    users would be bounced to /login by the SPA gate, hit a dead
    end (login-tunnel.html says "sign in via wattpost.cloud"), and
    be stuck.
    """
    from .. import web_auth as _wa
    # Broker first: cheap header check, no DB roundtrip.
    broker_header = request.headers.get("x-wp-broker-auth")
    if broker_header:
        cfg = state.get("config") if hasattr(state, "get") else state["config"]
        sso = (cfg.cloud.sso_secret if (cfg and cfg.cloud) else "") or ""
        if sso and _wa.verify_broker_auth(broker_header, sso):
            return {"authed": True, "origin": "broker"}
    # Cookie-based session (local password OR cloud SSO redirect).
    token = request.cookies.get(_wa.SESSION_COOKIE_NAME)
    if not token:
        return {"authed": False, "origin": None}
    sess = _wa._session_record(token)
    if sess is None:
        return {"authed": False, "origin": None}
    return {
        "authed": True,
        "origin": sess.get("origin", "local"),
    }


_SECRET_KEYS = {
    "bearer_token", "tunnel_token", "sso_secret",
    "api_key", "secret", "password", "smtp_password",
    "vapid_private_key",
}


def _redact(obj: Any, depth: int = 0) -> Any:
    """Walk a nested dict/list and replace any value whose KEY looks
    sensitive with `"<redacted>"`. Used to scrub the config blob
    before it's bundled into a diagnostics download.

    Conservative: false-positives (over-redacting) are fine here;
    false-negatives (leaking a token in a support ticket) are not."""
    if depth > 20:
        return obj  # recursion guard against pathological configs
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k in _SECRET_KEYS else _redact(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x, depth + 1) for x in obj]
    return obj


@get("/api/system/diagnostics")
async def diagnostics_bundle(state: State) -> Response:
    """Single-shot diagnostics bundle for support tickets. Returns a
    JSON document combining version + platform + redacted config +
    recent log lines + a transport/device summary, with a
    Content-Disposition that prompts a download.

    All secrets are scrubbed via `_redact`. The user can attach the
    resulting file to a support email without revealing tokens.

    Works identically on Pi and Docker — no journalctl / docker-logs
    dependency. The in-memory LOG_RING (solar_monitor.diagnostics)
    keeps the last ~500 lines across both deployment shapes.
    """
    from datetime import datetime, timezone
    from .. import __version__, diagnostics as _diag
    settings = state["settings"] if "settings" in state else None
    config = state.get("config") if hasattr(state, "get") else state["config"]
    cfg_raw = config.to_dict() if hasattr(config, "to_dict") else {}
    # Fall back to a manual dict-ification for msgspec.Struct configs
    # that don't expose to_dict.
    if not cfg_raw:
        try:
            import msgspec
            cfg_raw = msgspec.to_builtins(config)
        except Exception:
            cfg_raw = {}
    transports = getattr(config, "transports", None) or []
    devices    = getattr(config, "devices",    None) or []
    scheduler  = state.get("scheduler") if hasattr(state, "get") else state["scheduler"]
    last_result = getattr(scheduler, "last_result", None) if scheduler else None
    bundle = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "version":        __version__,
        "deployment":     os.environ.get("WATTPOST_DEPLOYMENT", "pi"),
        "demo":           os.environ.get("WATTPOST_DEMO") == "1",
        "platform":       platform.platform(terse=True),
        "python":         ".".join(map(str, sys.version_info[:3])),
        "uptime_seconds": _proc_uptime_seconds(),
        "disk":           _disk_usage("/"),
        "config":         _redact(cfg_raw),
        "transport_count": len(transports),
        "device_count":   len(devices),
        "last_poll": {
            "completed_at": (
                last_result.get("completed_at").isoformat()
                if last_result and hasattr(last_result.get("completed_at"), "isoformat")
                else None
            ) if last_result else None,
            "errors":   (last_result.get("errors") or []) if last_result else [],
            "device_count": len(last_result.get("devices") or []) if last_result else 0,
        },
        "log_tail":       _diag.LOG_RING.lines(),
        "broker_auth":    _diag.recent_broker_auth(),
    }
    body = json.dumps(bundle, indent=2, default=str)
    fname = f"wattpost-diagnostics-{__version__}-{int(time.time())}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@get("/api/diagnostics/broker-auth")
async def broker_auth_log() -> dict[str, Any]:
    """Return the recent broker-auth verify ring (last ~200 hits).

    Each entry: `ts`, `path`, `method`, `verdict`, `header_age_s`,
    `cf_ray`. Verdicts: ok / no-secret / bad-format / expired / bad-mac.

    Use during white-page-on-broker incidents: gaps in the timeline =
    requests not reaching the appliance (Caddy/CF problem upstream);
    a flood of `expired` = clock drift; `bad-mac` = sso_secret drift
    (#148-class bug); `ok` for the failing path = bug post-auth.

    Lives under /api/diagnostics/ not /api/system/ to keep the
    Diagnostics UI page from having to also gate by admin role —
    middleware applies the same session/broker rules as everything
    else; on the broker side the user already authenticated cloud-
    side to reach here.
    """
    from .. import diagnostics as _diag
    return {"items": _diag.recent_broker_auth()}


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
        # Demo flag — the UI renders a persistent banner when this is
        # true so visitors to demo.wattpost.io understand the data is
        # synthetic. Set by WATTPOST_DEMO=1 on the demo container.
        "demo": os.environ.get("WATTPOST_DEMO") == "1",
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
    About. Also reports the deployment type so the UI shows the right
    update path — Docker users can't fire wattpost-update, they need
    `docker compose pull` on the host."""
    deployment = os.environ.get("WATTPOST_DEPLOYMENT", "pi")
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
            "deployment":      deployment,
        }
    state_dict = updater.state.as_dict()
    state_dict["deployment"] = deployment
    return state_dict


@post("/api/system/web-password/rotate")
async def rotate_web_password() -> dict[str, Any]:
    """Generate a new local web password and persist it. Returns the
    new plaintext exactly once — caller must show it to the user
    immediately, we don't store it anywhere readable post-rotation
    apart from the on-disk mirror file (which is mode 0640 root only).

    Reachable from Settings → System on the dashboard. Already
    requires a session (the middleware enforces it for POSTs), so
    rotation is gated to logged-in users only. Stale sessions are
    NOT invalidated — the user who's rotating is logged in on this
    browser, and we don't want to log them out of their own tab.
    Other browser sessions stay valid until they natural-expire (30d)
    OR until the user clicks "Sign out all sessions" elsewhere."""
    from .. import web_auth as _wa
    import secrets as _secrets
    new_pw = _secrets.token_urlsafe(12)
    try:
        _wa.write_password_hash(new_pw)
    except OSError as e:
        log.exception("web-password rotate: hash write failed")
        raise HTTPException(
            status_code=500,
            detail=f"couldn't write the new password hash: {e}",
        )
    # Mirror plaintext for the "I forgot it" case — same path the
    # first-boot helper uses, same 0640 root-only perms. Best-effort;
    # rotation isn't a hard failure if the mirror write throws.
    try:
        _wa.PASSWORD_PLAINTEXT_PATH.write_text(new_pw + "\n", encoding="utf-8")
    except OSError:
        log.warning("web-password rotate: plaintext mirror write failed (non-fatal)")
    log.info("web-password rotated via Settings UI")
    return {"ok": True, "password": new_pw}


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


@get("/api/branding")
async def appliance_branding(state: State) -> dict[str, Any]:
    """White-label branding for this appliance, cached from the cloud
    on each heartbeat. Empty dict when the owner isn't on Installer
    tier (or hasn't paired to the cloud at all) — the dashboard
    falls back to the default WattPost brand in that case."""
    store = state["store"]
    try:
        row = await store.kv_get("cloud.branding")
    except Exception:
        return {}
    if row is None:
        return {}
    import json
    try:
        return json.loads(row[0])
    except Exception:
        return {}


@get("/api/releases/changelog")
async def release_changelog(state: State) -> Response:
    """Cached upstream CHANGELOG.md, refreshed by the update checker
    on every manifest poll. Lets the dashboard preview release notes
    for a not-yet-installed version — bundled docs only know about
    versions <= the running release. Returns 204 if the cache is
    empty (e.g. first-boot before the initial manifest poll); JS
    falls back to the bundled /web/docs/release-notes.md."""
    scheduler = state["scheduler"]
    updater = getattr(scheduler, "_updater", None)
    if updater is None or not updater.state.release_notes_md:
        return Response(content="", media_type="text/markdown",
                        status_code=204)
    return Response(content=updater.state.release_notes_md,
                    media_type="text/markdown")


@post("/api/system/update/apply", status_code=202)
async def update_apply() -> dict[str, Any]:
    """Trigger an in-place upgrade of WattPost.

    Backgrounds the `wattpost-update` helper script so the daemon can
    restart mid-flight (install.sh does `systemctl restart wattpost`
    at the end) without orphaning the update process.

    The helper is sudo-NOPASSWD allowlisted in /etc/sudoers.d/wattpost
    so the daemon's wattpost user can fire it. Helper handles tarball
    download, sha256 verify, atomic swap into /opt/wattpost-src, then
    runs install.sh. Live log at /var/log/wattpost-update.log.
    """
    if not os.path.exists("/usr/local/bin/wattpost-update"):
        raise HTTPException(
            status_code=500,
            detail="wattpost-update helper not found — reinstall to fix",
        )
    # setsid + nohup so the child survives this Python process getting
    # SIGTERM'd by install.sh's `systemctl restart wattpost`. We don't
    # await the result — the caller gets a 202 immediately and polls
    # /api/system/update/log for progress.
    try:
        await asyncio.create_subprocess_exec(
            "/usr/bin/setsid", "sudo", "-n",
            "/usr/local/bin/wattpost-update",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"could not start updater: {e}")
    return {
        "ok": True,
        "log_path": "/var/log/wattpost-update.log",
    }


@get("/api/system/update/log")
async def update_log() -> dict[str, Any]:
    """Tail of /var/log/wattpost-update.log — UI polls this every few
    seconds during an in-progress update to render live progress."""
    path = "/var/log/wattpost-update.log"
    if not os.path.exists(path):
        return {"lines": [], "running": False}
    try:
        with open(path, "r") as f:
            tail = f.readlines()[-200:]
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"cannot read log: {e}")
    # "running" heuristic: if the lock file is held the updater is
    # mid-flight. flock leaves the file around; existence isn't enough
    # so we just check the lock state via a non-blocking flock.
    running = False
    try:
        with open("/run/wattpost-update.lock", "r") as lf:
            import fcntl
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                running = True
    except OSError:
        pass
    return {"lines": tail, "running": running}


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
