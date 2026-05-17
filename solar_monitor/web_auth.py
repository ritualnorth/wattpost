"""Local web UI authentication.

Trust model (see BACKLOG.md → Local UI authentication for the full
table):

  * /kiosk + GETs on read endpoints: anonymous, read-only.
  * Source-IP loopback (127.0.0.1 / ::1): trusted. cloudflared on the
    appliance proxies to localhost so any request from the loopback
    came through the authenticated CF tunnel (cloud already authed
    the user when they signed in to wattpost.cloud). LAN visitors
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


# Default paths. Overridable via WATTPOST_PASSWORD_DIR for installs
# (Docker, custom data layouts) that put state somewhere other than
# /etc/wattpost. The Pi SD-card image keeps the default; Docker
# users already volume-mount /etc/wattpost so the default is fine
# there too.
_PASSWORD_DIR = Path(os.environ.get("WATTPOST_PASSWORD_DIR", "/etc/wattpost"))
PASSWORD_HASH_PATH      = _PASSWORD_DIR / "web-password.hash"
PASSWORD_PLAINTEXT_PATH = _PASSWORD_DIR / "web-password"
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
    "/sso",  # cloud-issued SSO redirect lands here; verifies its own token
    # /api/system/auth-status is a read-only, no-PII "are you authed?"
    # signal the SPA uses to gate the Sign In button. It HAS to be
    # anonymous-accessible — if it required a session, the unauthed
    # case would 401 instead of returning {authed: false} and the JS
    # would have to interpret that as a no, which is more fragile.
    "/api/system/auth-status",
    # /api/heartbeat is bearer-token authed elsewhere; the middleware
    # leaves the auth header path alone.
)

# In-memory session store. Token → {"issued_at": epoch, "origin": "local"|"sso"}.
# Origin matters because tunnel-origin requests must be backed by an
# "sso" session — a local-password session shouldn't grant tunnel
# access (the password is a fallback for LAN, not the perimeter for
# internet exposure).
_SESSIONS: dict[str, dict[str, float | str]] = {}
# Recently-used SSO nonces (jti claims). HMAC tokens are otherwise
# replayable within their 60 s window if intercepted. Keys are the
# nonce strings; values are the unix-second they expire. Cleaned on
# every issue.
_SSO_NONCES_SEEN: dict[str, int] = {}


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


def issue_session(origin: str = "local") -> str:
    """Generate a fresh session token and remember it. Returns the
    token; caller drops it in the response Set-Cookie.

    `origin` tags the session for the tunnel-origin check in the
    middleware — "local" for local-password logins, "sso" for
    cloud-redirect SSO logins. Tunnel-origin requests require an
    "sso" session (see is_session_valid_for_tunnel)."""
    _gc_sessions()
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {"issued_at": time.time(), "origin": origin}
    return token


def revoke_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def _session_record(token: str | None) -> dict[str, float | str] | None:
    if not token:
        return None
    rec = _SESSIONS.get(token)
    if rec is None:
        return None
    if time.time() - float(rec["issued_at"]) > SESSION_TTL_SECONDS:
        _SESSIONS.pop(token, None)
        return None
    return rec


def is_session_valid(token: str | None) -> bool:
    return _session_record(token) is not None


def is_session_valid_for_tunnel(token: str | None) -> bool:
    """Tunnel-origin requests need a session whose origin is "sso" —
    i.e. one issued by the /sso endpoint after verifying a cloud-
    signed redirect token. Local-password sessions don't qualify
    (you can still use the password on the LAN, but it can't grant
    you internet-facing access on its own)."""
    rec = _session_record(token)
    if rec is None:
        return False
    return rec.get("origin") == "sso"


def _gc_sessions() -> None:
    """Drop sessions older than the TTL. O(n) scan; n is small here
    (a couple of admin browsers, in practice). Runs only on issue —
    don't pay the cost on every request."""
    now = time.time()
    expired = [t for t, rec in _SESSIONS.items()
               if now - float(rec["issued_at"]) > SESSION_TTL_SECONDS]
    for t in expired:
        _SESSIONS.pop(t, None)
    # Also GC the SSO nonce cache.
    expired_n = [n for n, exp in _SSO_NONCES_SEEN.items() if exp < int(now)]
    for n in expired_n:
        _SSO_NONCES_SEEN.pop(n, None)


def verify_broker_auth(header_value: str, sso_secret_hex: str) -> bool:
    """Verify the cloud-broker auth header (#139).

    Format: `<ts>.<hmac_b64url>` where hmac signs the unix-second `ts`
    with the per-appliance `sso_secret`. The appliance trusts any
    request bearing a valid broker header — the cloud is the
    authenticated party (cloud session + ownership check happen
    cloud-side before this signed request fires).

    Freshness window: ±30s on the timestamp. The cloud signs
    immediately before sending; clock skew on the appliance is
    rarely worse than a few seconds.

    Replay protection isn't strictly needed (the body of what
    flows through is read-only telemetry or write-actions that
    the cloud session already authorised), and a 30s window keeps
    the surface tiny enough that practical replay isn't useful."""
    import base64
    import hashlib
    import hmac
    if not header_value or "." not in header_value:
        return False
    try:
        ts_str, sig_b64 = header_value.split(".", 1)
        ts = int(ts_str)
        pad = lambda s: s + "=" * (-len(s) % 4)
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except (ValueError, TypeError):
        return False
    now = int(time.time())
    if abs(now - ts) > 30:
        return False
    try:
        key = bytes.fromhex(sso_secret_hex)
    except ValueError:
        return False
    expected = hmac.new(key, ts_str.encode("ascii"), hashlib.sha256).digest()
    return hmac.compare_digest(expected, sig)


def consume_sso_token(token: str, sso_secret_hex: str) -> dict | None:
    """Verify a cloud-signed SSO redirect token. Returns the decoded
    payload dict on success, None on any failure (bad signature,
    expired, replayed, malformed).

    Token format: `urlsafe_b64(payload_json)` + `.` + `urlsafe_b64(sig)`.
    Sig is HMAC-SHA256(sso_secret_bytes, payload_json_bytes).

    Replay protection: the `jti` claim is recorded in _SSO_NONCES_SEEN
    until exp + 10s. A second use within that window is rejected even
    if the signature is otherwise valid."""
    import base64
    import hashlib
    import hmac
    import json as _json

    if not token or "." not in token or not sso_secret_hex:
        return None
    try:
        body_b64, sig_b64 = token.split(".", 1)
        # Pad for urlsafe_b64decode (we stripped = on the mint side).
        pad = lambda s: s + "=" * (-len(s) % 4)
        body = base64.urlsafe_b64decode(pad(body_b64))
        sig  = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception:
        return None
    try:
        key = bytes.fromhex(sso_secret_hex)
    except ValueError:
        return None
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = _json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, _json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        return None
    if jti in _SSO_NONCES_SEEN:
        return None  # replay
    _SSO_NONCES_SEEN[jti] = exp + 10
    return payload


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


def ensure_first_boot_password() -> str | None:
    """Generate + persist a random initial password if none is set.

    Called once on daemon startup. Closes the "Docker installs ship
    with no password, so the auth middleware bypasses everything"
    hole — packaging/install.sh did this for the Pi SD image, but
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
    except OSError:
        pass
    log.warning("=" * 64)
    log.warning("FIRST-BOOT: generated initial web password")
    log.warning("")
    log.warning("    %s", plaintext)
    log.warning("")
    log.warning("Save it now — you'll need it to log into the dashboard.")
    log.warning("Rotate via Settings → System → Reset web password (or")
    log.warning("`wattpost-config` on the Pi). Stored at %s",
                PASSWORD_HASH_PATH)
    log.warning("=" * 64)
    return plaintext
