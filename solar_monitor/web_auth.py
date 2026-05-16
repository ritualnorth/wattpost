"""Local web UI authentication.

Trust model (see BACKLOG.md → Local UI authentication for the full
table):

  * /kiosk + GETs on read endpoints: anonymous, read-only.
  * Source-IP loopback (127.0.0.1 / ::1): trusted. cloudflared on the
    appliance proxies to localhost so any request from the loopback
    came through the authenticated CF tunnel (cloud already authed
    the user when they signed in to app.wattpost.io). LAN visitors
    have non-loopback source IPs and can't spoof loopback — the
    kernel decides which interface a packet came in on.
  * LAN access to write endpoints / `/`: password required.
  * WATTPOST_DEMO=1: middleware does nothing (demo dashboard stays
    public so visitors can poke around without an account).
  * `/api/heartbeat`: stays bearer-token authed (appliance → cloud
    flow, unchanged).

Storage:
  * Password hash lives in `/etc/wattpost/web-password.hash`
    (argon2id, regenerated on every change). Group-readable by
    `wattpost` so the daemon can read it; written 0640.
  * Sessions: in-memory only — they don't survive a daemon restart.
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

import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger(__name__)


PASSWORD_HASH_PATH      = Path("/etc/wattpost/web-password.hash")
PASSWORD_PLAINTEXT_PATH = Path("/etc/wattpost/web-password")
SESSION_COOKIE_NAME     = "wp_local_session"
SESSION_TTL_SECONDS     = 60 * 60 * 24 * 30   # 30 days
# Paths that don't need auth even on LAN. /kiosk is the wall-display
# route; the static-asset router serves uPlot/CSS/etc; the readonly
# /api endpoints that the kiosk + dashboard polls need to work
# anonymously when in read-only-public mode.
ANONYMOUS_PATH_PREFIXES = (
    "/kiosk",
    "/web/",
    "/login",
    "/manifest.webmanifest",
    "/service-worker.js",
    "/api/login",
    # /api/heartbeat is bearer-token authed elsewhere; the middleware
    # leaves the auth header path alone.
)

# In-memory session store. Token → (issued_at_epoch,) tuple so a
# future expiry policy can add fields without breaking unpacks.
_SESSIONS: dict[str, float] = {}


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


def issue_session() -> str:
    """Generate a fresh session token and remember it. Returns the
    token; caller drops it in the response Set-Cookie."""
    _gc_sessions()
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = time.time()
    return token


def revoke_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def is_session_valid(token: str | None) -> bool:
    if not token:
        return False
    issued = _SESSIONS.get(token)
    if issued is None:
        return False
    if time.time() - issued > SESSION_TTL_SECONDS:
        _SESSIONS.pop(token, None)
        return False
    return True


def _gc_sessions() -> None:
    """Drop sessions older than the TTL. O(n) scan; n is small here
    (a couple of admin browsers, in practice). Runs only on issue —
    don't pay the cost on every request."""
    now = time.time()
    expired = [t for t, issued in _SESSIONS.items() if now - issued > SESSION_TTL_SECONDS]
    for t in expired:
        _SESSIONS.pop(t, None)


def is_loopback_source(scope: dict) -> bool:
    """Returns True only for a *real* loopback request — i.e. local
    curl, SSH port-forward, the appliance talking to itself.

    Returns False when the request came through the Cloudflare Tunnel,
    even though the TCP peer is 127.0.0.1. cloudflared on the appliance
    proxies tunnel traffic to localhost, so the kernel-level client
    address always looks loopback. Without distinguishing tunnel vs.
    real-loopback, anyone with the public tunnel URL would inherit
    "tunnel-loopback trust" and bypass auth entirely — a complete
    security hole.

    Detection: cloudflared always injects `CF-Ray` and
    `CF-Connecting-IP` headers when forwarding from the public
    hostname. A direct loopback request has neither.
    """
    client = scope.get("client")
    if not client:
        return False
    host = client[0] if isinstance(client, (list, tuple)) and client else None
    if not host:
        return False
    if host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return False
    # Tunnel-origin sniff. cloudflared sets these headers; a real
    # loopback request (curl from the Pi, SSH -L tunnel) won't.
    for k, _v in scope.get("headers", []):
        if k in (b"cf-ray", b"cf-connecting-ip", b"cf-ipcountry"):
            return False
    return True


def is_tunnel_origin(scope: dict) -> bool:
    """True iff this request arrived via the Cloudflare Tunnel — i.e.
    the appliance's cloudflared proxied it from the public hostname.

    Used by the auth middleware to also disable the "readonly public"
    GET bypass for tunnel traffic. Without this, a leaked tunnel URL
    grants anonymous read access to every device + metric on the
    appliance even though writes require login."""
    for k, _v in scope.get("headers", []):
        if k in (b"cf-ray", b"cf-connecting-ip", b"cf-ipcountry"):
            return True
    return False


def is_anonymous_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in ANONYMOUS_PATH_PREFIXES) \
        or path == "/api/heartbeat"  # bearer-token endpoint, leave to that layer


def hash_password(plaintext: str) -> str:
    return _argon2_hasher().hash(plaintext)


def write_password_hash(plaintext: str) -> None:
    """Replace the stored password hash. Caller is responsible for
    file ownership / mode — this just writes the bytes."""
    PASSWORD_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)
    PASSWORD_HASH_PATH.write_text(hash_password(plaintext) + "\n", encoding="utf-8")
