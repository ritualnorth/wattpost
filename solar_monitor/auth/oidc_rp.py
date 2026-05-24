"""OIDC Relying-Party primitives for the appliance (Phase 3, #305).

Lets the appliance offload LAN-side login to the cloud OIDC server:

  * fetch + cache the cloud JWKS (24h refresh)
  * verify EdDSA-signed JWTs (kid lookup → ed25519 verify → exp/nbf)
  * generate PKCE S256 verifier/challenge pairs
  * hold a small in-memory state store (CSRF nonce + PKCE verifier
    survive the redirect round-trip; 5min TTL)
  * POST the auth code to /oidc/token, return parsed tokens

Anti-goals: this module DOES NOT touch the local session store, DOES
NOT mount any HTTP routes. Those live in solar_monitor/api/auth_oidc.py
so this module stays unit-testable with no Litestar dependency.

Verify-side primitives use PyNaCl (already a dep via the keypair seal
module) rather than introducing cryptography on the appliance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from nacl.signing import VerifyKey

log = logging.getLogger(__name__)


# JWKS on-disk cache — used as a fallback when the appliance is
# offline and a session needs validating. Lives next to the keypair
# so the on-disk identity surface is one directory.
_KEY_DIR = Path(os.environ.get("WATTPOST_KEYS_DIR", "/var/lib/wattpost/keys"))
JWKS_CACHE_PATH = _KEY_DIR / "cloud_jwks.json"

# How long an in-memory JWKS cache is honoured before re-fetching.
# Cloud sets cache-control: max-age=86400 on /oidc/jwks; matching
# that keeps us aligned with the cloud's rotation expectations
# (key 'rotating' for ~24h, then promoted).
JWKS_TTL_SECONDS = 60 * 60 * 24

# State store TTL — the redirect round-trip should take <5min in
# normal browsing. Anything older was abandoned + can be cleared.
STATE_TTL_SECONDS = 60 * 5


# ---------------------------------------------------------------- #
#  JWKS fetch + cache                                               #
# ---------------------------------------------------------------- #


@dataclass(slots=True)
class _CachedJwks:
    fetched_at: float
    keys:       list[dict[str, Any]]


_jwks_cache: _CachedJwks | None = None


def _now() -> float:
    return time.time()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _disk_load_jwks() -> list[dict[str, Any]] | None:
    """Last-resort JWKS recovery when the cloud is unreachable. Returns
    None if no cache file exists or it's unparseable."""
    try:
        raw = JWKS_CACHE_PATH.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("oidc_rp: jwks disk-cache read failed (%s)", e)
        return None
    try:
        body = json.loads(raw)
        keys = body.get("keys") or []
        if isinstance(keys, list) and all(isinstance(k, dict) for k in keys):
            return keys
    except json.JSONDecodeError:
        pass
    log.warning("oidc_rp: jwks disk-cache corrupt — ignoring")
    return None


def _disk_save_jwks(keys: list[dict[str, Any]]) -> None:
    try:
        _KEY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = JWKS_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"keys": keys}, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, JWKS_CACHE_PATH)
    except OSError as e:
        log.warning("oidc_rp: jwks disk-cache write failed (%s) — "
                    "in-memory cache will still be used", e)


async def fetch_jwks(jwks_url: str, *, force: bool = False) -> list[dict[str, Any]]:
    """Return the current JWKS keys list. Cached in-memory + on-disk.

    Concurrent callers during the TTL get the cached value (no
    thundering herd). `force=True` bypasses the cache — used by the
    verify path when an unknown kid is encountered (key rotation that
    happened mid-window)."""
    global _jwks_cache
    cache = _jwks_cache
    if not force and cache is not None and (_now() - cache.fetched_at) < JWKS_TTL_SECONDS:
        return cache.keys

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(jwks_url)
            r.raise_for_status()
            body = r.json()
            keys = body.get("keys") or []
            if not isinstance(keys, list):
                raise ValueError("JWKS response missing 'keys' array")
    except Exception as e:
        # Network failure — try the on-disk fallback so verify can
        # still proceed. If THAT's missing too, return [] (callers
        # treat empty key set as "no JWT verifiable").
        log.warning("oidc_rp: JWKS fetch failed (%s) — falling back to disk cache", e)
        disk = _disk_load_jwks()
        return disk if disk is not None else []

    _jwks_cache = _CachedJwks(fetched_at=_now(), keys=keys)
    _disk_save_jwks(keys)
    return keys


def _verify_key_from_jwk(jwk: dict[str, Any]) -> VerifyKey | None:
    """Build a PyNaCl VerifyKey from a JWK. Returns None for unsupported
    key types so the verify loop can skip-and-continue."""
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        return None
    x = jwk.get("x")
    if not x:
        return None
    try:
        return VerifyKey(_b64url_decode(x))
    except Exception:
        return None


# ---------------------------------------------------------------- #
#  JWT verify                                                       #
# ---------------------------------------------------------------- #


class JwtVerifyError(Exception):
    """Any failure during JWT verify. Generic on purpose — callers
    treat as 'unauthenticated' and don't branch on the message."""


async def verify_jwt(
    token:       str,
    *,
    jwks_url:    str,
    expected_iss: str | None = None,
    expected_aud: str | None = None,
    leeway:      int = 60,
) -> dict[str, Any]:
    """Parse + verify a compact-serialised EdDSA JWT against the
    cloud JWKS. Returns the claims dict on success.

    Behaviour:
      * fetches JWKS (in-memory cache, then disk, then network)
      * unknown kid → force a JWKS refresh once before giving up
      * verifies signature via PyNaCl ed25519
      * enforces exp (with `leeway` seconds tolerance for clock skew)
      * optionally checks iss / aud claims if expected_iss/aud given

    Raises JwtVerifyError on any failure (malformed segments, wrong
    kid after refresh, bad signature, expired, etc.)."""
    try:
        h_b64, p_b64, s_b64 = token.split(".")
    except ValueError as e:
        raise JwtVerifyError("malformed compact JWT") from e
    try:
        header = json.loads(_b64url_decode(h_b64))
        claims = json.loads(_b64url_decode(p_b64))
        sig    = _b64url_decode(s_b64)
    except Exception as e:
        raise JwtVerifyError("undecodable JWT segment") from e

    if header.get("alg") != "EdDSA":
        raise JwtVerifyError(f"unexpected alg {header.get('alg')!r}")
    kid = header.get("kid")
    if not kid:
        raise JwtVerifyError("JWT header missing kid")

    keys = await fetch_jwks(jwks_url)
    match = next((k for k in keys if k.get("kid") == kid), None)
    if match is None:
        # Unknown kid — possible mid-rotation, force-refresh once.
        keys = await fetch_jwks(jwks_url, force=True)
        match = next((k for k in keys if k.get("kid") == kid), None)
    if match is None:
        raise JwtVerifyError(f"unknown kid {kid!r}")

    vk = _verify_key_from_jwk(match)
    if vk is None:
        raise JwtVerifyError(f"unsupported JWK shape for kid {kid!r}")
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    try:
        vk.verify(signing_input, sig)
    except Exception as e:
        raise JwtVerifyError("signature verify failed") from e

    now = int(_now())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp + leeway < now:
        raise JwtVerifyError("JWT expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, int) and nbf - leeway > now:
        raise JwtVerifyError("JWT not yet valid")

    if expected_iss is not None and claims.get("iss") != expected_iss:
        raise JwtVerifyError(f"iss mismatch")
    if expected_aud is not None and claims.get("aud") != expected_aud:
        raise JwtVerifyError(f"aud mismatch")

    return claims


# ---------------------------------------------------------------- #
#  PKCE                                                             #
# ---------------------------------------------------------------- #


def new_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) suitable for an /oidc/authorize
    request. Verifier is 43 base64url chars (32 bytes entropy).
    Challenge is the S256 hash."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------- #
#  In-memory state store                                            #
# ---------------------------------------------------------------- #


@dataclass(slots=True)
class _PendingAuth:
    pkce_verifier: str
    nonce:         str
    return_to:     str
    created_at:    float


# state-token → _PendingAuth. Cleared on consume or after TTL.
_pending: dict[str, _PendingAuth] = {}


def stash_pending(*, pkce_verifier: str, nonce: str, return_to: str) -> str:
    """Generate a fresh state token, store the PKCE verifier + nonce
    + post-login return path against it, return the state value."""
    _gc_pending()
    state = secrets.token_urlsafe(24)
    _pending[state] = _PendingAuth(
        pkce_verifier=pkce_verifier,
        nonce=nonce,
        return_to=return_to,
        created_at=_now(),
    )
    return state


def consume_pending(state: str) -> _PendingAuth | None:
    """Look up + remove a pending-auth entry. Returns None if the
    state token is unknown or expired."""
    _gc_pending()
    entry = _pending.pop(state, None)
    if entry is None:
        return None
    if (_now() - entry.created_at) > STATE_TTL_SECONDS:
        return None
    return entry


def _gc_pending() -> None:
    cutoff = _now() - STATE_TTL_SECONDS
    expired = [k for k, v in _pending.items() if v.created_at < cutoff]
    for k in expired:
        _pending.pop(k, None)


# ---------------------------------------------------------------- #
#  Token exchange                                                   #
# ---------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token:  str
    refresh_token: str
    id_token:      str
    expires_in:    int
    scope:         str


async def exchange_code(
    *,
    token_endpoint: str,
    code:           str,
    code_verifier:  str,
    client_id:      str,
    redirect_uri:   str,
) -> TokenSet:
    """POST to /oidc/token with grant_type=authorization_code.
    Returns the parsed TokenSet. Raises on HTTP error or malformed
    response."""
    form = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        r = await client.post(token_endpoint, data=form)
    if r.status_code != 200:
        raise RuntimeError(
            f"/oidc/token exchange failed: HTTP {r.status_code} — "
            f"{r.text[:200]}",
        )
    body = r.json()
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        id_token=body["id_token"],
        expires_in=int(body.get("expires_in", 0)),
        scope=str(body.get("scope", "")),
    )


async def refresh_tokens(
    *,
    token_endpoint: str,
    refresh_token:  str,
    client_id:      str,
) -> TokenSet:
    """grant_type=refresh_token. Returns the rotated TokenSet — the
    refresh token field will be a NEW value; persist it and discard
    the old one (the cloud has marked it rotated; presenting the old
    one again triggers chain revocation)."""
    form = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     client_id,
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        r = await client.post(token_endpoint, data=form)
    if r.status_code != 200:
        raise RuntimeError(
            f"/oidc/token refresh failed: HTTP {r.status_code} — "
            f"{r.text[:200]}",
        )
    body = r.json()
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        id_token=body["id_token"],
        expires_in=int(body.get("expires_in", 0)),
        scope=str(body.get("scope", "")),
    )
