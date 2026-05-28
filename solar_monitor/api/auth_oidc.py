"""LAN OIDC login routes (Identity v2 Phase 3, #305).

Two endpoints:

  GET /auth/lan/login
      Initiates the OIDC redirect. Generates a fresh PKCE pair +
      state token + nonce, stashes them in the in-memory state
      store, 302s the user to cloud's /oidc/authorize.

  GET /auth/callback?code=...&state=...
      Completes the flow. Looks up the state, exchanges the code
      for tokens (PKCE verifier proves we initiated it), verifies
      the id_token signature, issues a local session cookie,
      302s back to the original return_to URL.

Both endpoints are no-ops (404) if the appliance hasn't received
its OIDC client config from the cloud yet (pre-v2 or upgrade in
flight). This makes the feature feature-flagged-by-presence: as
soon as identity_v2 upgrade succeeds and oidc_client.json is
written, OIDC login becomes available; until then it doesn't.

Existing password login keeps working in parallel, OIDC is
additive in Phase 3. Phase 4 adds an offline fallback for
no-WAN scenarios; Phase 5 layers WebAuthn on top.
"""
from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from litestar import Request, Response, get
from litestar.exceptions import HTTPException
from litestar.response import Redirect
from typing import Any

from ..auth import oidc_config, oidc_rp

log = logging.getLogger(__name__)


def _safe_return_to(raw: str | None) -> str:
    """Validate the ?next= / return_to param. Only allow same-origin
    relative paths to defend against open-redirect."""
    if not raw or not isinstance(raw, str):
        return "/"
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


@get("/api/system/oidc-available", sync_to_thread=False)
async def oidc_available() -> dict[str, Any]:
    """Lightweight status the login page polls to decide whether to
    show the "Sign in with WattPost cloud" button. Returns {ok: bool,
    available: bool}. Anonymous-readable; reveals no PII (just whether
    the appliance has completed v2 upgrade + got an OIDC client)."""
    cfg = oidc_config.load()
    return {"ok": True, "available": cfg is not None}


@get("/auth/lan/login", sync_to_thread=False)
async def auth_lan_login(request: Request) -> Redirect:
    """Initiate the OIDC redirect to wattpost.cloud."""
    cfg = oidc_config.load()
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail="OIDC login not configured (appliance hasn't completed v2 upgrade)",
        )
    return_to = _safe_return_to(request.query_params.get("next"))
    verifier, challenge = oidc_rp.new_pkce_pair()
    nonce = secrets.token_urlsafe(16)
    state = oidc_rp.stash_pending(
        pkce_verifier=verifier, nonce=nonce, return_to=return_to,
    )
    # Discovery doc URL ends with /.well-known/openid-configuration
    # → /oidc/authorize is at the issuer root. The issuer URL is the
    # discovery URL minus the well-known suffix.
    issuer = cfg.discovery_url.rsplit("/.well-known/", 1)[0]
    qs = urlencode({
        "client_id":             cfg.client_id,
        "response_type":         "code",
        "scope":                 "dashboard:read dashboard:write appliance:admin",
        "redirect_uri":          cfg.redirect_uri,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "state":                 state,
        "nonce":                 nonce,
    })
    target = f"{issuer}/oidc/authorize?{qs}"
    log.info("auth_oidc: initiating OIDC flow → %s", target[:80])
    return Redirect(path=target, status_code=302)


@get("/auth/callback", sync_to_thread=False)
async def auth_callback(request: Request) -> Response:
    """Complete the OIDC redirect. Exchange code for tokens, verify,
    issue a local session, redirect to original return_to."""
    cfg = oidc_config.load()
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail="OIDC login not configured",
        )

    q = request.query_params
    code  = q.get("code")
    state = q.get("state")
    error = q.get("error")

    if error:
        # User declined consent / cloud refused. Show a generic page.
        log.warning("auth_oidc: cloud returned error=%s", error)
        raise HTTPException(
            status_code=400,
            detail=f"OIDC error from cloud: {error}",
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    pending = oidc_rp.consume_pending(state)
    if pending is None:
        # state token unknown (CSRF mismatch, replay from history, or
        # expired). Don't dead-end the user in a JSON 400, bounce
        # back to /login with a banner. They click again, the round-
        # trip works. The 400 was a real bug seen on retry after a
        # v0.1.96 pull; state was wiped by the container recreate.
        # Phase 3-followup: state store is now disk-persisted, so this
        # branch should be much rarer (only true CSRF / replay).
        log.info("auth_oidc: state %s... unknown/expired, bouncing to /login", state[:8])
        return Redirect(path="/login?reauth=expired", status_code=302)

    issuer = cfg.discovery_url.rsplit("/.well-known/", 1)[0]
    try:
        tokens = await oidc_rp.exchange_code(
            token_endpoint=f"{issuer}/oidc/token",
            code=code,
            code_verifier=pending.pkce_verifier,
            client_id=cfg.client_id,
            redirect_uri=cfg.redirect_uri,
        )
    except Exception as e:
        log.exception("auth_oidc: token exchange failed")
        raise HTTPException(
            status_code=502,
            detail=f"OIDC token exchange failed: {e}",
        )

    # Verify the id_token signature + claims. We don't actually need
    # the access_token to validate (it's for cloud API calls, not the
    # local session) but verifying the id_token confirms the cloud
    # signed what we just exchanged for.
    try:
        claims = await oidc_rp.verify_jwt(
            tokens.id_token,
            jwks_url=cfg.jwks_url,
            expected_iss=issuer,
            expected_aud=cfg.client_id,
        )
    except oidc_rp.JwtVerifyError as e:
        log.warning("auth_oidc: id_token verify failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="OIDC id_token failed verification",
        )

    # Issue local session. We stash the OIDC claims on the session
    # record so future requests can know the sub / scope / acr.
    from .. import web_auth as _wa
    token = _wa.issue_session(origin="oidc")
    log.info(
        "auth_oidc: issued local session for sub=%s acr=%s scope=%s",
        claims.get("sub"), claims.get("acr"), tokens.scope,
    )

    resp = Redirect(path=pending.return_to, status_code=302)
    resp.set_cookie(
        key=_wa.SESSION_COOKIE_NAME,
        value=token,
        max_age=_wa.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # appliance is HTTP on LAN
    )
    return resp
