"""HTML pages, kiosk pages, login/logout, service worker and manifest (split from app.py)."""
from __future__ import annotations

from pathlib import Path

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import File


import logging
log = logging.getLogger(__name__)


def _web_dir() -> Path:
    # Deliberately NOT .resolve(): on the Pi the daemon runs from the
    # /opt/wattpost symlink, so __file__ is /opt/wattpost/venv/.../app.py.
    # .resolve() would canonicalise that symlink down to the concrete slot
    # the daemon imported from (/opt/wattpost-slots/<x>), pinning the web
    # directory — and the static-files router caches it at startup — to that
    # slot. After an atomic slot swap the symlink moves but the resolved path
    # doesn't, so /web/* keeps serving the OLD slot's assets until a restart
    # (stale dashboard). Leaving the symlink unresolved means the OS follows
    # the live symlink on every file open, so a swap is reflected immediately.
    # No-op on Docker/dev where there's no slot symlink in the path.
    return Path(__file__).parent.parent / "web"




@get("/", sync_to_thread=False)
def index() -> File:
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing, was the package built correctly?")
    # no-cache (revalidate, not no-store) so the browser always re-checks the
    # shell HTML and picks up new ?v= asset versions after a deploy. Without
    # it Safari heuristically caches the shell for hours and serves stale
    # app.js/styles.css (the recurring "stale shell"). Assets stay
    # long-cached via their ?v= busters; only the shell revalidates.
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


@get("/kiosk", sync_to_thread=False)
def kiosk_index() -> File:
    """Public, chrome-free wall-display view of the SoC + flow.

    Serves the same SPA bundle as `/`; the boot script in app.js
    detects `location.pathname === '/kiosk'` and flips the SPA into
    kiosk mode before first render. Real server-side path (rather
    than the previous `/#/kiosk` hash route) so the local-auth
    middleware can whitelist this single URL without exposing the
    rest of the dashboard."""
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing")
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


# Mode-aware kiosk route — pin a wall display at /kiosk/cabin (for
# example) and the SPA reads the path segment, sets `data-mode` on
# <body>, and renders the layout tuned for that persona. The validation
# is permissive: unknown modes fall back to the standard kiosk view
# rather than 404'ing, so a typo'd URL still gives the wall something
# useful to show. See [[modes-state-machine]] for the full set.
_VALID_KIOSK_MODES = frozenset({"home", "van", "cabin", "marine", "kiosk"})


@get("/kiosk/{mode:str}", sync_to_thread=False)
def kiosk_index_mode(mode: str) -> File:
    """Mode-aware kiosk view (one of home / van / cabin / marine / kiosk)."""
    path = _web_dir() / "index.html"
    if not path.exists():
        raise NotFoundException("index.html missing")
    # Pass the requested mode through to the SPA via a header the
    # boot script reads; it doesn't need to be in the body since the
    # path itself encodes the intent (`window.location.pathname`).
    return File(path=path, media_type="text/html", content_disposition_type="inline",
                headers={"Cache-Control": "no-cache"})


@get("/login", sync_to_thread=False)
def login_page(request: Request) -> File | Response:
    """Static HTML login form. POSTs to /api/login → cookie + redirect."""
    from .. import web_auth as _wa
    # First run: the password is still the auto-generated one the user
    # has never seen. Offer an in-browser "create your password" page
    # instead of an "enter password" form they can't fill. `?signin=1`
    # forces the normal form for the rare user who DID read the MOTD
    # password and wants to use it.
    qs = (request.scope.get("query_string", b"") or b"").decode("latin-1")
    if _wa.is_first_run() and "signin=1" not in qs:
        setup = _web_dir() / "login-setup.html"
        if setup.exists():
            return File(path=setup, media_type="text/html",
                        content_disposition_type="inline")
    path = _web_dir() / "login.html"
    if not path.exists():
        raise NotFoundException("login.html missing")
    return File(path=path, media_type="text/html", content_disposition_type="inline")


@post("/api/setup/first-run-password", status_code=200)
async def first_run_password(data: dict, request: Request, state: State) -> Response:
    """One-time, pre-auth endpoint: set the appliance's web password on
    first run (while it's still the auto-generated one). Lets a LAN user
    claim the box in-browser instead of fishing the random password out
    of the MOTD / docker logs. Self-guards: only works while
    is_first_run(): the marker it checks is cleared by the first
    successful login or any password change, so once the owner has been
    in this endpoint is closed. Shares the /api/login brute-force
    backoff. (It does not reject proxied requests.)"""
    from .. import web_auth as _wa
    client_ip = request.scope.get("client", ("?",))[0] if request.scope.get("client") else "?"
    loopback = _wa.is_loopback_source(request.scope)
    if not loopback:
        wait = _wa.login_locked_for(client_ip)
        if wait > 0:
            return Response(
                {"ok": False, "detail": "too many attempts — try again shortly"},
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )
    if not _wa.is_first_run():
        if not loopback:
            _wa.record_login_failure(client_ip)
        return Response(
            {"ok": False,
             "detail": "already set up — sign in with your password"},
            status_code=409,
        )
    pw = (data or {}).get("password") or ""
    if not isinstance(pw, str) or len(pw) < 8:
        return Response(
            {"ok": False, "detail": "password must be at least 8 characters"},
            status_code=400,
        )
    try:
        _wa.set_user_web_password(pw)
    except OSError as e:
        log.exception("first-run password: hash write failed")
        return Response(
            {"ok": False, "detail": f"couldn't save the password: {e}"},
            status_code=500,
        )
    await _audit(state, event_type="first_run_password_set", payload={"ip": client_ip})
    # Log them straight in so they land in Settings, not a second prompt.
    token = _wa.issue_session()
    nxt = (data or {}).get("next") or "/"
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = Response({"ok": True, "redirect": nxt})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME, value=token,
        max_age=_wa.SESSION_TTL_SECONDS, httponly=True,
        samesite="lax", path="/", secure=False,
    )
    return resp


@post("/api/kiosk/claim", status_code=200, sync_to_thread=False)
def kiosk_claim(data: dict, request: Request) -> Response:
    """Exchange a valid kiosk token for a read-only kiosk session cookie.
    The wall display loads `/kiosk?token=<t>` and the SPA calls this once
    per load. Self-guards: only issues a cookie for a token that verifies."""
    from .. import web_auth as _wa
    tok = (data or {}).get("token") or ""
    if not _wa.verify_kiosk_token(tok):
        return Response({"ok": False, "detail": "invalid or revoked kiosk token"},
                        status_code=401)
    session = _wa.issue_session(origin="kiosk")
    resp = Response({"ok": True})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME, value=session,
        max_age=_wa.SESSION_TTL_SECONDS, httponly=True,
        samesite="lax", path="/", secure=False,
    )
    return resp


async def _audit(state, *, event_type: str, payload: dict | None = None) -> None:
    """Helper: write_event to the signed audit log (Phase 8B).
    Best-effort, never raises into the caller, since security
    touchpoints must not break when audit logging hiccups."""
    try:
        from .. import signed_audit as _sa
        store = state["store"]
        if store is not None and store._db is not None:
            await _sa.write_event(store._db, event_type=event_type, payload=payload or {})
            await store._db.commit()
    except Exception:
        log.exception("signed_audit: write_event(%s) failed", event_type)


@post("/api/login", status_code=200)
async def do_login(data: dict, request: Request, state: State) -> Response:
    """Verify the supplied password, drop a session cookie. Returns
    the URL the caller should redirect to (the `next` query param if
    safe, else `/`). Demo-mode + no-password installs short-circuit
    above this in the middleware, so we don't have to handle them.

    Phase 8B (#310), login outcomes recorded in the signed audit
    log so brute-force probes leave a tamper-evident trace and the
    cloud's per-site security view (when wired) can flag anomalies."""
    from .. import web_auth as _wa
    client_ip = request.scope.get("client", ("?",))[0] if request.scope.get("client") else "?"
    # Brute-force backoff: per-source-IP, exponential after a few free
    # tries. Genuine loopback (on-box) is exempt so the owner can never
    # be locked out of their own device.
    loopback = _wa.is_loopback_source(request.scope)
    if not loopback:
        wait = _wa.login_locked_for(client_ip)
        if wait > 0:
            await _audit(state, event_type="login_throttled", payload={"ip": client_ip})
            return Response(
                {"ok": False, "detail": "too many attempts — try again shortly"},
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )
    pw = (data or {}).get("password") or ""
    if not _wa.verify_password(pw):
        # Record the failure with the client IP, useful for
        # detecting attempted brute force after the fact. Don't
        # include the attempted password (obvious anti-pattern).
        if not loopback:
            _wa.record_login_failure(client_ip)
        await _audit(state, event_type="login_failed", payload={"ip": client_ip})
        return Response({"ok": False, "detail": "wrong password"}, status_code=401)
    if not loopback:
        _wa.record_login_success(client_ip)
    await _audit(state, event_type="login_succeeded", payload={"ip": client_ip})
    # The (possibly auto-generated) password has now been used by someone
    # who knew it: close the anonymous first-run "set a password" window.
    _wa.clear_first_run_marker()
    token = _wa.issue_session()
    # Validate `next` to only allow same-origin relative paths.
    nxt = (data or {}).get("next") or "/"
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = Response({"ok": True, "redirect": nxt})
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME,
        value=token,
        max_age=_wa.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # appliance is HTTP on the LAN
    )
    return resp


@post("/api/logout", status_code=200, sync_to_thread=False)
def do_logout(request: Request) -> Response:
    from .. import web_auth as _wa
    token = request.cookies.get(_wa.SESSION_COOKIE_NAME)
    if token:
        _wa.revoke_session(token)
    resp = Response({"ok": True})
    resp.delete_cookie(key=_wa.SESSION_COOKIE_NAME, path="/")
    return resp


@get("/sw.js", sync_to_thread=False)
def service_worker() -> File:
    """Service worker must be served from the path it claims scope over
   , at /sw.js it can intercept the whole site, at /web/sw.js it could
    only see /web/* requests. So we mirror the file at the root.
    Cache-Control: no-cache so a deployed SW update propagates next
    refresh instead of being held by the browser for a week."""
    path = _web_dir() / "sw.js"
    return File(
        path=path, media_type="application/javascript",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-cache"},
    )


@get("/manifest.webmanifest", sync_to_thread=False)
def manifest() -> File:
    """Mirror the manifest at root too so PWA install prompts find it
    without the /web/ prefix (some Android Chrome versions are picky)."""
    path = _web_dir() / "manifest.webmanifest"
    return File(
        path=path,
        media_type="application/manifest+json",
        content_disposition_type="inline",
    )
