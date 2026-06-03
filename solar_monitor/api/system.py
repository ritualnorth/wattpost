"""System endpoints, disk usage, uptime, update flow, history settings.

Kept out of api/app.py so the route factory stays readable. The
restart + logs endpoints already in app.py would naturally live here
too, leaving them in place to avoid a churn move; this file's the
right home for any future system-level handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import sys
import time
from typing import Any

import httpx

import msgspec
from litestar import Request, get, patch, post
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

    Used to be a /proc/uptime read, which on bare metal gave the
    box's uptime (fine) but in a Docker container with host /proc
    leakage gave the host's uptime (e.g. '3d 23h' on a laptop
    that's just had the container restarted ten minutes ago). The
    daemon process start time is what users actually want to see.
    """
    return time.time() - _DAEMON_STARTED_AT


@get("/api/system/auth-status")
async def auth_status(request: Request, state: State) -> dict[str, Any]:
    """Read-only signal of whether the current request is authed,
    and by what mechanism. Three positive cases:

      1. Local session cookie, set by /api/login after a password
         sign-in. origin="local".
      2. SSO session cookie, set by /sso after a cloud-minted token
         (e.g. dashboard "Open" button → broker-redirect-with-token).
         origin="sso".
      3. Broker HMAC header, every request via the cloud broker
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

    Works identically on Pi and Docker, no journalctl / docker-logs
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
    Diagnostics UI page from having to also gate by admin role,
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
        # Demo flag, the UI renders a persistent banner when this is
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
    About. Also reports the deployment type + whether the in-UI Update
    button can fire: Pi always can (slot-swap helper); Docker can iff the
    wattpost-updater sidecar is configured (WATCHTOWER_URL/TOKEN), else
    the user is told to add the sidecar or `docker compose pull`."""
    deployment = os.environ.get("WATTPOST_DEPLOYMENT", "pi")
    # Can the local Update button apply an update on this box?
    updater_available = (deployment != "docker") or bool(
        (os.environ.get("WATCHTOWER_URL") or "").strip()
        and (os.environ.get("WATCHTOWER_TOKEN") or "").strip()
    )
    scheduler = state["scheduler"]
    updater = getattr(scheduler, "_updater", None)
    if updater is None:
        from .. import __version__ as v
        return {
            "current_version":  v,
            "latest_version":   None,
            "has_update":       False,
            "last_checked_at":  None,
            "last_error":       "update checker not running",
            "deployment":       deployment,
            "updater_available": updater_available,
        }
    state_dict = updater.state.as_dict()
    state_dict["deployment"] = deployment
    state_dict["updater_available"] = updater_available
    return state_dict


@get("/api/system/slots")
async def slot_state() -> dict[str, Any]:
    """Atomic-swap slot layout (#36). Reports which slot is active,
    which slot the auto-rollback would swap to, and the version each
    one is carrying. Returns empty dict on Docker installs and on
    legacy /opt/wattpost layouts that haven't been migrated to slots
    yet, UI should hide the slot card in those cases."""
    deployment = os.environ.get("WATTPOST_DEPLOYMENT", "pi")
    if deployment == "docker":
        return {"applicable": False, "reason": "docker-install"}
    app_root = "/opt/wattpost"
    slots_dir = "/opt/wattpost-slots"
    try:
        if not os.path.islink(app_root) or not os.path.isdir(slots_dir):
            return {"applicable": False, "reason": "legacy-layout"}
        active = os.path.realpath(app_root)
        active_name = os.path.basename(active)
        prev_link = os.path.join(slots_dir, "previous")
        previous = os.path.realpath(prev_link) if os.path.islink(prev_link) else None
        previous_name = os.path.basename(previous) if previous else None

        def _read_version(slot_path: str) -> str | None:
            vfile = os.path.join(slot_path, "version")
            try:
                with open(vfile, "r") as f:
                    return f.read().strip() or None
            except OSError:
                return None

        return {
            "applicable":     True,
            "active":         {"name": active_name, "path": active,
                               "version": _read_version(active)},
            "previous":       (None if previous is None
                               else {"name": previous_name, "path": previous,
                                     "version": _read_version(previous)}),
            "rollback_available": previous is not None and previous != active,
        }
    except Exception as e:
        return {"applicable": False, "reason": f"error: {e}"}


@post("/api/system/slots/rollback", status_code=202)
async def slot_rollback() -> dict[str, Any]:
    """Trigger a rollback to the previous slot. Fires the same
    wattpost-rollback helper that the OnFailure watchdog uses, so
    behaviour is identical between manual and auto rollback.

    Requires that a previous slot is recorded, fresh installs that
    have never updated have nothing to roll back to and get a 400."""
    if os.environ.get("WATTPOST_DEPLOYMENT") == "docker":
        raise HTTPException(
            status_code=400,
            detail="rollback isn't supported on Docker installs, "
                   "downgrade by pulling an earlier image tag.",
        )
    if not os.path.exists("/usr/local/bin/wattpost-rollback"):
        raise HTTPException(
            status_code=400,
            detail="wattpost-rollback helper not found, reinstall to fix",
        )
    if not os.path.islink("/opt/wattpost-slots/previous"):
        raise HTTPException(
            status_code=400,
            detail="no previous slot recorded, this appliance has "
                   "never been updated via wattpost-update, so there's "
                   "nothing to roll back to.",
        )
    try:
        await asyncio.create_subprocess_exec(
            "/usr/bin/setsid", "sudo", "-n",
            "/usr/local/bin/wattpost-rollback",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"could not start rollback: {e}")
    return {"ok": True, "log_path": "/var/log/wattpost-rollback.log"}


@post("/api/system/web-password/rotate")
async def rotate_web_password() -> dict[str, Any]:
    """Generate a new local web password and persist it. Returns the
    new plaintext exactly once, caller must show it to the user
    immediately, we don't store it anywhere readable post-rotation
    apart from the on-disk mirror file (which is mode 0640 root only).

    Reachable from Settings → System on the dashboard. Already
    requires a session (the middleware enforces it for POSTs), so
    rotation is gated to logged-in users only. Stale sessions are
    NOT invalidated, the user who's rotating is logged in on this
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
    # Mirror plaintext for the "I forgot it" case, same path the
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
    """Force a one-off manifest fetch, Settings UI's "Check now"
    button. Independent of the 24h background loop."""
    scheduler = state["scheduler"]
    updater = getattr(scheduler, "_updater", None)
    if updater is None:
        raise HTTPException(status_code=500, detail="update checker not running")
    await updater.check_once()
    return updater.state.as_dict()


class UpdateChannelPatch(msgspec.Struct):
    channel: str


@post("/api/system/update/channel")
async def set_update_channel(
    data: UpdateChannelPatch, state: State,
) -> dict[str, Any]:
    """Switch the release channel this appliance follows (#11).

    Persists `update.channel` to config.yaml and applies it live to the
    running update checker, then fires an immediate manifest re-check so
    the dashboard reflects the new channel's latest version without
    waiting for the daily poll. Stable / beta / edge only.
    """
    import yaml as _yaml, shutil as _shutil
    from pathlib import Path as _Path

    from ..config import UpdateCfg
    from ..update.checker import VALID_CHANNELS

    channel = (data.channel or "").strip().lower()
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"channel must be one of {', '.join(VALID_CHANNELS)}",
        )

    scheduler = state["scheduler"]
    config = state.get("config") if hasattr(state, "get") else state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    # Apply live to the running checker FIRST so a later YAML write
    # failure still leaves the user on the channel they picked (mirrors
    # patch_history_settings' apply-before-persist ordering).
    updater = getattr(scheduler, "_updater", None)
    if updater is not None:
        updater.set_channel(channel)
    # Mirror onto the in-memory Config so a hot-reload doesn't revert it.
    if config is not None:
        config.update = UpdateCfg(channel=channel)

    # Persist under `update:`, preserving any sibling keys.
    path = _Path(config_path)
    raw = _yaml.safe_load(path.read_text()) or {}
    upd = raw.get("update") or {}
    if not isinstance(upd, dict):
        upd = {}
    upd["channel"] = channel
    raw["update"] = upd
    backup = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)
    log.info("update channel set to %s", channel)

    deployment = os.environ.get("WATTPOST_DEPLOYMENT", "pi")
    # Immediate re-check so the UI doesn't wait 24h to learn the new
    # channel's latest. Best-effort; the daily loop catches up on failure.
    if updater is not None:
        try:
            await updater.check_once()
        except Exception:
            log.info("post-channel-switch re-check failed (non-fatal)")
        return {**updater.state.as_dict(), "deployment": deployment}
    return {"channel": channel, "deployment": deployment}


@get("/api/branding")
async def appliance_branding(state: State) -> dict[str, Any]:
    """White-label branding for this appliance, cached from the cloud
    on each heartbeat. Empty dict when the owner isn't on Installer
    tier (or hasn't paired to the cloud at all), the dashboard
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
    for a not-yet-installed version, bundled docs only know about
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

    On Docker there's no slot-swap helper — the daemon can't pull a new
    image + recreate its own container. Instead it fires the same
    `/v1/update` on the wattpost-updater sidecar that the cloud "Update"
    button uses, so the local UI button works on the box itself with no
    `docker compose pull`.
    """
    if os.environ.get("WATTPOST_DEPLOYMENT") == "docker":
        wt_url   = (os.environ.get("WATCHTOWER_URL")   or "").strip()
        wt_token = (os.environ.get("WATCHTOWER_TOKEN") or "").strip()
        if not (wt_url and wt_token):
            raise HTTPException(
                status_code=400,
                detail="No updater sidecar configured. Add the "
                       "wattpost-updater service + WATCHTOWER_URL / "
                       "WATCHTOWER_TOKEN (see the Docker install guide), "
                       "or run `docker compose pull && docker compose up -d`.",
            )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{wt_url.rstrip('/')}/v1/update",
                    headers={"Authorization": f"Bearer {wt_token}"},
                )
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"could not reach the updater sidecar at {wt_url}: "
                       f"{type(e).__name__}: {e}",
            )
        if r.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"updater sidecar returned HTTP {r.status_code}: {r.text[:200]}",
            )
        return {"ok": True, "method": "docker",
                "detail": "Updater sidecar is pulling the new image and "
                          "recreating the container."}

    if not os.path.exists("/usr/local/bin/wattpost-update"):
        # 400, not 500, Litestar hides the `detail` on 5xx so the user
        # would see a useless "Internal Server Error" otherwise. This
        # branch happens on a broken Pi install (helper missing); it's a
        # precondition failure, not a server-side bug.
        raise HTTPException(
            status_code=400,
            detail="wattpost-update helper not found — the install may be "
                   "incomplete. Re-run install.sh.",
        )
    # setsid + nohup so the child survives this Python process getting
    # SIGTERM'd by install.sh's `systemctl restart wattpost`. We don't
    # await the result, the caller gets a 202 immediately and polls
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
    """Tail of /var/log/wattpost-update.log, UI polls this every few
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


# ---------- editable retention + poll interval (#172) ----------

class HistorySettingsPatch(msgspec.Struct):
    """All fields optional; only the ones present in the request get
    applied. Lets the UI patch one knob without resending the rest."""
    poll_interval_seconds: int | None = None
    retention_raw_days:    int | None = None
    retention_min_days:    int | None = None
    retention_hour_days:   int | None = None


@get("/api/system/history_settings")
async def get_history_settings(state: State) -> dict[str, Any]:
    """Current poll cadence + per-tier retention windows.

    Reads from the live scheduler + store (not from config.yaml on
    disk), so values reflect any unsaved PATCH that hasn't been
    persisted yet. The persisted (`saved_*`) fields show the
    config.yaml state for comparison.
    """
    scheduler = state["scheduler"]
    store = state["store"]
    config = state["config"]
    hist = getattr(config, "history", None)
    return {
        "live": {
            "poll_interval_seconds": int(scheduler.interval_seconds),
            "retention_raw_days":    store._retention_raw_s   // 86400,
            "retention_min_days":    store._retention_1min_s  // 86400,
            "retention_hour_days":   store._retention_1hour_s // 86400,
        },
        "saved": {
            "poll_interval_seconds": (hist.poll_interval_seconds if hist else None),
            "retention_raw_days":    (hist.retention_raw_days    if hist else None),
            "retention_min_days":    (hist.retention_min_days    if hist else None),
            "retention_hour_days":   (hist.retention_hour_days   if hist else None),
        },
        "defaults": {
            "poll_interval_seconds": 60,
            "retention_raw_days":    7,
            "retention_min_days":    30,
            "retention_hour_days":   365,
        },
    }


@patch("/api/system/history_settings")
async def patch_history_settings(
    data: HistorySettingsPatch, state: State,
) -> dict[str, Any]:
    """Apply + persist changes to the polling cadence or retention
    windows. Values apply live: the scheduler reads its
    interval_seconds each cycle and the store reads retention on
    every maintenance pass."""
    import yaml as _yaml, shutil as _shutil
    from pathlib import Path as _Path

    scheduler = state["scheduler"]
    store = state["store"]
    config_path: str = state.get("config_path", "config.yaml")

    # Clamp + validate. Reject obvious nonsense; tier ordering is the
    # main invariant, raw must be shortest, hour longest.
    if data.poll_interval_seconds is not None:
        if not (5 <= data.poll_interval_seconds <= 3600):
            raise HTTPException(
                status_code=400,
                detail="poll_interval_seconds must be between 5 and 3600",
            )
    new_raw  = data.retention_raw_days  if data.retention_raw_days  is not None else None
    new_min  = data.retention_min_days  if data.retention_min_days  is not None else None
    new_hour = data.retention_hour_days if data.retention_hour_days is not None else None
    # When validating ordering, fall back to the current live value
    # for any tier the patch isn't touching so we compare apples-to-
    # apples.
    eff_raw  = new_raw  if new_raw  is not None else store._retention_raw_s   // 86400
    eff_min  = new_min  if new_min  is not None else store._retention_1min_s  // 86400
    eff_hour = new_hour if new_hour is not None else store._retention_1hour_s // 86400
    if not (1 <= eff_raw <= 90):
        raise HTTPException(status_code=400, detail="retention_raw_days must be between 1 and 90")
    if not (eff_raw <= eff_min <= 365):
        raise HTTPException(
            status_code=400,
            detail="retention_min_days must be ≥ retention_raw_days and ≤ 365",
        )
    if not (eff_min <= eff_hour <= 3650):
        raise HTTPException(
            status_code=400,
            detail="retention_hour_days must be ≥ retention_min_days and ≤ 3650",
        )

    # Apply live BEFORE persisting. Order matters: if the YAML write
    # fails the user sees a 500 but the values are already in effect
    #, better than failing silently after persisting.
    if data.poll_interval_seconds is not None:
        scheduler.interval_seconds = int(data.poll_interval_seconds)
        log.info("history: live poll interval = %ds", scheduler.interval_seconds)
    store.set_retention_policy(
        raw_days=new_raw, min_days=new_min, hour_days=new_hour,
    )

    # Persist to config.yaml under `history:`. Touch-existing-block
    # so unrelated keys in `history:` (none today, but future-proof)
    # are preserved.
    path = _Path(config_path)
    raw = _yaml.safe_load(path.read_text()) or {}
    hist = raw.get("history") or {}
    if not isinstance(hist, dict):
        hist = {}
    if data.poll_interval_seconds is not None:
        hist["poll_interval_seconds"] = int(data.poll_interval_seconds)
    if new_raw  is not None: hist["retention_raw_days"]  = int(new_raw)
    if new_min  is not None: hist["retention_min_days"]  = int(new_min)
    if new_hour is not None: hist["retention_hour_days"] = int(new_hour)
    raw["history"] = hist
    backup = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)

    return await get_history_settings(state)


class ResetRequest(msgspec.Struct):
    confirm: str
    keep_cloud_pairing: bool = True


# Sub-blocks the reset endpoint will drop entirely. Listed so the YAML
# falls back to first-boot defaults instead of carrying tier overrides
# or quiet-hours rules from the previous install.
_RESET_OPTIONAL_BLOCKS = (
    "bank", "quiet_hours", "forecast", "weather", "history", "discovery",
)
_RESET_LIST_BLOCKS = (
    "transports", "devices", "exporters", "alerts",
    "notification_transports", "output_schedules", "rules",
)


@post("/api/system/reset")
async def reset_to_defaults(
    data: ResetRequest, state: State,
) -> dict[str, Any]:
    import yaml as _yaml
    import shutil as _shutil
    from pathlib import Path as _Path

    if data.confirm != "RESET":
        raise HTTPException(
            status_code=400,
            detail='confirmation string must be exactly "RESET"',
        )

    config_path: str = state.get("config_path", "config.yaml")
    path = _Path(config_path)
    raw = _yaml.safe_load(path.read_text()) or {}

    counts = {k: len(raw.get(k, []) or []) for k in _RESET_LIST_BLOCKS}

    for key in _RESET_LIST_BLOCKS:
        if key in raw:
            raw[key] = []
    for key in _RESET_OPTIONAL_BLOCKS:
        raw.pop(key, None)
    if not data.keep_cloud_pairing:
        raw.pop("cloud", None)

    backup = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)

    log.warning(
        "[reset] config wiped: %s; keep_cloud_pairing=%s",
        counts, data.keep_cloud_pairing,
    )

    return {
        "ok": True,
        "wiped": counts,
        "kept_cloud_pairing": data.keep_cloud_pairing,
        "next_step": (
            "Reload the dashboard. The setup wizard will reopen so "
            "you can re-pair devices. Restart the daemon to fully "
            "release any open transports."
        ),
    }
