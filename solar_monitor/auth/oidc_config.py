"""On-disk OIDC client config (Identity v2 Phase 3, #305).

Persists the OIDC parameters the cloud hands back during the v2
upgrade response:

    {
      "client_id":            "apl_137_lan",
      "redirect_uri":         "https://garage-stack.wattpost.cloud/auth/callback",
      "jwks_url":             "https://wattpost.cloud/oidc/jwks",
      "discovery_url":        "https://wattpost.cloud/.well-known/openid-configuration",
      "registered_at":        "2026-05-24T10:21:33+00:00"
    }

These are all public values, nothing sealed; plain JSON. Lives next
to the sealed keypair under /var/lib/wattpost/keys/ so the on-disk
identity surface is one directory.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_KEY_DIR = Path(os.environ.get("WATTPOST_KEYS_DIR", "/var/lib/wattpost/keys"))
OIDC_CONFIG_PATH = _KEY_DIR / "oidc_client.json"


@dataclass(frozen=True, slots=True)
class OidcConfig:
    client_id:     str
    redirect_uri:  str
    jwks_url:      str
    discovery_url: str
    registered_at: str   # ISO 8601


def load() -> OidcConfig | None:
    """Return the saved OIDC config or None if the appliance hasn't
    completed v2 upgrade yet."""
    try:
        raw = OIDC_CONFIG_PATH.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("oidc_config: read failed (%s), treating as missing", e)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("oidc_config: %s corrupt JSON, ignoring (%s)",
                    OIDC_CONFIG_PATH, e)
        return None
    try:
        return OidcConfig(
            client_id=data["client_id"],
            redirect_uri=data["redirect_uri"],
            jwks_url=data["jwks_url"],
            discovery_url=data["discovery_url"],
            registered_at=data["registered_at"],
        )
    except KeyError as e:
        log.warning("oidc_config: %s missing key %s, ignoring", OIDC_CONFIG_PATH, e)
        return None


def save(
    *,
    client_id:     str,
    redirect_uri:  str,
    jwks_url:      str,
    discovery_url: str,
) -> OidcConfig:
    """Atomically persist the cloud-returned OIDC parameters.

    Atomic write (tmp + rename) so a power loss mid-write can't
    leave a half-corrupt file the next boot can't parse."""
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    cfg = OidcConfig(
        client_id=client_id,
        redirect_uri=redirect_uri,
        jwks_url=jwks_url,
        discovery_url=discovery_url,
        registered_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = json.dumps({
        "client_id":     cfg.client_id,
        "redirect_uri":  cfg.redirect_uri,
        "jwks_url":      cfg.jwks_url,
        "discovery_url": cfg.discovery_url,
        "registered_at": cfg.registered_at,
    }, indent=2)
    tmp = OIDC_CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OIDC_CONFIG_PATH)
    log.info("oidc_config: persisted client_id=%s redirect_uri=%s",
             client_id, redirect_uri)
    return cfg


def save_from_cloud(
    *,
    endpoint:      str,
    client_id:     str,
    redirect_uri:  str,
    jwks_url:      str,
    discovery_url: str,
) -> OidcConfig | None:
    """Persist OIDC parameters taken from a cloud reply, but only if every URL
    is https (http for loopback in development) AND on the host of the cloud
    endpoint the user configured. These URLs become the trust anchor for
    signed commands, so a reply must not be able to move them elsewhere.

    On any violation the previously saved config (if any) is left untouched,
    a warning is logged, and None is returned. All-or-nothing, so the saved
    set never mixes old and new values."""
    from ..urlcheck import same_cloud_host
    bad = [name for name, url in (("redirect_uri", redirect_uri),
                                  ("jwks_url", jwks_url),
                                  ("discovery_url", discovery_url))
           if not isinstance(url, str) or not same_cloud_host(url, endpoint)]
    if bad or not isinstance(client_id, str) or not client_id:
        log.warning("oidc_config: refusing cloud-supplied %s: not https on the "
                    "configured cloud host (%s); keeping the previous config",
                    ", ".join(bad) or "client_id", endpoint)
        return None
    return save(client_id=client_id, redirect_uri=redirect_uri,
                jwks_url=jwks_url, discovery_url=discovery_url)
