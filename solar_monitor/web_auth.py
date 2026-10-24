"""Local web UI authentication.

Trust model (see BACKLOG.md → Local UI authentication for the full
table):

  * /kiosk + GETs on read endpoints: anonymous, read-only.
  * Source-IP loopback (127.0.0.1 / ::1): trusted, but only when the
    request carries no reverse-proxy forwarding headers. A request
    that is genuinely local (curl on the Pi, an SSH port-forward, the
    daemon talking to itself) has none; one relayed by a reverse proxy
    on the same host (nginx, Caddy, ...) does, and is treated like any
    other remote request. LAN visitors have non-loopback source IPs
    and can't spoof loopback, the kernel decides which interface a
    packet came in on.
  * LAN access to write endpoints / `/`: password required.
  * WATTPOST_DEMO=1: middleware does nothing (demo dashboard stays
    public so visitors can poke around without an account).
  * `/api/heartbeat`: stays bearer-token authed (appliance → cloud
    flow, unchanged).

Storage:
  * Password hash lives in `/etc/wattpost/web-password.hash`
    (argon2id, regenerated on every change). Group-readable by
    `wattpost` so the daemon can read it; written 0640.
  * Sessions: in-memory only, they don't survive a daemon restart.
    Acceptable trade-off: a brief re-login after `Restart wattpost`
    is fine; we get no DB write path complexity in return.

First-boot:
  * If the hash file doesn't exist and we're not in demo mode, the
    installer (packaging/install.sh on the SD image) generates a
    short random passphrase + writes the hash AND the plaintext to
    /etc/wattpost/web-password (so the MOTD can print it). Both
    files are 0640 root:wattpost.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger(__name__)


# Default paths. Overridable via WATTPOST_PASSWORD_DIR for installs
# (Docker, custom data layouts) that put state somewhere other than
# /etc/wattpost. The Pi SD-card image keeps the default; Docker
# users already volume-mount /etc/wattpost so the default is fine
# there too.
_PASSWORD_DIR = Path(os.environ.get("WATTPOST_PASSWORD_DIR", "/etc/wattpost"))
PASSWORD_HASH_PATH      = _PASSWORD_DIR / "web-password.hash"
# Sessions persist to a tiny JSON file in the same config dir. A SQLite
# table would be overkill for the ~handful of active sessions a typical
# install ever holds, and JSON keeps web_auth decoupled from the
# storage layer (Store is wired in at scheduler-time, web_auth gets
# imported earlier than that). Atomic write-then-rename means a power
# loss can't half-corrupt the file.
SESSIONS_PATH           = _PASSWORD_DIR / "sessions.json"
PASSWORD_PLAINTEXT_PATH = _PASSWORD_DIR / "web-password"
# Written by ensure_first_boot_password() when it auto-generates the
# initial password. Its presence means "the current password is the
# untouched auto-generated one the user has never seen" → the login
# page offers a one-time in-browser "create your password" step instead
# of making them fish the random one out of the MOTD / docker logs.
# Cleared the moment the user sets or rotates their own password.
FIRST_RUN_MARKER_PATH   = _PASSWORD_DIR / "web-password.autogen"
SESSION_COOKIE_NAME     = "wp_local_session"
SESSION_TTL_SECONDS     = 60 * 60 * 24 * 30   # 30 days
# Paths that don't need auth even on LAN. /kiosk is the wall-display
# route; the static-asset router serves uPlot/CSS/etc; the readonly
# /api endpoints that the kiosk + dashboard polls need to work
# anonymously when in read-only-public mode.
ANONYMOUS_PATH_PREFIXES = (
    # NB: /kiosk is NOT here. With login mandatory (cloud #15) a wall
    # display authenticates with a kiosk token (?token= -> kiosk cookie,
    # granted the read-only KIOSK allow-list by the middleware). Leaving
    # /kiosk anonymous would re-open the exact public-view hole we closed.
    "/web/",
    # Prometheus scrape endpoint (#14). Scrapers don't carry a session;
    # it's read-only LAN telemetry (same data as /kiosk + /api/snapshot)
    # and 404s when no prometheus exporter is configured.
    "/metrics",
    "/login",
    "/manifest.webmanifest",
    "/service-worker.js",
    "/api/login",
    # First-run "create your password" POST. Pre-auth by necessity (the
    # box has an auto-generated password the user has never seen); the
    # handler self-guards on is_first_run() (the marker is cleared by the
    # first successful login or password change) and shares the login
    # brute-force backoff. It does NOT reject proxied requests.
    "/api/setup/first-run-password",
    # Kiosk-token claim: exchanges a valid ?token= for a read-only
    # kiosk session cookie. Pre-auth by necessity (a wall display has
    # no login); self-guards on verify_kiosk_token().
    "/api/kiosk/claim",
    # Identity v2 Phase 3 (#305), OIDC redirect endpoints. /auth/lan/login
    # initiates the flow; /auth/callback completes it. Both verify their
    # own state before issuing a session, so they're safe-anon.
    "/auth/lan/login",
    "/auth/callback",
    "/api/system/oidc-available",
    # /api/system/auth-status is a read-only, no-PII "are you authed?"
    # signal the SPA uses to gate the Sign In button. It HAS to be
    # anonymous-accessible, if it required a session, the unauthed
    # case would 401 instead of returning {authed: false} and the JS
    # would have to interpret that as a no, which is more fragile.
    "/api/system/auth-status",
    # /api/heartbeat is bearer-token authed elsewhere; the middleware
    # leaves the auth header path alone.
    #
    # Captive-portal probe endpoints (Pillar 3b). A device joining the
    # hotspot is pre-auth by definition; its OS hits these to detect the
    # portal, so they MUST answer without a session. They only redirect
    # to the dashboard when the captive portal is active. See api/captive.py.
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
)

# In-memory session store. Token → {"issued_at": epoch, "origin": "local"|"kiosk"}.
# Origin matters because a kiosk session is read-only (kiosk allow-list
# only) whereas a local-password session is full access.
#
# Read-through cache over SESSIONS_PATH: every issue/revoke writes the
# file, every read hits the dict. On module import we slurp whatever
# the file had (so daemon restart preserves logged-in users, fixed
# in v0.0.75 after a pentest revealed the in-memory-only store would
# silently log everyone out on every container restart, including the
# user-initiated Settings → Restart daemon).
_SESSIONS: dict[str, dict[str, float | str]] = {}


def _load_sessions_from_disk() -> None:
    """Populate _SESSIONS from SESSIONS_PATH at module-import time.
    Tolerates a missing or unreadable file by starting empty, sessions
    are recoverable (user re-logs in) so a clobbered file isn't fatal."""
    import json as _json
    if not SESSIONS_PATH.is_file():
        return
    try:
        raw = _json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            now = time.time()
            for token, rec in raw.items():
                if not isinstance(rec, dict): continue
                issued = rec.get("issued_at")
                if not isinstance(issued, (int, float)): continue
                if now - float(issued) > SESSION_TTL_SECONDS: continue
                _SESSIONS[token] = {
                    "issued_at": float(issued),
                    "origin":    str(rec.get("origin", "local")),
                }
    except (OSError, ValueError, TypeError):
        # Corrupt or missing file → start with empty cache. Users will
        # need to re-login; recoverable, no data loss.
        pass


def _persist_sessions_to_disk() -> None:
    """Atomic-write the current _SESSIONS dict to SESSIONS_PATH. Called
    on every issue + revoke (cheap, dict is tiny). Write-temp-rename
    means a power loss can't half-corrupt the on-disk file.

    Failure is non-fatal: a write error logs and continues. The
    in-memory cache stays correct for THIS process; only the persisted
    state lags. Next successful write catches up."""
    import json as _json
    try:
        SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        from .fsutil import atomic_write_text
        atomic_write_text(SESSIONS_PATH, _json.dumps(_SESSIONS))   # live tokens: 0600
    except OSError:
        # Disk full, permissions, RO filesystem. Don't break login,
        # the user's session is still valid in memory for this run.
        import logging as _lg
        _lg.getLogger(__name__).warning(
            "could not persist sessions to %s, sessions will not "
            "survive daemon restart this cycle", SESSIONS_PATH,
        )


_load_sessions_from_disk()


def _argon2_hasher():
    """Lazy import so the appliance doesn't crash on import when
    argon2-cffi isn't installed (e.g. fresh dev shell). Cached on the
    module."""
    global _hasher
    try:
        return _hasher
    except NameError:
        from argon2 import PasswordHasher
        _hasher = PasswordHasher()
        return _hasher


def is_demo_mode() -> bool:
    return os.environ.get("WATTPOST_DEMO") == "1"


def password_is_set() -> bool:
    return PASSWORD_HASH_PATH.is_file() and PASSWORD_HASH_PATH.stat().st_size > 0


def is_first_run() -> bool:
    """True when the web password is still the auto-generated first-boot
    one (marker present) and we're not in demo mode. Drives the
    in-browser 'create your password' onboarding so a LAN user can claim
    the box without SSHing in to read the MOTD."""
    if is_demo_mode():
        return False
    if not FIRST_RUN_MARKER_PATH.is_file():
        return False
    # Somebody already signed in with the password (older versions never
    # cleared the marker on login): the box has been claimed, so the
    # anonymous "create your password" window is closed for good.
    if any(r.get("origin") == "local" for r in _SESSIONS.values()):
        clear_first_run_marker()
        return False
    return True


def clear_first_run_marker() -> None:
    """Drop the autogen marker — the current password is no longer the
    untouched auto-generated one. Best-effort; idempotent."""
    try:
        FIRST_RUN_MARKER_PATH.unlink()
    except OSError:
        pass


def set_user_web_password(plaintext: str) -> None:
    """Persist a user-CHOSEN web password (the first-run flow): write the
    hash, drop the autogen marker, and remove the plaintext mirror. A
    secret the user picked themselves shouldn't sit on disk in the clear
    the way the throwaway auto-generated one did (which we kept only so
    the MOTD could surface it)."""
    write_password_hash(plaintext)
    clear_first_run_marker()
    try:
        PASSWORD_PLAINTEXT_PATH.unlink()
    except OSError:
        pass


def verify_password(plaintext: str) -> bool:
    """Returns True iff `plaintext` matches the stored hash. False on
    any error (no hash file, bad hash format, mismatch)."""
    if not password_is_set():
        return False
    try:
        stored = PASSWORD_HASH_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not stored:
        return False
    hasher = _argon2_hasher()
    try:
        hasher.verify(stored, plaintext)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Local kiosk tokens (#cloud-15, Phase A). A revocable, read-only credential
# for off-grid wall displays, so they keep working once anonymous viewing is
# gone. Stored hashed (the token is high-entropy random, so a fast SHA-256
# is fine — no need for argon2's per-call cost on every kiosk poll). The
# token is presented as `/kiosk?token=<plaintext>`; the SPA claims it once
# per load into a kiosk-origin session cookie (see /api/kiosk/claim), and the
# middleware then grants only the read-only KIOSK allow-list. Unlike the old
# `?key=` bypass this is revocable, rotatable, scoped, and never stored in
# the clear.
KIOSK_TOKENS_PATH = _PASSWORD_DIR / "kiosk-tokens.json"


def _kiosk_hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _load_kiosk_tokens() -> list[dict]:
    try:
        raw = json.loads(KIOSK_TOKENS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _save_kiosk_tokens(toks: list[dict]) -> None:
    KIOSK_TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    from .fsutil import atomic_write_text
    atomic_write_text(KIOSK_TOKENS_PATH, json.dumps(toks))


def create_kiosk_token(name: str = "", skin: str = "") -> tuple[str, str]:
    """Mint a kiosk token. Returns (id, plaintext); the plaintext is shown
    to the admin once and never stored in the clear. `skin` binds a kiosk
    theme to this link so each wall display can differ; empty = fall back to
    the appliance-wide default at render time."""
    plaintext = secrets.token_urlsafe(24)
    tid = secrets.token_hex(8)
    toks = _load_kiosk_tokens()
    toks.append({
        "id": tid,
        "name": (name or "Kiosk").strip()[:60],
        "hash": _kiosk_hash(plaintext),
        "skin": (skin or "").strip().lower()[:20],
        "created_at": time.time(),
    })
    _save_kiosk_tokens(toks)
    return tid, plaintext


def list_kiosk_tokens() -> list[dict]:
    """Metadata only — never the hash."""
    return [
        {"id": t.get("id"), "name": t.get("name", ""),
         "skin": t.get("skin", ""), "created_at": t.get("created_at")}
        for t in _load_kiosk_tokens()
    ]


def kiosk_token_skin(plaintext: str | None) -> str | None:
    """The theme bound to this kiosk token, or None if the token is unknown
    or pinned to no specific skin (render falls back to the global default)."""
    if not plaintext:
        return None
    h = _kiosk_hash(plaintext)
    for t in _load_kiosk_tokens():
        if hmac.compare_digest(str(t.get("hash", "")), h):
            return (t.get("skin") or "") or None
    return None


def revoke_kiosk_token(tid: str) -> bool:
    toks = _load_kiosk_tokens()
    kept = [t for t in toks if t.get("id") != tid]
    if len(kept) == len(toks):
        return False
    _save_kiosk_tokens(kept)
    return True


def verify_kiosk_token(plaintext: str | None) -> bool:
    if not plaintext:
        return False
    h = _kiosk_hash(plaintext)
    for t in _load_kiosk_tokens():
        if hmac.compare_digest(str(t.get("hash", "")), h):
            return True
    return False


def session_origin(token: str | None) -> str | None:
    """Origin tag of a live session ('local' | 'kiosk'), or None."""
    rec = _session_record(token)
    return None if rec is None else str(rec.get("origin", "local"))


# --------------------------------------------------------------------------
# Login brute-force backoff (cloud #15, Phase A). Per-source-IP, in-memory,
# exponential. We back the SOURCE off rather than locking the account, so an
# attacker can't lock the owner out by failing on purpose. Genuine loopback
# is exempted by the caller. Lost on restart — fine, the
# window is short and an attacker gains nothing from a daemon bounce.
_LOGIN_FAILS: dict[str, dict] = {}     # ip -> {"n": int, "until": epoch}
_LOGIN_FREE_ATTEMPTS = 5               # tries before backoff starts
_LOGIN_BACKOFF_CAP   = 300             # max lockout (seconds)


def login_locked_for(ip: str) -> float:
    """Seconds remaining until `ip` may try again (0.0 if not locked)."""
    rec = _LOGIN_FAILS.get(ip)
    if not rec:
        return 0.0
    rem = float(rec.get("until", 0.0)) - time.time()
    return rem if rem > 0 else 0.0


def record_login_failure(ip: str) -> None:
    rec = _LOGIN_FAILS.setdefault(ip, {"n": 0, "until": 0.0})
    rec["n"] = int(rec["n"]) + 1
    if rec["n"] > _LOGIN_FREE_ATTEMPTS:
        backoff = min(_LOGIN_BACKOFF_CAP, 2 ** (rec["n"] - _LOGIN_FREE_ATTEMPTS))
        rec["until"] = time.time() + backoff


def record_login_success(ip: str) -> None:
    _LOGIN_FAILS.pop(ip, None)


def issue_session(origin: str = "local") -> str:
    """Generate a fresh session token and remember it. Returns the
    token; caller drops it in the response Set-Cookie.

    `origin` tags the session: "local" for local-password logins,
    "kiosk" for read-only kiosk-token sessions."""
    _gc_sessions()
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {"issued_at": time.time(), "origin": origin}
    _persist_sessions_to_disk()
    return token


def revoke_session(token: str) -> None:
    if _SESSIONS.pop(token, None) is not None:
        _persist_sessions_to_disk()


def _session_record(token: str | None) -> dict[str, float | str] | None:
    if not token:
        return None
    rec = _SESSIONS.get(token)
    if rec is None:
        return None
    if time.time() - float(rec["issued_at"]) > SESSION_TTL_SECONDS:
        _SESSIONS.pop(token, None)
        _persist_sessions_to_disk()
        return None
    return rec


def is_session_valid(token: str | None) -> bool:
    return _session_record(token) is not None


def _gc_sessions() -> None:
    """Drop sessions older than the TTL. O(n) scan; n is small here
    (a couple of admin browsers, in practice). Runs only on issue,
    don't pay the cost on every request."""
    now = time.time()
    expired = [t for t, rec in _SESSIONS.items()
               if now - float(rec["issued_at"]) > SESSION_TTL_SECONDS]
    if expired:
        for t in expired:
            _SESSIONS.pop(t, None)
        _persist_sessions_to_disk()


# Headers a reverse proxy / CDN adds when it relays a request. A genuinely
# local request (curl on the Pi, SSH -L, the daemon itself) never has them.
# A denylist can never be complete (any proxy can be configured to strip or
# not add them), which is why `web.trust_loopback: false` exists for
# same-host proxies; this just catches the common ones.
_PROXY_HEADERS = frozenset({
    b"via", b"forwarded", b"x-real-ip", b"true-client-ip",
    b"x-original-forwarded-for", b"x-client-ip", b"fastly-client-ip",
    b"x-cluster-client-ip",
})
_PROXY_HEADER_PREFIXES = (b"x-forwarded-", b"cf-")

# Whether a loopback peer is trusted as "the local owner" at all
# (config: web.trust_loopback). Set once at startup by build_app().
TRUST_LOOPBACK = True


def set_trust_loopback(value: bool) -> None:
    global TRUST_LOOPBACK
    TRUST_LOOPBACK = bool(value)


def _foreign_origin(value: bytes) -> bool:
    """True when an Origin / Referer value names a non-loopback host (or is
    unparseable / "null"). A browser running on the Pi itself, pointed at
    localhost, never sends one."""
    from .urlcheck import host_of, is_loopback_host
    try:
        text = value.decode("latin-1").strip()
    except Exception:
        return True
    if not text:
        return False
    return not is_loopback_host(host_of(text))


def is_proxied(scope: dict) -> bool:
    """True iff the request looks relayed: it carries reverse-proxy / CDN
    forwarding headers, or an Origin/Referer pointing at a non-loopback host.
    Then the TCP peer address says nothing about who the real client is."""
    for k, v in scope.get("headers", []):
        k = k.lower()
        if k in _PROXY_HEADERS or k.startswith(_PROXY_HEADER_PREFIXES):
            return True
        if k in (b"origin", b"referer") and _foreign_origin(v):
            return True
    return False


def is_loopback_source(scope: dict) -> bool:
    """Returns True only for a *real* loopback request, i.e. local
    curl, SSH port-forward, the appliance talking to itself.

    Returns False when the TCP peer is loopback but the request was
    relayed by a reverse proxy on the same host (forwarding headers, or a
    foreign Origin/Referer): the kernel-level client address then always
    looks loopback, so trusting it would let anyone who can reach the proxy
    bypass auth. With `web.trust_loopback: false` loopback gets no special
    treatment at all.
    """
    if not TRUST_LOOPBACK:
        return False
    client = scope.get("client")
    if not client:
        return False
    host = client[0] if isinstance(client, (list, tuple)) and client else None
    if not host:
        return False
    if host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return False
    return not is_proxied(scope)


def is_anonymous_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in ANONYMOUS_PATH_PREFIXES) \
        or path == "/api/heartbeat"  # bearer-token endpoint, leave to that layer


def hash_password(plaintext: str) -> str:
    return _argon2_hasher().hash(plaintext)


def write_password_hash(plaintext: str) -> None:
    """Replace the stored password hash, owner-only (0600)."""
    PASSWORD_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)
    PASSWORD_HASH_PATH.write_text(hash_password(plaintext) + "\n", encoding="utf-8")
    try:
        os.chmod(PASSWORD_HASH_PATH, 0o600)  # never world-readable
    except OSError:
        pass


def ensure_first_boot_password() -> str | None:
    """Generate + persist a random initial password if none is set.

    Called once on daemon startup. Closes the "Docker installs ship
    with no password, so the auth middleware bypasses everything"
    hole, packaging/install.sh did this for the Pi SD image, but
    Docker users never ran install.sh and were left wide open.

    Returns the plaintext if a new password was just generated,
    None if one already existed.

    The plaintext is also logged at WARNING level so Docker users
    can grab it from `docker compose logs wattpost`."""
    if is_demo_mode():
        return None
    if password_is_set():
        return None
    plaintext = secrets.token_urlsafe(12)
    try:
        write_password_hash(plaintext)
    except OSError as e:
        log.error(
            "first-boot password generation FAILED: %s. "
            "Auth is currently bypassed because no password file exists. "
            "Fix permissions on %s (or set WATTPOST_PASSWORD_DIR to a "
            "writable path) and restart the daemon.",
            e, PASSWORD_HASH_PATH.parent,
        )
        return None
    # Mirror the plaintext to a sibling file so the MOTD on Pi
    # installs and `docker exec cat /etc/wattpost/web-password` on
    # Docker installs can both show it. Best-effort.
    try:
        PASSWORD_PLAINTEXT_PATH.write_text(plaintext + "\n", encoding="utf-8")
        os.chmod(PASSWORD_PLAINTEXT_PATH, 0o600)  # cleartext — owner-only
    except OSError:
        pass
    # Mark this as the untouched auto-generated password so the login
    # page offers the in-browser "create your password" step until the
    # user picks their own (or rotates).
    try:
        FIRST_RUN_MARKER_PATH.write_text("auto-generated\n", encoding="utf-8")
    except OSError:
        pass
    log.warning("=" * 64)
    log.warning("FIRST-BOOT: generated initial web password")
    log.warning("")
    log.warning("    %s", plaintext)
    log.warning("")
    log.warning("Save it now, you'll need it to log into the dashboard.")
    log.warning("Rotate via Settings → System → Reset web password (or")
    log.warning("`wattpost-config` on the Pi). Stored at %s",
                PASSWORD_HASH_PATH)
    log.warning("=" * 64)
    return plaintext
